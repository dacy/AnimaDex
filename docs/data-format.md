# Data format

AnimaDex consumes plain CSVs with UTF-8 encoding. Columns the parser
doesn't recognise are ignored, so you can keep extra metadata in the
same file.

## Characters CSV

| Column | Required | Notes |
| ------ | -------- | ----- |
| `character` | yes | The danbooru-style slug. Becomes the primary key and the URL slug (`/c/<character>`). |
| `copyright` | yes | The series slug (e.g. `vocaloid`). Drives the copyright facet. |
| `trigger` | yes | `<name>, <series>` -- both displayed in the gallery, and used to derive the on-disk filename. |
| `core_tags` | yes | Comma-separated danbooru tags. The parser picks out hair / eye / gender tags from this list for facet filters; the rest is shown as the row's tag chips. |
| `count` | no | Integer popularity score; defaults to 0. Drives the default "count" sort. |
| `url` | no | External link shown as a chip on the row. |

Filename rule: the image and thumbnail file names are
`sanitize_filename(trigger) + '.png' / '.webp'` where
`sanitize_filename` replaces `<>:"/\|?*` with `_` and strips trailing
dots/spaces. So `trigger = "hatsune miku, vocaloid"` produces
`hatsune miku, vocaloid.png`.

Example row:

```csv
character,copyright,trigger,core_tags,count,url
hatsune_miku,vocaloid,"hatsune miku, vocaloid","1girl, aqua eyes, twintails, detached sleeves",103500,https://danbooru.donmai.us/posts?tags=hatsune_miku
```

Recognised tag vocabularies (the parser is strict -- anything outside
these lists stays as a plain tag and doesn't become a facet):

- **Hair colour**: see `animadex/db.py:HAIR_COLOR_TAGS`.
- **Hair length**: very short / short / medium / long / very long /
  absurdly long.
- **Eye colour**: see `animadex/db.py:EYE_COLOR_TAGS`.
- **Gender**: `1boy`, `1girl`, `1other`, `no humans`.

## Artists CSV

| Column | Required | Notes |
| ------ | -------- | ----- |
| `artist` | yes | Slug, primary key, URL slug. |
| `trigger` | no | Display name. Defaults to `<artist>` with `_` -> space. |
| `count` | no | Popularity, drives the default sort. |
| `url` | no | External link. |

The `score` column is populated by the artist-scoring step -- don't
set it by hand unless you want a fixed score.

Example:

```csv
artist,trigger,count,url
0-den,0-den,52,https://danbooru.donmai.us/posts?tags=0-den
```

## Image directory layout

The orchestrator expects (and creates) this layout under
`[paths].<mode>_dir`:

```
characters/
  images/<sanitised>.png
  thumbs/<sanitised>.webp
artists/
  images/<sanitised>.png
  thumbs/<sanitised>.webp
copyrights/
  thumbs/<sanitised-copyright-slug>.webp
```

You can drop your own images at the right path and the pipeline will
treat them as already-generated -- ingest will pick them up, the
thumbnail step builds the matching WebP, and the version bump fires
so the web app cache-busts.

## Where the source CSVs come from

The original animadex dataset was built from
[Danbooru's wiki dumps](https://danbooru.donmai.us/wiki_pages.json).
Use whatever workflow makes sense for you -- AnimaDex doesn't care
where the CSV came from, only that it matches the columns above.
