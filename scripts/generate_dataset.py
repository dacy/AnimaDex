#!/usr/bin/env python3
"""
Generate an image dataset with the Anima model via ComfyUI.

Two modes:

  character (default) -- for every row in danbooru_character.csv, runs the
    AnimaGen.json workflow with the prompt placeholder ("tags here")
    replaced by the character's trigger + core_tags (explicit / NSFW tags
    are filtered out). Images -> Outputs/.

  artist -- for every row in danbooru_artist.csv, renders one generic SFW
    1girl image in that artist's style. The artist tag is prefixed with
    '@' (required -- without it the style effect is very weak) and a
    fixed seed is used, so the artist is the only variable between
    renders. Images -> ArtistOutputs/.

Throughput: several worker threads each run the full submit -> wait ->
download -> save cycle. ComfyUI processes its queue serially on the GPU,
so keeping a few prompts queued means the next one starts the instant the
current finishes -- the GPU never idles between images. Tune with
--workers (the default keeps ~3 prompts in flight).

The process is interruptible (Ctrl+C, finishes in-flight images first)
and resumable: on restart it skips any row whose output already exists.

Only the Python standard library is used -- no pip install required.

Usage (run from E:\\AnimaCharDb):
    python generate_dataset.py
    python generate_dataset.py --mode artist
    python generate_dataset.py --workers 4
    python generate_dataset.py --comfyui-url https://abc123-8188.proxy.runpod.net/
"""

import argparse
import csv
import hashlib
import json
import os
import queue
import random
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# The workflow ships with this literal text inside the positive prompt;
# the user may also brace it as "{tags here}". Either form is replaced.
PLACEHOLDERS = ("{tags here}", "tags here")

# Explicit / NSFW vocabulary stripped from character prompts to keep the
# generated dataset reasonably safe-for-work. A core_tags entry is dropped
# if any of its words appears here (whole-word match, so e.g. "unisex" is
# left alone). Extend this set with any other tags you want excluded.
EXCLUDED_WORDS = frozenset({
    # genitalia & explicit anatomy
    "penis", "penises", "pussy", "vagina", "vaginal", "clitoris", "clit",
    "anus", "anal", "testicles", "testicle", "nipple", "nipples",
    "areola", "areolae", "pubic", "foreskin", "scrotum", "labia",
    "cameltoe", "perineum",
    # nudity
    "nude", "naked", "topless", "bottomless",
    # sex acts
    "sex", "fellatio", "cunnilingus", "blowjob", "handjob", "footjob",
    "paizuri", "masturbation", "masturbating", "intercourse",
    "penetration", "penetrated", "deepthroat", "irrumatio", "fingering",
    "insertion", "groping", "humping",
    # fluids & explicit states
    "cum", "cumdrip", "ejaculation", "ejaculating", "semen", "precum",
    "squirting", "creampie", "bukkake", "orgasm", "erection",
    # explicit / fetish content
    "futanari", "dildo", "vibrator", "ahegao", "gangbang", "threesome",
    "orgy", "rape", "bestiality", "uncensored",
})

# Characters that are illegal in Windows filenames.
ILLEGAL_FS_CHARS = '<>:"/\\|?*'

# Per-mode default CSV and output folder.
MODE_DEFAULTS = {
    "character": ("danbooru_character.csv", "Outputs"),
    "artist":    ("danbooru_artist.csv",    "ArtistOutputs"),
}

# Fixed, generic, SFW subject for artist renders. Because the prompt is
# identical for every artist, the artist's *style* is the only variable.
ARTIST_SUBJECT = ("1girl, solo, brown hair, long hair, wavy hair, "
                  "green eyes, white blouse, blue pleated skirt, light smile, "
                  "standing, park, day, cowboy shot, straight-on, arms at sides")
ARTIST_SEED = 42

_LOG_LOCK = threading.Lock()

# TLS context for ComfyUI requests. None means normal certificate
# verification; --insecure swaps in an unverified context.
SSL_CONTEXT = None

# Headers sent on every ComfyUI request. A browser-like User-Agent avoids
# 403s from proxies/CDNs (e.g. RunPod's) that reject the default urllib
# agent. Cookies / auth tokens can be added with --header.
HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
}


