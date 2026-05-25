# Configuration

AnimaDex reads one file: `config.toml` in the repo root. It is not
shipped (gitignored); `config.toml.example` is the committed template.

Any value can be overridden by an environment variable named
`ANIMADEX_<SECTION>_<KEY>` (upper-case). Example:

```bash
ANIMADEX_SERVER_PORT=8080 ANIMADEX_ADMIN_PASSWORD=hunter2 ./run.sh
```

Env vars are the recommended path for production secrets (don't commit
them to `config.toml`).

## Section reference

### `[paths]`

```toml
data_dir       = "../animadex-data"
database       = "${data_dir}/animadex.db"
characters_dir = "${data_dir}/characters"
artists_dir    = "${data_dir}/artists"
copyrights_dir = "${data_dir}/copyrights"
```

`${data_dir}` substitutes the resolved value of `data_dir` into the
other keys. Relative paths resolve against the repo root.

Within each `<mode>_dir` the layout is:

```
characters/
  images/     full-resolution PNGs
  thumbs/     WebP thumbnails (built on demand)
artists/
  images/
  thumbs/
copyrights/
  thumbs/     2x2 collages built from the character thumbs
```

### `[server]`

```toml
host        = "127.0.0.1"
port        = 5000
debug       = false
secret_key  = ""              # required; `python -m animadex genkey`
trust_proxy = false           # set true if behind nginx/Caddy
```

`secret_key` signs the admin session cookie and the contact-form
captcha tokens. Without it, sessions reset on every restart (the app
prints a warning).

`trust_proxy = true` makes the rate limiter honour `X-Forwarded-For`.
Only enable behind a reverse proxy you control.

### `[gallery]`

```toml
page_size     = 72            # 6 cols x 12 rows
thumb_height  = 445           # px; width scales proportionally
thumb_quality = 82            # 0-100 WebP quality
```

### `[cache]`

```toml
landing_seconds   = 0
character_seconds = 0
api_seconds       = 0
image_seconds     = 604800
thumb_seconds     = 604800
```

`max-age` per response group. Defaults are tuned for **local dev** --
zero on HTML/JSON so changes are visible immediately. For a public
deploy, raise them. Suggested production values:

```toml
landing_seconds   = 300       # 5 min
character_seconds = 3600      # 1 hour
api_seconds       = 60        # 1 min
```

Images stay at 604800 (one week) because every URL is cache-busted
with `?v=<image_version>` whenever the underlying file changes -- a
new version is a new cache key, so long TTLs are safe.

### `[admin]`

```toml
username = "admin"
password = ""                 # empty = admin disabled
```

If `password` is empty, the `/admin/login` form returns a 503 with a
message pointing here. The admin inbox at `/admin/messages` only
matters when the contact form is enabled.

### `[ratelimit]`

```toml
enabled       = true
burst         = "150/10"      # 150 requests / 10 s
sustained     = "2000/300"    # 2000 / 5 min
block_seconds = 600           # cool-off after either window trips
```

Per-IP sliding-window limiter. `127.0.0.1`, `::1` and the `/static`
folder are always exempt, so iterating against the running server is
never throttled. Set `enabled = false` to disable entirely.

### `[features]`

```toml
scoring_enabled = false       # artwork-scorer; needs requirements-scoring.txt
loras_enabled   = false       # CivitAI sync; needs internet
contact_enabled = true        # /api/contact + admin inbox
```

### `[civitai]`

```toml
api_key = ""
```

Optional CivitAI token. Anonymous requests work but are rate-limited.

### `[generation]`

```toml
comfyui_url   = "http://127.0.0.1:8188/"
workflow_file = ""            # empty = generation disabled
```

When `workflow_file` is empty, the pipeline orchestrator skips the
"render image" step and just notes any missing images so you can drop
them in by hand. Set it to a ComfyUI workflow JSON path to wire up
automated generation -- see `docs/pipeline.md`.

## Environment-variable override examples

```bash
# Run on a different port without editing config.toml
ANIMADEX_SERVER_PORT=8080 ./run.sh

# Use a different data folder (e.g. for testing)
ANIMADEX_PATHS_DATA_DIR=/tmp/animadex-test ./run.sh

# Production hardening
ANIMADEX_SERVER_TRUST_PROXY=true \
ANIMADEX_CACHE_API_SECONDS=60 \
ANIMADEX_CACHE_LANDING_SECONDS=300 \
ANIMADEX_RATELIMIT_BURST=300/10 \
  gunicorn 'animadex.app:create_app()'
```

## Generating a secret key

```bash
python -m animadex genkey
# prints 64 hex chars; paste into config.toml [server].secret_key
```

Or via env var:

```bash
export ANIMADEX_SERVER_SECRET_KEY="$(python -m animadex genkey)"
```
