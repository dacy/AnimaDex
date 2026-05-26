# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

Install / run the app (POSIX; `install.bat` / `run.bat` mirror these on Windows):

```bash
./install.sh                 # creates .venv, installs core deps, seeds samples
./install.sh --with-scoring  # also installs torch/torchvision/transformers
./install.sh --with-generation
./run.sh                     # serves on http://127.0.0.1:5000
```

Everything in the app is reached via the single entry point `python -m animadex <subcommand>`:

| Command | Purpose |
| ------- | ------- |
| `serve` | Start the Flask web app. |
| `pipeline <csv> --mode characters\|artists` | Per-row pipeline (ingest + generate + thumbnail + score + version bump). Idempotent. Supports `--rows slug1,slug2`, `--limit N`, `--dry-run`. |
| `build-db <csv> --mode ...` | Bulk CSV → SQLite upsert; no image work. |
| `build-thumbs --mode all\|characters\|artists\|copyrights [--csv]` | Build missing thumbnails / copyright collages. `--csv` is required for the `copyrights` step. |
| `score [--limit N]` | Run artwork-scorer over unscored artists. |
| `sync-loras [--full]` | Pull character LoRAs from CivitAI. |
| `genkey` | Print a fresh hex `secret_key` for `config.toml`. |
| `db-init` | Apply the schema + create data folders. |

There is no test suite, lint config, or formatter in the repo — don't invent commands.

## Architecture

**Single Flask app + per-row pipeline + SQLite, no ORM, no migration framework.** Read this section before making structural changes — several conventions look incidental but are load-bearing.

### The `image_version` cache-busting protocol

Every image URL is emitted as `/thumb/<mode>/<slug>?v=<image_version>`. `image_version` is an integer column on `characters` / `artists` (and a side-table `copyright_versions` row for copyrights, since copyrights are a `GROUP BY` and have no row of their own). The pipeline bumps it to `int(time.time())` whenever it does real work (regenerates a full image, rebuilds a thumb). Cache TTLs on images are intentionally long (a week) because a new version is a new cache key. **If you change how an image is produced or replaced, you must call `db.bump_*_version()` for it** — otherwise CDNs and browsers will serve the old bytes indefinitely.

A `?v=0` URL means "no version yet"; `images._versioned()` strips the query string in that case to avoid spamming a meaningless `?v=0` on first render.

### Per-row pipeline + commit-per-row

`animadex/pipeline/orchestrator.py` runs **one CSV row at a time** through `ingest → generate → thumbnail → score → version bump`, committing after each row. Every step checks the filesystem first and short-circuits if it has nothing to do. This means:

- A crash mid-batch only loses the in-flight row; rerunning the same CSV picks up where it stopped.
- Deleting a thumb on disk and re-running rebuilds just that thumb (and bumps the version).
- You can drop hand-rendered PNGs into `<data_dir>/<mode>/images/` and the next pipeline pass will ingest + thumb + version-bump them automatically — `generation` is optional.

Don't refactor the orchestrator to batch transactions across rows; the per-row commit is what makes it crash-safe.

### Two connection helpers — use the right one

`db.connect_rw()` opens a writable connection and applies `schema.sql` + a tiny `_migrate()` that `ALTER`s columns added after launch (`image_version`, `score`). It is **the only path that creates/migrates the DB**. Use it from pipeline / admin / CLI code.

`db.connect_ro()` opens a `PRAGMA query_only = 1` connection — that's what `app.before_request` attaches to `g.db` for request handlers. Read routes must use `g.db` and never call `connect_rw()`; write routes (e.g. `admin.delete`, `contact_submit`) open their own `connect_rw()` connection for the duration of the write.

`schema.sql` is idempotent (`CREATE TABLE IF NOT EXISTS`) and is re-applied on every `connect_rw()`. For column additions to existing tables, extend `_migrate()` in `db.py` rather than editing the schema in a way that requires a drop-and-recreate.

### Config: TOML + env override + `${data_dir}` substitution

`animadex/config.py` reads `config.toml` (path overridable via `$ANIMADEX_CONFIG`) into typed dataclasses, then overlays env vars of the form `ANIMADEX_<SECTION>_<KEY>` (booleans/ints coerced from strings). Path keys support `${data_dir}` substitution; relative paths resolve from `REPO_ROOT`.

Three consequences worth knowing:

- **User data is deliberately outside the repo.** Default `data_dir` is `../animadex-data` so the SQLite DB, full images, and downloaded models never enter `git status`. Don't relocate them into the repo without a reason.
- **`config.toml` is gitignored.** The committed template is `config.toml.example` — keep them in sync when adding settings.
- **Env vars are the production secret path.** `ANIMADEX_SERVER_SECRET_KEY`, `ANIMADEX_ADMIN_PASSWORD`, `ANIMADEX_CIVITAI_API_KEY` are the documented pattern; don't add separate dotenv loading.

### Three datasets, two tables

There are three modes in the UI — `characters`, `artists`, `copyrights` — but only two tables. **Copyrights are derived from `characters` via `GROUP BY copyright`**, which is why their facet/search code lives next to the character code (`db.search_copyrights`, `routes/api_gallery.py`) and they need the `copyright_versions` side-table for cache-busting.

The character→trait fan-out is in the `traits` table (`facet ∈ {hair_color, hair_length, eye_color, gender}`), rebuilt from the row's `core_tags` on ingest. The recognised tag vocabularies live in `db.py` (`HAIR_COLOR_TAGS`, `HAIR_LENGTH_TAGS`, `EYE_COLOR_TAGS`, `GENDER_TAGS`) — tags outside those sets stay as plain display chips and never become facets. If you extend the vocabulary, the ingest step has to be re-run for trait rows to repopulate.

### Optional features are runtime-disabled, not conditionally imported

`features.scoring_enabled`, `features.loras_enabled`, `features.contact_enabled`, and `generation.workflow_file` gate behavior at runtime. The orchestrator and CLI silently skip disabled steps; the only thing that *changes* with the extras-install (`requirements-scoring.txt`, `requirements-generation.txt`) is whether the import inside the optional step succeeds. Keep `torch` / `requests` imports lazy (inside the function that uses them) so a core-only install never tries to import them.

### Blueprints

`app.create_app()` wires six blueprints: `pages` (HTML), `api_gallery` (JSON facets/search), `api_images` (thumb + full image serving — also builds thumbs on-demand if missing), `sitemap`, `contact`, `admin`. Admin is hidden behind a configured `admin.password`; an empty password returns 503 from `/admin/login` rather than 401. There's also an `ANIMADEX_DEV=1` env flag that flips `cfg.features_dev` to expose hand-curation routes under `/api/dev/` — off by default in the public repo.

## Conventions worth following

- **SQL is hand-written via `sqlite3.Row`.** No ORM. Search/facet queries build `WHERE` fragments with parameter lists by hand — match the existing pattern in `db.search_characters` / `db.search_artists` (LIKE escaping via `_like()`, `ESCAPE '\'`).
- **Slug `?` parsing.** `images._versioned()` and the `?v=` URLs use `urllib.parse.quote(slug, safe="()")` — slugs can contain parens, so don't tighten that.
- **CSV→filename rule.** `db.sanitize_filename(trigger)` is the single source of truth for the on-disk filename. Anything else that needs the path must call it (not reimplement it) so file lookups don't drift.
- **Filesystem-vs-DB consistency.** The orchestrator's `_stale()` check uses mtime comparison between thumb and full image — that's how "delete a thumb to rebuild it" works. Don't replace it with a DB-only check.
- **Generation contract.** `scripts/generate_dataset.py` is shelled out, not imported. The contract is just `--out <path>` + exit code 0; the orchestrator only checks the file appeared. You can swap it for any generator without touching `pipeline/generation.py` as long as the CLI signature matches (`docs/pipeline.md` documents this).
