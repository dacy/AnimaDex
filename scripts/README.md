# Scripts

Standalone helper scripts that live outside the `animadex` Python
package. They are not imported by the web app; they're tools you run
by hand (or get shelled out to by the orchestrator).

## `generate_dataset.py`

The ComfyUI driver. Renders character / artist images from a CSV using
a ComfyUI workflow JSON.

The pipeline orchestrator shells out to this script when
`[generation].workflow_file` is configured. If you don't use ComfyUI,
replace this file with your own generator -- see `docs/pipeline.md`
for the contract. As long as your replacement:

- accepts `--out <path>` and writes the image at that path,
- returns exit code 0 on success,

the orchestrator doesn't care what's inside.

## `clean_explicit_tags.py`

One-off helper that strips NSFW / explicit tags from a danbooru
character CSV. Run it on your source CSV before feeding it to
`python -m animadex pipeline`.