def log(msg):
    """Thread-safe stdout logging."""
    with _LOG_LOCK:
        print(msg, flush=True)


def sanitize_filename(name):
    """Make `name` safe to use as a Windows filename without losing identity."""
    cleaned = "".join("_" if c in ILLEGAL_FS_CHARS else c for c in name)
    # Windows forbids trailing dots/spaces on filenames.
    cleaned = cleaned.rstrip(" .")
    return cleaned or "unnamed"


def artist_prompt(trigger):
    """Positive prompt for an artist render.

    The artist tag MUST be prefixed with '@' -- without it the model's
    style effect is very weak.
    """
    return (f"@{trigger}, "
            f"{ARTIST_SUBJECT}")


# --------------------------------------------------------------------------
# Workflow inspection
# --------------------------------------------------------------------------

def find_prompt_node(workflow):
    """Return the node id whose `text` input holds the tag placeholder."""
    for node_id, node in workflow.items():
        text = node.get("inputs", {}).get("text")
        if isinstance(text, str) and any(p in text for p in PLACEHOLDERS):
            return node_id
    return None


def find_seed_nodes(workflow):
    """Return ids of every node that has an integer `seed` input."""
    return [nid for nid, n in workflow.items()
            if isinstance(n.get("inputs", {}).get("seed"), int)]


def inject_tags(text, tags):
    """Replace the first placeholder found in `text` with `tags`."""
    for p in PLACEHOLDERS:
        if p in text:
            return text.replace(p, tags, 1)
    return text


def sanitize_tags(tag_string):
    """Drop explicit / NSFW tags from a comma-separated tag string.

    A tag is removed when any of its words is in EXCLUDED_WORDS.
    """
    kept = []
    for tag in tag_string.split(","):
        tag = tag.strip()
        if not tag:
            continue
        words = set(re.findall(r"[a-z]+", tag.lower()))
        if words.isdisjoint(EXCLUDED_WORDS):
            kept.append(tag)
    return ", ".join(kept)


# --------------------------------------------------------------------------
# ComfyUI HTTP API (stdlib only)
# --------------------------------------------------------------------------

def http_json(url, payload=None, timeout=30):
    """GET (payload=None) or POST a JSON request and return the parsed JSON."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = dict(HTTP_HEADERS)
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()
        if len(body) > 600:
            body = body[:600] + " ..."
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from None


def queue_prompt(base_url, workflow, client_id):
    """Submit a workflow; return its prompt_id (raises on validation errors)."""
    resp = http_json(f"{base_url}/prompt",
                     {"prompt": workflow, "client_id": client_id})
    if resp.get("node_errors"):
        raise RuntimeError(f"workflow validation failed: {resp['node_errors']}")
    prompt_id = resp.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"no prompt_id in response: {resp}")
    return prompt_id


def fetch_image(base_url, image, timeout=120):
    """Download a single image described by a ComfyUI history `images` entry."""
    qs = urllib.parse.urlencode({
        "filename": image["filename"],
        "subfolder": image.get("subfolder", ""),
        "type": image.get("type", "temp"),
    })
    req = urllib.request.Request(f"{base_url}/view?{qs}",
                                 headers=dict(HTTP_HEADERS))
    with urllib.request.urlopen(req, timeout=timeout,
                                context=SSL_CONTEXT) as resp:
        return resp.read()


def wait_for_images(base_url, prompt_id, poll_interval, timeout):
    """Poll /history until the prompt finishes; return its list of images."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = http_json(f"{base_url}/history/{prompt_id}")
        entry = history.get(prompt_id)
        if entry:  # /history only lists prompts that have finished.
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI execution error: "
                                   f"{status.get('messages')}")
            for node_output in entry.get("outputs", {}).values():
                if node_output.get("images"):
                    return node_output["images"]
            raise RuntimeError(f"workflow produced no image (status={status})")
        time.sleep(poll_interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for prompt "
                       f"{prompt_id}")


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def seed_for(key, mode, args):
    if args.seed is not None:
        return args.seed
    if args.random_seed:
        return random.randint(0, 2 ** 48 - 1)
    if mode == "artist":
        return ARTIST_SEED          # same seed for every artist
    # Deterministic: same trigger always yields the same seed, so a re-run
    # of a previously failed character reproduces the same image.
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def build_workflow(template, prompt_node, seed_nodes, prompt_text, seed):
    """Return a fresh copy of the workflow with the prompt + seed applied."""
    wf = json.loads(json.dumps(template))  # deep copy
    wf[prompt_node]["inputs"]["text"] = prompt_text
    for nid in seed_nodes:
        wf[nid]["inputs"]["seed"] = seed
    return wf


