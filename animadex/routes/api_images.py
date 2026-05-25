"""/thumb/<mode>/<slug>  --  on-demand WebP thumbnail
/img/<mode>/<slug>      --  full-resolution PNG
/thumb/copyrights/<slug> -- copyright collage thumbnail

Thumbnails are built on first request if the full image exists but the
WebP doesn't yet, so dropping in a freshly generated PNG is enough --
the next page load builds and caches the thumb.
"""

from __future__ import annotations

import os

from flask import Blueprint, abort, current_app, g, send_file

from .. import db
from ..images import (full_path, thumb_path, copyright_thumb_filename,
                      _thumb_dir, _full_dir)
from ..pipeline.thumbnails import build_one_file

images_bp = Blueprint('api_images', __name__)


def _image_cache(resp, *, thumb: bool):
    cfg = current_app.config['ANIMADEX'].cache
    secs = cfg.thumb_seconds if thumb else cfg.image_seconds
    resp.headers['Cache-Control'] = (
        f'public, max-age={secs}, immutable' if secs else 'no-store')
    return resp


def _serve(mode: str, slug: str, *, want_thumb: bool):
    if mode == 'characters':
        row = db.get_character(g.db, slug)
    elif mode == 'artists':
        row = db.get_artist(g.db, slug)
    else:
        abort(404)
    if row is None:
        abort(404)
    name = row['thumbname'] if want_thumb else row['imgname']
    folder = _thumb_dir(current_app.config['ANIMADEX'], mode) if want_thumb \
        else _full_dir(current_app.config['ANIMADEX'], mode)
    dst = os.path.join(folder, name)

    if want_thumb and not os.path.exists(dst):
        # Build the WebP thumb on the fly from the full PNG.
        cfg = current_app.config['ANIMADEX']
        src = full_path(cfg, mode, row['imgname'])
        if not os.path.exists(src):
            abort(404)
        try:
            build_one_file(src, dst,
                           height=cfg.gallery.thumb_height,
                           quality=cfg.gallery.thumb_quality)
        except OSError:
            abort(404)

    if not os.path.exists(dst):
        abort(404)
    mime = 'image/webp' if want_thumb else 'image/png'
    return _image_cache(send_file(dst, mimetype=mime), thumb=want_thumb)


@images_bp.route('/thumb/<mode>/<path:slug>')
def thumb(mode, slug):
    return _serve(mode, slug, want_thumb=True)


@images_bp.route('/img/<mode>/<path:slug>')
def image(mode, slug):
    return _serve(mode, slug, want_thumb=False)


@images_bp.route('/thumb/copyrights/<path:slug>')
def copyright_thumb(slug):
    if not db.copyright_exists(g.db, slug):
        abort(404)
    cfg = current_app.config['ANIMADEX']
    fname = copyright_thumb_filename(slug)
    path = thumb_path(cfg, 'copyrights', fname)
    if not os.path.exists(path):
        abort(404)
    return _image_cache(send_file(path, mimetype='image/webp'), thumb=True)
