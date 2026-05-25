# Sample data

A tiny seed bundle so AnimaDex boots into a populated gallery on first
run: 20 characters across diverse copyrights, 10 placeholder artists,
and 13 copyright collage thumbnails.

Only **thumbnails** ship here. The full-resolution PNGs are not
included (they would balloon the repo); the gallery's "View full"
link will 404 until you generate or drop in your own. See
`docs/pipeline.md` for how to wire up your own image generator.

The `install.{sh,bat}` script seeds these into your configured
`data_dir` once on first install, then leaves them alone. To re-seed,
delete `<data_dir>/.seeded` and re-run the installer.
