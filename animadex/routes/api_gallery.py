"""JSON API: characters, artists, copyrights -- search + facets."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, g, jsonify, request

from .. import db
from ..images import (serialize_character, serialize_artist,
                      serialize_copyright)

api = Blueprint('api_gallery', __name__)


# Per-mode plumbing -- the gallery routes are mostly mode-parametric.
DATASETS = {
    'characters': {
        'facets':    db.CHARACTER_FACETS,
        'search':    db.search_characters,
        'facet':     db.character_facet_values,
        'count':     db.count_characters,
        'serialize': serialize_character,
    },
    'artists': {
        'facets':    db.ARTIST_FACETS,
        'search':    db.search_artists,
        'facet':     db.artist_facet_values,
        'count':     db.count_artists,
        'serialize': serialize_artist,
    },
}


def _api_cache_header(resp):
    secs = int(current_app.config['ANIMADEX'].cache.api_seconds)
    resp.headers['Cache-Control'] = f'public, max-age={secs}'
    return resp


@api.route('/api/<mode>/facets')
def api_facets(mode):
    plumb = DATASETS.get(mode)
    if plumb is None:
        abort(404)
    conn = g.db
    resp = jsonify(total=plumb['count'](conn), facets={
        name: plumb['facet'](conn, name, '', 30) for name in plumb['facets']
    })
    return _api_cache_header(resp)


@api.route('/api/<mode>/facet/<name>')
def api_facet(mode, name):
    plumb = DATASETS.get(mode)
    if plumb is None:
        abort(404)
    data = plumb['facet'](g.db, name, request.args.get('q', '').strip(), 30)
    if data is None:
        abort(404)
    resp = jsonify(**data)
    return _api_cache_header(resp)


@api.route('/api/<mode>/search')
def api_search(mode):
    plumb = DATASETS.get(mode)
    if plumb is None:
        abort(404)
    args = request.args
    page_size = current_app.config['ANIMADEX'].gallery.page_size
    try:
        page = max(1, int(args.get('page', 1)))
    except ValueError:
        page = 1
    filters = {f: set(args.getlist(f)) for f in plumb['facets']}
    if args.get('loras'):
        filters['loras'] = True
    total, rows = plumb['search'](
        g.db,
        q=args.get('q', '').strip(),
        sort=args.get('sort', 'count'),
        filters=filters,
        page=page,
        page_size=page_size,
        seed=args.get('seed'),
    )
    results = [plumb['serialize'](r) for r in rows]
    if mode == 'characters' and results:
        loras = db.loras_for(g.db, [r['slug'] for r in results])
        for r in results:
            r['loras'] = loras.get(r['slug'], [])
    resp = jsonify(
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
        results=results,
    )
    return _api_cache_header(resp)


# --- copyrights (derived from the characters table) --------------------

@api.route('/api/copyrights/facets')
def api_copyright_facets():
    resp = jsonify(total=db.count_copyrights(g.db), facets={})
    return _api_cache_header(resp)


@api.route('/api/copyrights/search')
def api_copyright_search():
    args = request.args
    page_size = current_app.config['ANIMADEX'].gallery.page_size
    try:
        page = max(1, int(args.get('page', 1)))
    except ValueError:
        page = 1
    total, rows = db.search_copyrights(
        g.db, q=args.get('q', '').strip(),
        sort=args.get('sort', 'count'), page=page, page_size=page_size,
        seed=args.get('seed'))
    resp = jsonify(
        total=total, page=page, page_size=page_size,
        pages=(total + page_size - 1) // page_size,
        results=[serialize_copyright(r, g.db) for r in rows],
    )
    return _api_cache_header(resp)
