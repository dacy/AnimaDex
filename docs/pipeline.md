# Pipeline

The pipeline turns a CSV into a populated gallery. It runs **one row
at a time**, so a crash mid-batch only loses the in-flight row and a
re-run picks up where it stopped. Every step is idempotent.

## What runs per row

```
ingest  →  generate (optional)  →  thumbnail  →  score (artists)  →  bump version
```

1. **Ingest**. CSV columns are parsed and UPSERTed into `characters`
   or `artists`. Existing rows have only the CSV-derived columns
   refreshed -- any extra columns you've added by hand are left alone.
   For characters, the `traits` table is rebuilt from the row's
   `core_tags` so faceted filters stay in sync.

2. **Generate**. If `[generation].workflow_file` is set in
   `config.toml`, the orchestrator shells out to
   `scripts/generate_dataset.py` to render the missing full image via
   ComfyUI. If generation is disabled the step is skipped with a note
   telling you where to drop the file yourself.

3. **Thumbnail**. A proportional WebP scaled to
   `[gallery].thumb_height` (default 445 px), saved into the row's
   `thumbs/` folder. Rebuilt only if missing or older than the full
   image.

4. **Score**. Artists only, and only when
   `[features].scoring_enabled = true`. Runs the
   `Muinez/artwork-scorer` ConvNeXt model on the artist's render and
   stores a 0--1 score. Already-scored rows are skipped.

5. **Bump version**. If any earlier step actually did work,
   `image_version` is set to `int(time.time())`. The web layer reads
   this and appends `?v=<version>` to every image URL so caches see a
   fresh key when the underlying file changes.

## CLI

```bash
# Full pipeline over a CSV
python -m animadex pipeline data/characters.csv --mode characters

# Artists CSV
python -m animadex pipeline data/artists.csv --mode artists

# Subset by slug -- handy when you re-render a single image
python -m animadex pipeline data/characters.csv --mode characters \
    --rows hatsune_miku,kirisame_marisa

# Cap row count
python -m animadex pipeline data/characters.csv --mode characters --limit 20

# Preview without writing
python -m animadex pipeline data/characters.csv --mode characters --dry-run
```

Output is one line per row:

```
  [3/20] hatsune_miku   *ingest -generate (already present) *thumbnail -score (skipped) *version (v=1737000003)
```

`*` means the step did work; `-` means it ran but had nothing to do.

## Step-only commands

Each step is also callable on its own:

| Step | Command |
| ---- | ------- |
| Bulk ingest CSV | `python -m animadex build-db data/characters.csv --mode characters` |
| Build all missing thumbnails | `python -m animadex build-thumbs --mode all --csv data/characters.csv` |
| Score every unscored artist | `python -m animadex score` |
| Sync CivitAI LoRAs | `python -m animadex sync-loras` |

## Plugging in your own generator

`animadex/pipeline/generation.py` shells out to
`scripts/generate_dataset.py`. Replace that file with anything that:

1. Accepts at least `--out <path>` and writes a PNG at that path.
2. Returns exit code 0 on success, non-zero on failure (stderr is
   captured and surfaced in the orchestrator's per-row log).

The orchestrator passes:

```
python scripts/generate_dataset.py \
    --regen \
    --out <full image path> \
    --workflow <[generation].workflow_file> \
    --comfyui-url <[generation].comfyui_url> \
    [--tags <prompt-override>]
```

If you don't use ComfyUI, ignore the `--workflow` and `--comfyui-url`
args and dispatch to whatever stack you do use. The orchestrator only
checks the exit code and that the output file appeared.

You can also leave `generation.workflow_file` empty and just drop
your own pre-rendered PNGs into `<data_dir>/<mode>/images/`. The
orchestrator will pick them up on the next run -- ingest + thumbnail
+ version-bump happens automatically.

## CSV format

See `docs/data-format.md` for the required columns.