def generate_one(base_url, client_id, workflow, out_path, args):
    """Run one workflow and save its first image atomically to out_path."""
    prompt_id = queue_prompt(base_url, workflow, client_id)
    images = wait_for_images(base_url, prompt_id,
                             args.poll_interval, args.timeout)
    data = fetch_image(base_url, images[0])
    tmp_path = out_path + ".part"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, out_path)  # atomic: a partial file is never "done"


class Counters:
    """Thread-safe run tallies."""

    def __init__(self):
        self._lock = threading.Lock()
        self.generated = 0
        self.skipped = 0
        self.failed = 0

    def bump(self, field):
        with self._lock:
            value = getattr(self, field) + 1
            setattr(self, field, value)
            return value


def worker(jobs, ctx, counters, stop_event):
    """Pull rows off the shared queue and render each until it drains.

    Each worker independently submits to ComfyUI and waits for its result.
    With several workers, ComfyUI's queue always holds the next prompt, so
    its GPU starts it the instant the current render finishes.
    """
    args = ctx["args"]
    client_id = str(uuid.uuid4())

    while not stop_event.is_set():
        try:
            index, row = jobs.get_nowait()
        except queue.Empty:
            return

        trigger = (row.get("trigger") or "").strip()
        if not trigger:
            counters.bump("skipped")
            continue

        out_path = os.path.join(ctx["output_dir"],
                                sanitize_filename(trigger) + ".png")
        if os.path.exists(out_path):
            counters.bump("skipped")
            continue

        if ctx["mode"] == "artist":
            prompt_text = artist_prompt(trigger)
        else:
            core_tags = (row.get("core_tags") or "").strip()
            tags = f"{trigger}, {core_tags}" if core_tags else trigger
            prompt_text = inject_tags(ctx["template_text"],
                                      sanitize_tags(tags))
        seed = seed_for(trigger, ctx["mode"], args)
        workflow = build_workflow(ctx["template"], ctx["prompt_node"],
                                  ctx["seed_nodes"], prompt_text, seed)

        attempt = 0
        while not stop_event.is_set():
            attempt += 1
            t0 = time.time()
            try:
                generate_one(ctx["base_url"], client_id, workflow,
                             out_path, args)
                done = counters.bump("generated")
                log(f"[{index}/{ctx['total']}] OK   {trigger}  "
                    f"(seed={seed}, {time.time() - t0:.1f}s, total {done})")
                if args.limit and done >= args.limit:
                    stop_event.set()
                break
            except Exception as e:                          # noqa: BLE001
                if attempt <= args.retries and not stop_event.is_set():
                    log(f"[{index}/{ctx['total']}] retry {attempt}/"
                        f"{args.retries} for {trigger}: {e}")
                    time.sleep(2)
                    continue
                counters.bump("failed")
                log(f"[{index}/{ctx['total']}] FAIL {trigger}: {e}")
                with ctx["fail_lock"]:
                    with open(ctx["failures_path"], "a",
                              encoding="utf-8") as fl:
                        fl.write(f"{trigger}\t{e}\n")
                break


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Generate an Anima image dataset via ComfyUI.")
    p.add_argument("--mode", choices=("character", "artist"),
                   default="character",
                   help="Dataset to generate (default: %(default)s)")
    p.add_argument("--csv", default=None,
                   help="Source CSV (default: per --mode)")
    p.add_argument("--workflow", default="AnimaGen.json",
                   help="ComfyUI API workflow JSON (default: %(default)s)")
    p.add_argument("--output-dir", default=None,
                   help="Folder for generated images (default: per --mode)")
    p.add_argument("--comfyui-url",
                   default=os.environ.get("COMFYUI_URL",
                                          "http://127.0.0.1:8188/"),
                   help="ComfyUI server URL -- local or a remote endpoint "
                        "such as a RunPod proxy URL. Also settable via the "
                        "COMFYUI_URL environment variable "
                        "(default: %(default)s)")
    p.add_argument("--insecure", action="store_true",
                   help="Skip TLS certificate verification for https:// "
                        "endpoints.")
    p.add_argument("--header", action="append", metavar="KEY:VALUE",
                   help="Extra HTTP header sent with every request "
                        "(repeatable). Useful for an authenticated proxy, "
                        'e.g. --header "Cookie: <copied from your browser>"')
    p.add_argument("--workers", type=int, default=3,
                   help="Concurrent worker threads keeping ComfyUI's queue "
                        "primed so the GPU never idles (default: %(default)s)")
    p.add_argument("--limit", type=int, default=0,
                   help="Generate roughly N new images then stop "
                        "(0 = no limit). Handy for a test run.")
    p.add_argument("--seed", type=int, default=None,
                   help="Force this seed for every image.")
    p.add_argument("--random-seed", action="store_true",
                   help="Use a fresh random seed for every image.")
    p.add_argument("--poll-interval", type=float, default=2.0,
                   help="Seconds between history polls (default: %(default)s)")
    p.add_argument("--timeout", type=float, default=900.0,
                   help="Seconds to wait for one image (default: %(default)s)")
    p.add_argument("--retries", type=int, default=2,
                   help="Retry attempts per row on failure "
                        "(default: %(default)s)")
    # Single-image regeneration -- the webapp's dev 'Regenerate' tool
    # invokes this form instead of running the full dataset.
    p.add_argument("--regen", action="store_true",
                   help="Regenerate one image from --tags into --out, then "
                        "exit (instead of the full dataset run).")
    p.add_argument("--tags", default=None,
                   help="[--regen] Prompt tags to inject into the workflow.")
    p.add_argument("--out", default=None,
                   help="[--regen] Destination PNG path (overwritten).")
    return p.parse_args()


