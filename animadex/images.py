"""URL + filesystem helpers for image serving.

The `?v=<image_version>` query string on every image URL is how
AnimaDex cache-busts CDNs and browsers when a row's image is
(re)generated. A version of 0 -- the schema default -- emits the bare
URL so caches don't see a meaningless `?v=0` on every page load.
"""

from __future__ import annotations

import os
from urllib.parse import quote

from .config import Config
from .db import sanitize_filename


def thumb_path(cfg: Config, mode: str, filename: str) -> str:
    """Filesystem path to a thumbnail file."""
    base = _thumb_dir(cfg, mode)
    return os.path.join(base, filename)


def full_path(cfg: Config, mode: str, filename: str) -> str:
    """Filesystem path to a full-resolution image."""
    base = _full_dir(cfg, mode)
    return os.path.join(base, filename)


def _thumb_dir(cfg: Config, mode: str) -> str:
    if mode == 'characters':
        return os.path.join(cfg.paths.characters_dir, 'thumbs')
    if mode == 'artists':
        return os.path.join(cfg.paths.artists_dir, 'thumbs')
    if mode == 'copyrights':
        return os.path.join(cfg.paths.copyrights_dir, 'thumbs')
    raise ValueError(f'unknown mode {mode!r}')


def _full_dir(cfg: Config, mode: str) -> str:
    if mode == 'characters':
        return os.path.join(cfg.paths.characters_dir, 'images')
    if mode == 'artists':
        return os.path.join(cfg.paths.artists_dir, 'images')
    raise ValueError(f'no full image dir for mode {mode!r}')


def copyright_thumb_filename(slug: str) -> str:
    return sanitize_filename(slug) + '.webp'


# ---- URL builders (used by serializers and templates) ------------------

def _versioned(url: str, version: int | None) -> str:
    """Append ?v=<int> to a URL unless version is falsy (0 / None)."""
    v = int(version or 0)
    return f'{url}?v={v}' if v else url


def thumb_url(mode: str, slug: str, version: int | None = 0) -> str:
    return _versioned(f'/thumb/{mode}/{quote(slug, safe="()")}', version)


def img_url(mode: str, slug: str, version: int | None = 0) -> str:
    return _versioned(f'/img/{mode}/{quote(slug, safe="()")}', version)


# ---- API serializers (image_version-aware) -----------------------------

def serialize_character(row) -> dict:
    slug = row['character']
    v = row['image_version'] if 'image_version' in row.keys() else 0
    return {
        'slug': slug,
        'name': row['name'],
        'copyright': row['copyright'],
        'copyright_name': row['copyright_name'],
        'trigger': row['trigger'],
        'tags': [t.strip()
                 for t in (row['core_tags'] or '').split(',')
                 if t.strip()],
        'count': row['count'],
        'url': row['url'],
        'thumb_url': thumb_url('characters', slug, v),
        'img_url':   img_url(  'characters', slug, v),
        'has_image': True,
    }


def serialize_artist(row) -> dict:
    slug = row['artist']
    v = row['image_version'] if 'image_version' in row.keys() else 0
    return {
        'slug': slug,
        'name': row['name'],
        'trigger': row['trigger'],
        'count': row['count'],
        'url': row['url'],
        'score': row['score'],
        'thumb_url': thumb_url('artists', slug, v),
        'img_url':   img_url(  'artists', slug, v),
        'has_image': True,
    }


def serialize_copyright(row, conn=None) -> dict:
    """Copyrights are derived from a GROUP BY, so their version lives
    in a side-table. Pass `conn` to look it up; omit for version=0."""
    from . import db
    slug = row['value']
    version = 0
    if conn is not None:
        version = db.copyright_version(conn,
                                       copyright_thumb_filename(slug))
    return {
        'slug': slug,
        'name': row['label'],
        'count': row['n'],
        'thumb_url': thumb_url('copyrights', slug, version),
        'has_image': True,
    }
