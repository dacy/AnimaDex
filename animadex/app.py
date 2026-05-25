"""Flask application factory.

Builds an app from a `Config`. The factory pattern lets the test
harness (and `python -m animadex serve`) reuse the same wiring without
relying on import-time side effects.
"""

from __future__ import annotations

import os
import secrets

from flask import Flask, g

from . import db
from .config import Config, ensure_dirs, load as load_config
from .gzip_mw import compress_response
from .ratelimit import RateLimiter
from .routes.admin import admin_bp
from .routes.api_gallery import api as api_gallery_bp
from .routes.api_images import images_bp
from .routes.contact import contact_bp
from .routes.pages import pages as pages_bp
from .routes.sitemap import sitemap_bp


def create_app(cfg: Config | None = None) -> Flask:
    if cfg is None:
        cfg = load_config()

    # Convenience: an origin string used by templates that need an
    # absolute URL (SEO meta tags, sitemap).
    setattr(cfg, 'server_origin',
            f'http://{cfg.server.host}:{cfg.server.port}')
    # Hand-curation routes live behind /api/dev/. They're disabled in
    # the public repo by default; flip via ANIMADEX_DEV=1 if you need
    # them for moderation.
    setattr(cfg, 'features_dev', bool(os.environ.get('ANIMADEX_DEV')))

    ensure_dirs(cfg)
    _ensure_database(cfg)

    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')
    app.config['ANIMADEX'] = cfg
    app.json.sort_keys = False
    app.json.compact = True
    app.secret_key = cfg.server.secret_key or secrets.token_hex(32)
    if not cfg.server.secret_key:
        print('[startup] No server.secret_key set in config.toml -- using '
              'an ephemeral key. Admin sessions will not survive a '
              'restart. Generate one with `python -m animadex genkey`.',
              flush=True)

    limiter = RateLimiter(cfg.ratelimit,
                          trust_proxy=cfg.server.trust_proxy)

    @app.before_request
    def _rate_limit():
        return limiter.check()

    @app.before_request
    def _open_db():
        if 'db' not in g:
            g.db = db.connect_ro(cfg.paths.database)

    @app.teardown_appcontext
    def _close_db(_exc):
        conn = g.pop('db', None)
        if conn is not None:
            conn.close()

    @app.after_request
    def _gzip(resp):
        return compress_response(resp)

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_gallery_bp)
    app.register_blueprint(images_bp)
    app.register_blueprint(sitemap_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(admin_bp)

    return app


def _ensure_database(cfg: Config) -> None:
    """Open (and migrate, if needed) the database. Creates the file if
    it doesn't exist -- the schema is idempotent so this is safe even
    on a fresh install. Surfaces a clear startup error if the path is
    unwritable so the user knows what to fix."""
    path = cfg.paths.database
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    conn = db.connect_rw(path)
    try:
        chars = db.count_characters(conn)
        artists = db.count_artists(conn)
    finally:
        conn.close()
    print(f'Ready: {chars} characters, {artists} artists  ({path})',
          flush=True)