def apply_connection_args(args):
    """Apply --insecure / --header to the module-level HTTP settings."""
    if args.insecure:
        global SSL_CONTEXT
        SSL_CONTEXT = ssl.create_default_context()
        SSL_CONTEXT.check_hostname = False
        SSL_CONTEXT.verify_mode = ssl.CERT_NONE
    for raw in (args.header or []):
        name, sep, value = raw.partition(":")
        if not sep:
            sys.exit(f"--header must be 'Key: Value' -- got: {raw}")
        HTTP_HEADERS[name.strip()] = value.strip()


def regen_main(args):
    """Single-image regeneration for the webapp's dev 'Regenerate' tool.

    Builds the workflow with --tags and a fresh random seed (unless --seed
    is given), generates one image, and saves it to --out.
    """
    if not args.out:
        sys.exit("--regen requires --out")
    if not (args.tags and args.tags.strip()):
        sys.exit("--regen requires --tags")
    if not os.path.isfile(args.workflow):
        sys.exit(f"Workflow not found: {args.workflow}")

    base_url = args.comfyui_url.rstrip("/")
    apply_connection_args(args)

    with open(args.workflow, encoding="utf-8") as f:
        template = json.load(f)
    prompt_node = find_prompt_node(template)
    if not prompt_node:
        sys.exit("Could not find the tag placeholder in the workflow.")
    seed_nodes = find_seed_nodes(template)
    template_text = template[prompt_node]["inputs"]["text"]

    prompt_text = inject_tags(template_text,
                              sanitize_tags(args.tags.strip()))
    seed = (args.seed if args.seed is not None
            else random.randint(0, 2 ** 48 - 1))
    workflow = build_workflow(template, prompt_node, seed_nodes,
                              prompt_text, seed)

    try:
        http_json(f"{base_url}/system_stats", timeout=15)
    except Exception as e:                              # noqa: BLE001
        sys.exit(f"Cannot reach ComfyUI at {base_url}: {e}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    try:
        generate_one(base_url, str(uuid.uuid4()), workflow, args.out, args)
    except Exception as e:                              # noqa: BLE001
        sys.exit(f"Generation failed: {e}")
    log(f"Regenerated -> {args.out}  (seed={seed})")


def main():
    args = parse_args()
    if args.regen:
        regen_main(args)
        return
    base_url = args.comfyui_url.rstrip("/")
    mode = args.mode
    default_csv, default_out = MODE_DEFAULTS[mode]
    csv_path = args.csv or default_csv
    output_dir = args.output_dir or default_out
    workers = max(1, args.workers)

    if not os.path.isfile(csv_path):
        sys.exit(f"CSV not found: {csv_path}")
    if not os.path.isfile(args.workflow):
        sys.exit(f"Workflow not found: {args.workflow}")
    os.makedirs(output_dir, exist_ok=True)

    with open(args.workflow, encoding="utf-8") as f:
        template = json.load(f)
    prompt_node = find_prompt_node(template)
    if not prompt_node:
        sys.exit("Could not find the tag placeholder ('tags here') in the "
                 "workflow's prompt nodes.")
    seed_nodes = find_seed_nodes(template)
    template_text = template[prompt_node]["inputs"]["text"]
    log(f"Mode: {mode}  |  prompt node = {prompt_node}, "
        f"seed node(s) = {seed_nodes or 'none'}")
    if mode == "character":
        log(f"Explicit-tag filter active: {len(EXCLUDED_WORDS)} NSFW "
            f"words excluded from prompts.")

    # Apply --insecure / --header to the HTTP settings.
    apply_connection_args(args)

    # Verify ComfyUI is reachable before doing any work.
    try:
        http_json(f"{base_url}/system_stats", timeout=15)
    except Exception as e:
        msg = f"Cannot reach ComfyUI at {base_url}\n  {e}"
        if "403" in str(e) or "401" in str(e):
            msg += ("\n\nThe endpoint refused the request. If the URL works "
                    "in your browser, the proxy is checking headers or "
                    "cookies. Open the URL in your browser, copy the "
                    "'Cookie' request header from dev tools (Network tab), "
                    'and pass it with:  --header "Cookie: <value>"')
        sys.exit(msg)
    log(f"Connected to ComfyUI at {base_url}"
        + ("  (TLS verification off)" if args.insecure else ""))

    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    total = len(rows)

    # Fill the shared work queue (workers pull rows from it).
    jobs = queue.Queue()
    for i, row in enumerate(rows, 1):
        jobs.put((i, row))

    ctx = {
        "args": args, "base_url": base_url, "mode": mode,
        "output_dir": output_dir, "template": template,
        "prompt_node": prompt_node, "seed_nodes": seed_nodes,
        "template_text": template_text, "total": total,
        "failures_path": os.path.join(output_dir, "_failures.log"),
        "fail_lock": threading.Lock(),
    }
    counters = Counters()
    stop_event = threading.Event()

    log(f"Loaded {total} {mode}s from {csv_path}")
    log(f"Running {workers} worker(s) -- ComfyUI's queue stays primed so "
        f"the GPU never idles between images.\n")

    start = time.time()
    threads = [threading.Thread(target=worker,
                                args=(jobs, ctx, counters, stop_event),
                                name=f"worker-{w + 1}", daemon=True)
               for w in range(workers)]
    for t in threads:
        t.start()

    try:
        # Join with a timeout so Ctrl+C reaches the main thread promptly.
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.3)
    except KeyboardInterrupt:
        log("\nInterrupted -- finishing in-flight images, then stopping...")
        stop_event.set()
        for t in threads:
            t.join()

    elapsed = time.time() - start
    log(f"\nDone. generated={counters.generated}  "
        f"skipped(existing)={counters.skipped}  "
        f"failed={counters.failed}  elapsed={elapsed:.0f}s")
    if counters.generated:
        per = elapsed / counters.generated
        log(f"Throughput: {counters.generated / max(elapsed, 1) * 60:.1f} "
            f"images/min  ({per:.1f}s per image, {workers} workers)")
    if counters.failed:
        log(f"Failures logged to: {ctx['failures_path']}")


if __name__ == "__main__":
    main()
