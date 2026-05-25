"""`python -m animadex <subcommand>` -- the single entry point.

Subcommands:
    serve       Start the Flask web app.
    pipeline    Per-row pipeline driver (ingest + generate + thumb + score).
    build-db    CSV -> SQLite upsert in bulk (no image work).
    build-thumbs Build any missing thumbnails / copyright collages.
    score       Score every artist with no score yet.
    sync-loras  Pull character LoRAs from CivitAI.
    genkey      Print a fresh hex secret_key for config.toml.
    db-init     Create the schema + data dirs without doing anything else.

Run `python -m animadex <subcommand> --help` for per-command options.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

from . import db
from .config import REPO_ROOT, ensure_dirs, load as load_config


def _cmd_serve(args):
    from .app import create_app
    cfg = load_config()
    app = create_app(cfg)
    app.run(host=cfg.server.host, port=cfg.server.port,
            debug=cfg.server.debug, threaded=True)


def _cmd_pipeline(args):
    from .pipeline.orchestrator import run_csv
    cfg = load_config()
    ensure_dirs(cfg)
    only = (s.strip() for s in args.rows.split(',')) if args.rows else None
    only = [s for s in only if s] if only else None
    summary = run_csv(cfg, args.csv, args.mode,
                       limit=args.limit, only_slugs=only,
                       dry_run=args.dry_run)
    print(f'\nProcessed {summary["processed"]} rows, failed '
          f'{summary["failed"]}.')


def _cmd_build_db(args):
    from .pipeline.ingest import build_db as bulk_ingest
    cfg = load_config()
    ensure_dirs(cfg)
    conn = db.connect_rw(cfg.paths.database)
    try:
        count = bulk_ingest(conn, args.csv, args.mode)
        print(f'Upserted {count} {args.mode} from {args.csv}.')
    finally:
        conn.close()


def _cmd_build_thumbs(args):
    from .pipeline.thumbnails import (build_all_for_mode,
                                       build_copyright_collages)
    cfg = load_config()
    ensure_dirs(cfg)
    if args.mode in ('all', 'characters'):
        build_all_for_mode(cfg, 'characters')
    if args.mode in ('all', 'artists'):
        build_all_for_mode(cfg, 'artists')
    if args.mode in ('all', 'copyrights') and args.csv:
        n = build_copyright_collages(cfg, args.csv)
        print(f'Built {n} copyright collages.')


def _cmd_score(args):
    from .pipeline.scoring import score_all_pending
    cfg = load_config()
    if not cfg.features.scoring_enabled:
        print('[scoring] features.scoring_enabled is false in config.toml; '
              'running anyway. Set it to true to integrate scoring with '
              'the pipeline orchestrator.', file=sys.stderr)
    score_all_pending(cfg, limit=args.limit)


def _cmd_sync_loras(args):
    from .pipeline.loras import sync
    cfg = load_config()
    sync(cfg, full=args.full)


def _cmd_genkey(args):
    print(secrets.token_hex(32))


def _cmd_db_init(args):
    cfg = load_config()
    ensure_dirs(cfg)
    conn = db.connect_rw(cfg.paths.database)
    conn.close()
    print(f'Schema applied: {cfg.paths.database}')


def main(argv=None):
    p = argparse.ArgumentParser(prog='animadex',
                                description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('serve', help='Start the Flask web app.')
    s.set_defaults(func=_cmd_serve)

    s = sub.add_parser('pipeline',
                       help='Per-row pipeline (ingest + image + thumb + score).')
    s.add_argument('csv', help='Path to a characters or artists CSV.')
    s.add_argument('--mode', choices=('characters', 'artists'),
                   required=True)
    s.add_argument('--limit', type=int, default=0,
                   help='Stop after N rows (0 = all).')
    s.add_argument('--rows',
                   help='Comma-separated slugs to process; others skipped.')
    s.add_argument('--dry-run', action='store_true',
                   help='Print the plan, run nothing.')
    s.set_defaults(func=_cmd_pipeline)

    s = sub.add_parser('build-db',
                       help='Bulk CSV -> SQLite upsert; no image work.')
    s.add_argument('csv')
    s.add_argument('--mode', choices=('characters', 'artists'),
                   required=True)
    s.set_defaults(func=_cmd_build_db)

    s = sub.add_parser('build-thumbs',
                       help='Build any missing thumbnails and collages.')
    s.add_argument('--mode',
                   choices=('all', 'characters', 'artists', 'copyrights'),
                   default='all')
    s.add_argument('--csv',
                   help='Character CSV (required for the copyrights step).')
    s.set_defaults(func=_cmd_build_thumbs)

    s = sub.add_parser('score',
                       help='Run artwork-scorer over unscored artists.')
    s.add_argument('--limit', type=int, default=0)
    s.set_defaults(func=_cmd_score)

    s = sub.add_parser('sync-loras', help='Pull LoRAs from CivitAI.')
    s.add_argument('--full', action='store_true',
                   help='Ignore the stored timestamp; full re-sync.')
    s.set_defaults(func=_cmd_sync_loras)

    s = sub.add_parser('genkey',
                       help='Print a fresh hex secret_key for config.toml.')
    s.set_defaults(func=_cmd_genkey)

    s = sub.add_parser('db-init',
                       help='Create the schema and data folders.')
    s.set_defaults(func=_cmd_db_init)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    main()
