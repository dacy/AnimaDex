# Deploying

AnimaDex is a normal Flask app -- nothing exotic. Two flavours: running
the dev server (what `./run.sh` does) is fine for personal use on
your LAN, but for anything public you want a WSGI server + reverse
proxy.

## Production stack

Recommended: gunicorn behind nginx (or Caddy).

```bash
pip install gunicorn
ANIMADEX_SERVER_TRUST_PROXY=true \
ANIMADEX_CACHE_LANDING_SECONDS=300 \
ANIMADEX_CACHE_CHARACTER_SECONDS=3600 \
ANIMADEX_CACHE_API_SECONDS=60 \
  gunicorn -w 4 -b 127.0.0.1:5000 'animadex.app:create_app()'
```

`trust_proxy=true` makes the rate limiter read `X-Forwarded-For` --
**only** enable this when you control the upstream proxy, otherwise
clients can spoof their IP.

## systemd unit

```ini
# /etc/systemd/system/animadex.service
[Unit]
Description=AnimaDex
After=network.target

[Service]
User=animadex
WorkingDirectory=/srv/animadex
Environment="ANIMADEX_SERVER_TRUST_PROXY=true"
Environment="ANIMADEX_CACHE_LANDING_SECONDS=300"
Environment="ANIMADEX_CACHE_CHARACTER_SECONDS=3600"
Environment="ANIMADEX_CACHE_API_SECONDS=60"
EnvironmentFile=/srv/animadex/.env
ExecStart=/srv/animadex/.venv/bin/gunicorn \
    -w 4 -b 127.0.0.1:5000 'animadex.app:create_app()'
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## nginx

```nginx
server {
    listen 443 ssl http2;
    server_name animadex.example.com;
    # ssl_certificate ...;

    location /static/ {
        alias /srv/animadex/animadex/static/;
        access_log off;
        expires 30d;
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If you serve images straight from disk via nginx (avoiding the Python
layer for cache hits), point a location block at
`<data_dir>/<mode>/{images,thumbs}/` -- but then the per-row
`?v=<image_version>` query string is up to nginx to ignore (it does by
default; cache key is based on URL path, not querystring).

## Data backups

The two things you can't regenerate are:

1. `<data_dir>/animadex.db` -- contact-form messages, hand-curated
   artist categories, CivitAI LoRA matches, the
   `loras_synced_at` / `*_built_at` meta keys. Run
   `sqlite3 animadex.db ".backup '/path/to/backup.db'"` for a hot
   backup; cron it nightly.
2. Your image folders, if you've been generating them locally rather
   than pulling from a deterministic source.

Everything else (the schema, the CSVs, the LoRA matches if you keep
the CivitAI sync cron) can be rebuilt from scratch.

## Updates

`git pull && ./install.sh` is the upgrade path. The installer is
idempotent:

- Existing `config.toml` is never overwritten.
- The schema is re-applied (idempotent; only adds missing columns).
- Sample seeding is skipped after the first run.

If a new release adds a new `[section]` to `config.toml.example`,
diff the two files and copy the new keys over by hand.
