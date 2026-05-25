# AnimaDex

Self-hosted, searchable gallery for AI-generated anime character and
artist references. Flask backend, vanilla JS frontend, SQLite storage,
zero external dependencies for the core feature set.

Built around three datasets:

- **Characters** -- one entry per character with faceted filters
  (copyright, hair colour, hair length, eye colour, gender), text
  search, and optional CivitAI LoRA links.
- **Artists** -- one entry per artist with optional quality scoring
  (Muinez/artwork-scorer ConvNeXt model) and hand-curated category
  filters.
- **Copyrights** -- a browse view derived from the characters dataset,
  with 2x2 collage thumbnails per series.

## Quick start

```bash
git clone https://github.com/your-username/AnimaDex.git
cd AnimaDex
./install.sh       # creates .venv, installs deps, seeds samples
./run.sh           # serves on http://127.0.0.1:5000
```

Windows:

```bat
git clone https://github.com/your-username/AnimaDex.git
cd AnimaDex
install.bat
run.bat
```

On first run the installer copies a small sample bundle (~20
characters, ~10 artists, thumbnails only) into your configured
`data_dir` so the gallery isn't empty. Bring your own CSV + images to
fill it out -- see `docs/data-format.md`.

## Configuration

One unified file: `config.toml`. The installer creates it from
`config.toml.example`; edit it to set:

- `[server].secret_key` -- required for sessions. Run
  `python -m animadex genkey` to get a fresh hex value.
- `[admin].password` -- only if you want the contact-form admin inbox.
- `[paths].data_dir` -- where the SQLite DB and image folders live.
  Defaults to `../animadex-data` so user data stays out of git.
- `[cache].*` -- HTTP `max-age` per route group. Defaults are 0
  (instant updates during dev); raise for production.

Any value can also be overridden by an environment variable named
`ANIMADEX_<SECTION>_<KEY>` (e.g. `ANIMADEX_SERVER_PORT=8080`).

See `docs/configuration.md` for the full reference.

## Pipeline

The `python -m animadex pipeline <csv>` command processes a CSV
row-by-row. For each row it:

1. Upserts the row into SQLite.
2. Generates the full image via ComfyUI -- if `generation.workflow_file`
   is set and the file is missing. Otherwise the orchestrator notes the
   path and moves on, so you can drop your own renders in by hand.
3. Builds the WebP thumbnail if missing or stale.
4. Scores the artist (artists only, if `features.scoring_enabled`).
5. Stamps `image_version` so the web app cache-busts URLs that change.

Each step is idempotent. Re-running on a row that's already fully
processed does no work; delete a thumbnail and re-run to rebuild just
that thumbnail.

```bash
# Process all rows
python -m animadex pipeline data/characters.csv --mode characters

# Just one row (handy when you re-render one image)
python -m animadex pipeline data/characters.csv --mode characters \
    --rows hatsune_miku

# Preview the plan without writing anything
python -m animadex pipeline data/characters.csv --mode characters \
    --dry-run --limit 5
```

See `docs/pipeline.md` for plugging in your own image generator.

## Optional features

Each ships disabled. Flip the matching `[features]` switch in
`config.toml` and install the right requirements file:

| Feature | Extra deps | Notes |
| ------- | ---------- | ----- |
| Artist scoring | `pip install -r requirements-scoring.txt` | Downloads the artwork-scorer model on first use (~350 MB). |
| Image generation | `pip install -r requirements-generation.txt` | Needs a running ComfyUI server + your own workflow JSON. |
| CivitAI LoRA sync | (none) | API key in `[civitai].api_key` raises the rate limit. |

## Architecture

```
animadex/                Python package
  app.py                 Flask factory
  config.py              TOML loader + env override
  db.py                  SQLite layer (sqlite3, no ORM)
  schema.sql             Tables, indices
  images.py              URL builders, ?v= cache-busting
  ratelimit.py           Per-IP sliding-window limiter
  gzip_mw.py             Response compression
  auth.py / captcha.py   Admin session + contact captcha
  routes/                One module per blueprint
    api_gallery.py       /api/<mode>/{facets,facet,search}
    api_images.py        /thumb /img
    pages.py             /  /c/<slug>
    sitemap.py           /sitemap.xml
    contact.py           /api/contact*
    admin.py             /admin/*
  pipeline/              Data ingestion + image work
    orchestrator.py      Per-row driver
    ingest.py            CSV -> SQLite upsert
    thumbnails.py        WebP builds + copyright collages
    scoring.py           artwork-scorer (optional)
    loras.py             CivitAI sync
    generation.py        Shell out to scripts/generate_dataset.py
  templates/  static/

scripts/                 Standalone scripts (not on the Python path)
  generate_dataset.py    ComfyUI driver -- replace with your own
  clean_explicit_tags.py CSV tag-pruning helper

samples/                 Tiny seed CSVs + thumbnails (committed)
docs/                    Configuration + pipeline references
```

The Python package is self-contained. Image folders, the SQLite DB,
and downloaded models all live under `paths.data_dir` -- not inside
the repo -- so `git status` stays clean.

## License

MIT. See `LICENSE`.
