"""GET /sitemap.xml -- one <url> per character, lastmod from image_version."""

from __future__ import annotations

import datetime as dt

from flask import Blueprint, Response, current_app, g, render_template

from .. import db

sitemap_bp = Blueprint('sitemap', __name__)


@sitemap_bp.route('/sitemap.xml')
def sitemap():
    rows = db.all_character_slugs(g.db)
    site_origin = current_app.config['ANIMADEX'].server_origin
    urls = []
    for r in rows:
        v = r['image_version']
        lastmod = dt.datetime.utcfromtimestamp(v).strftime('%Y-%m-%d') \
            if v else ''
        urls.append({
            'loc': f'{site_origin}/c/{r["slug"]}',
            'lastmod': lastmod,
        })
    body = render_template('sitemap.xml',
                           site_origin=site_origin,
                           urls=urls)
    resp = Response(body, mimetype='application/xml')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp
