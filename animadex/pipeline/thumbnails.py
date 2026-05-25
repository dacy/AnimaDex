"""Thumbnail builder. Single-file and bulk modes; the orchestrator
uses single-file (no pool overhead for one image) while the standalone
CLI uses a ProcessPoolExecutor for batched rebuilds."""

from __future__ import annotations

import csv
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

from .. import db
from ..config import Config
from ..images import full_path, thumb_path

COLLAGE_W, COLLAGE_H = 297, 445  # matches a character thumbnail


def build_one_file(src: str, dst: str, *, height: int,
                   quality: int) -> None:
    """Resize one PNG to a proportionally scaled WebP at `dst`. Uses a
    `.tmp` write + rename so a partial result never overwrites a good
    thumb on crash."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with Image.open(src) as im:
        im = im.convert('RGB')
        w, h = im.size
        new_w = max(1, round(height * w / h))
        im = im.resize((new_w, height), Image.Resampling.LANCZOS)
        tmp = dst + '.tmp'
        im.save(tmp, 'WEBP', quality=quality, method=6)
    os.replace(tmp, dst)


def build_one(cfg: Config, mode: str, imgname: str,
              thumbname: str) -> bool:
    """High-level: build the thumb for one (imgname, thumbname) pair,
    using paths from `cfg`. Returns True if it built (or already
    exists), False if the source full image is missing."""
    src = full_path(cfg, mode, imgname)
    dst = thumb_path(cfg, mode, thumbname)
    if not os.path.isfile(src):
        return False
    if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return True
    build_one_file(src, dst,
                   height=cfg.gallery.thumb_height,
                   quality=cfg.gallery.thumb_quality)
    return True


# --- bulk ---------------------------------------------------------------

def _make_worker(job):
    src, dst, height, quality = job
    try:
        build_one_file(src, dst, height=height, quality=quality)
        return True, os.path.basename(src), None
    except OSError as e:
        return False, os.path.basename(src), str(e)


def build_all_for_mode(cfg: Config, mode: str) -> int:
    """Process every full image in cfg's `<mode>_dir/images` that does
    not yet have a thumb. Returns count built."""
    if mode not in ('characters', 'artists'):
        raise ValueError(f'unknown mode {mode!r}')
    images_dir = os.path.join(
        cfg.paths.characters_dir if mode == 'characters'
        else cfg.paths.artists_dir, 'images')
    thumbs_dir = os.path.join(
        cfg.paths.characters_dir if mode == 'characters'
        else cfg.paths.artists_dir, 'thumbs')
    if not os.path.isdir(images_dir):
        print(f'  [{mode}] images dir missing: {images_dir}')
        return 0
    os.makedirs(thumbs_dir, exist_ok=True)
    jobs = []
    with os.scandir(images_dir) as it:
        for entry in it:
            if not (entry.is_file()
                    and entry.name.lower().endswith('.png')):
                continue
            dst = os.path.join(thumbs_dir,
                               os.path.splitext(entry.name)[0] + '.webp')
            if not os.path.exists(dst):
                jobs.append((entry.path, dst,
                             cfg.gallery.thumb_height,
                             cfg.gallery.thumb_quality))
    if not jobs:
        return 0
    return _run_pool(jobs, _make_worker)


def _run_pool(jobs, fn) -> int:
    workers = min(8, os.cpu_count() or 2)
    done = failed = 0
    start = time.time()
    print(f'  building {len(jobs)} thumbnails with {workers} workers',
          flush=True)
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for ok, name, err in pool.map(fn, jobs, chunksize=16):
                if ok:
                    done += 1
                else:
                    failed += 1
                    print(f'  FAIL {name}: {err}', flush=True)
                n = done + failed
                if n % 200 == 0 or n == len(jobs):
                    rate = n / max(time.time() - start, 0.001)
                    print(f'  {n}/{len(jobs)}  ({rate:.0f}/s)', flush=True)
    except KeyboardInterrupt:
        print('\n  interrupted -- re-run to resume.')
    print(f'  thumbs built={done} failed={failed} '
          f'elapsed={time.time() - start:.0f}s')
    return done


# --- copyright collages ------------------------------------------------

def _crop_profile(im):
    w, h = im.size
    return im.crop((round(w * 0.25), 0, round(w * 0.75), round(h * 0.5)))


def _crop_wide(im):
    w, h = im.size
    return im.crop((0, 0, w, round(h * 0.5)))


def make_collage(sources: list[str], dst: str, *, quality: int) -> None:
    R = Image.Resampling.LANCZOS
    W, H = COLLAGE_W, COLLAGE_H
    mx, my = W // 2, H // 2
    canvas = Image.new('RGB', (W, H), (14, 14, 26))
    imgs = [Image.open(p).convert('RGB') for p in sources]
    n = len(imgs)
    try:
        if n >= 4:
            canvas.paste(_crop_profile(imgs[0]).resize((mx, my), R),
                         (0, 0))
            canvas.paste(_crop_profile(imgs[1]).resize((W - mx, my), R),
                         (mx, 0))
            canvas.paste(_crop_profile(imgs[2]).resize((mx, H - my), R),
                         (0, my))
            canvas.paste(_crop_profile(imgs[3]).resize(
                (W - mx, H - my), R), (mx, my))
        elif n == 3:
            canvas.paste(_crop_profile(imgs[0]).resize((mx, my), R),
                         (0, 0))
            canvas.paste(_crop_profile(imgs[1]).resize((W - mx, my), R),
                         (mx, 0))
            canvas.paste(_crop_wide(imgs[2]).resize((W, H - my), R),
                         (0, my))
        elif n == 2:
            canvas.paste(_crop_wide(imgs[0]).resize((W, my), R), (0, 0))
            canvas.paste(_crop_wide(imgs[1]).resize((W, H - my), R),
                         (0, my))
        elif n == 1:
            canvas.paste(imgs[0].resize((W, H), R), (0, 0))
    finally:
        for im in imgs:
            im.close()
    tmp = dst + '.tmp'
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    canvas.save(tmp, 'WEBP', quality=quality, method=6)
    os.replace(tmp, dst)


def build_copyright_collages(cfg: Config, csv_path: str | Path) -> int:
    """Build one collage per copyright that doesn't yet have one. Pulls
    the top-4 character thumbnails per series from the CSV."""
    char_thumbs = os.path.join(cfg.paths.characters_dir, 'thumbs')
    dst_dir = os.path.join(cfg.paths.copyrights_dir, 'thumbs')
    os.makedirs(dst_dir, exist_ok=True)
    if not os.path.isfile(csv_path) or not os.path.isdir(char_thumbs):
        return 0

    groups: dict[str, list[tuple[int, str]]] = {}
    with open(csv_path, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            cp = (row.get('copyright') or '').strip()
            trigger = (row.get('trigger') or '').strip()
            if not cp or not trigger:
                continue
            thumb = os.path.join(char_thumbs,
                                 db.sanitize_filename(trigger) + '.webp')
            if not os.path.exists(thumb):
                continue
            try:
                count = int(row.get('count') or 0)
            except ValueError:
                count = 0
            groups.setdefault(cp, []).append((count, thumb))

    built = 0
    for cp, members in groups.items():
        dst = os.path.join(dst_dir,
                           db.sanitize_filename(cp) + '.webp')
        if os.path.exists(dst):
            continue
        members.sort(key=lambda m: -m[0])
        try:
            make_collage([m[1] for m in members[:4]], dst,
                          quality=cfg.gallery.thumb_quality)
            built += 1
        except (OSError, ValueError) as e:
            print(f'  FAIL collage {cp}: {e}', flush=True)
    return built
