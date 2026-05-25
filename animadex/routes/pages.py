"""HTML pages: landing (/), gallery shell (/), per-character SEO (/c/<slug>)."""

from __future__ import annotations

import random
import time
import urllib.parse

from flask import (Blueprint, abort, current_app, g, render_template,
                   request)

from .. import db
from ..images import thumb_url, img_url

pages = Blueprint('pages', __name__)


# ---- landing page caches (process-local) ------------------------------
LANDING_COUNTS_TTL = 600
_landing_counts_cache: tuple[float, tuple[int, int] | None] = (0.0, None)
_landing_pool_cache: tuple[float, tuple[list, list] | None] = (0.0, None)


def _landing_counts(conn):
    global _landing_counts_cache
    expires, data = _landing_counts_cache
    if data is not None and time.monotonic() < expires:
        return data
    data = (db.count_characters(conn), db.count_artists(conn))
    _landing_counts_cache = (time.monotonic() + LANDING_COUNTS_TTL, data)
    return data


def _landing_pool(conn):
    global _landing_pool_cache
    expires, data = _landing_pool_cache
    if data is not None and time.monotonic() < expires:
        return data
    chars = conn.execute(
        'SELECT character AS slug, image_version FROM characters '
        'ORDER BY count DESC LIMIT 300').fetchall()
    artists = conn.execute(
        'SELECT artist AS slug, image_version FROM artists '
        'ORDER BY count DESC LIMIT 200').fetchall()
    data = ([(r['slug'], r['image_version']) for r in chars],
            [(r['slug'], r['image_version']) for r in artists])
    _landing_pool_cache = (time.monotonic() + LANDING_COUNTS_TTL, data)
    return data


def _landing_collage(conn, n_char=14, n_artist=6):
    chars, artists = _landing_pool(conn)
    pool = (
        [('characters', s, v)
         for s, v in random.sample(chars, min(n_char, len(chars)))] +
        [('artists', s, v)
         for s, v in random.sample(artists, min(n_artist, len(artists)))])
    random.shuffle(pool)
    items = []
    for mode, slug, version in pool:
        blur = (0.0 if random.random() < 0.45
                else round(random.uniform(1.5, 5), 1))
        items.append({
            'url':     thumb_url(mode, slug, version),
            'top':     round(random.uniform(-4, 88), 1),
            'left':    round(random.uniform(-5, 92), 1),
            'w':       random.randint(110, 230),
            'opacity': round(random.uniform(0.22, 0.5), 2),
            'blur':    blur,
            'rotate':  random.randint(-6, 6),
        })
    return items


def _format_count(n):
    if n >= 1000:
        n = (n // 1000) * 1000
    elif n >= 100:
        n = (n // 100) * 100
    return f'{n:,}'


def _set_cache(resp, kind: str):
    cfg = current_app.config['ANIMADEX'].cache
    secs = getattr(cfg, f'{kind}_seconds', 0)
    resp.headers['Cache-Control'] = (
        f'public, max-age={secs}' if secs else 'no-store')
    return resp


@pages.route('/')
def index():
    # Bare "/" -> marketing landing; any query string -> gallery shell.
    cfg = current_app.config['ANIMADEX']
    if request.args:
        resp = current_app.make_response(
            render_template('index.html', dev=cfg.features_dev))
        return _set_cache(resp, 'landing')
    conn = g.db
    chars, artists = _landing_counts(conn)
    resp = current_app.make_response(render_template(
        'landing.html',
        collage=_landing_collage(conn),
        char_count=_format_count(chars),
        artist_count=_format_count(artists),
    ))
    return _set_cache(resp, 'landing')


# ---- per-character SEO landing pages ----------------------------------

TAG_LIMIT      = 60
TAGS_PREVIEW_N = 8


@pages.route('/c/<path:slug>')
def character_page(slug):
    """Server-rendered per-character page so search engines and shared
    links land on a real <title>/<meta>-tagged document."""
    slug = urllib.parse.unquote(slug or '').strip()
    if not slug:
        abort(404)
    row = db.character_detail(g.db, slug)
    if row is None:
        abort(404)
    tags = [t.strip() for t in (row['core_tags'] or '').split(',')
            if t.strip()][:TAG_LIMIT]
    loras = db.loras_for(g.db, [slug]).get(slug, [])
    v = row['image_version']
    site_origin = current_app.config['ANIMADEX'].server_origin
    resp = current_app.make_response(render_template(
        'character.html',
        c=row,
        tags=tags,
        tag_count=len(tags),
        tags_preview=', '.join(tags[:TAGS_PREVIEW_N]),
        loras=loras,
        thumb_url=thumb_url('characters', slug, v),
        img_url=img_url('characters', slug, v),
        count_fmt=f"{int(row['count'] or 0):,}",
        site_origin=site_origin,
    ))
    return _set_cache(resp, 'character')
