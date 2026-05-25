"""Per-row pipeline driver.

For each CSV row this runs, in order:
    1. ingest         -- upsert the row into SQLite
    2. generation     -- render the full image via ComfyUI (if enabled)
    3. thumbnail      -- build the WebP thumb if missing or stale
    4. scoring        -- artwork-scorer (artists only, if enabled)
    5. version bump   -- stamp characters/artists.image_version so the
                         worker / web app emits a fresh ?v= URL

Every step is idempotent. Re-running on a row that's already fully
processed does no work; deleting a thumb on disk and re-running
rebuilds just that thumb (and bumps the version).
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .. import db
from ..config import Config
from ..images import full_path, thumb_path
from . import generation, ingest, thumbnails


@dataclass
class StepResult:
    name: str
    did_work: bool
    note: str = ''


@dataclass
class RowResult:
    slug: str
    mode: str
    steps: list[StepResult]
    failed: str | None = None

    def summary(self) -> str:
        parts = []
        for s in self.steps:
            mark = '*' if s.did_work else '-'
            extra = f' ({s.note})' if s.note else ''
            parts.append(f'{mark}{s.name}{extra}')
        return ' '.join(parts)


def process_row(conn, cfg: Config, csv_row: dict, mode: str,
                *, dry_run: bool = False) -> RowResult:
    """Run the full pipeline for one CSV row. Mutations are committed
    by the caller (we want one transaction per row so a crash mid-row
    doesn't leave the DB half-updated)."""
    if mode not in ('characters', 'artists'):
        raise ValueError(f'unknown mode {mode!r}')

    steps: list[StepResult] = []
    slug = ((csv_row.get('character') if mode == 'characters'
             else csv_row.get('artist')) or '').strip()
    if not slug:
        return RowResult(slug='', mode=mode, steps=[],
                          failed='empty key column')

    # --- 1. ingest ----
    if dry_run:
        steps.append(StepResult('ingest', False, 'dry-run'))
        # Build a best-effort fields dict from the raw row so the rest
        # of the dry run can still print which paths it would touch.
        parsed = (db.parse_character_row if mode == 'characters'
                  else db.parse_artist_row)(csv_row)
        if mode == 'characters' and isinstance(parsed, tuple):
            fields = parsed[0]
        else:
            fields = parsed or {}
    else:
        fields = ingest.upsert_one(conn, csv_row, mode)
        if fields is None:
            return RowResult(slug=slug, mode=mode, steps=steps,
                              failed='unparseable row')
        steps.append(StepResult('ingest', True))

    imgname = fields.get('imgname', '')
    thumbname = fields.get('thumbname', '')

    full = full_path(cfg, mode, imgname) if imgname else ''
    thumb = thumb_path(cfg, mode, thumbname) if thumbname else ''
    version_bumped = False

    # --- 2. generation ----
    full_ok = bool(full) and os.path.isfile(full)
    if not full_ok:
        if dry_run:
            steps.append(StepResult(
                'generate', False,
                'would generate' if generation.is_enabled(cfg)
                else 'disabled; place image at ' + full))
        elif generation.is_enabled(cfg):
            try:
                generation.render(cfg, mode, imgname)
                full_ok = True
                steps.append(StepResult('generate', True))
                version_bumped = True
            except RuntimeError as e:
                steps.append(StepResult('generate', False,
                                         f'FAILED: {e}'))
        else:
            steps.append(StepResult(
                'generate', False,
                f'skipped (no workflow); drop image at {full}'))
    else:
        steps.append(StepResult('generate', False, 'already present'))

    # --- 3. thumbnail ----
    thumb_ok = bool(thumb) and os.path.isfile(thumb)
    if full_ok and (not thumb_ok or _stale(thumb, full)):
        if dry_run:
            steps.append(StepResult('thumbnail', False, 'would rebuild'))
        else:
            try:
                thumbnails.build_one(cfg, mode, imgname, thumbname)
                steps.append(StepResult('thumbnail', True))
                version_bumped = True
            except OSError as e:
                steps.append(StepResult('thumbnail', False,
                                         f'FAILED: {e}'))
    elif full_ok:
        steps.append(StepResult('thumbnail', False, 'up to date'))
    else:
        steps.append(StepResult('thumbnail', False, 'no full image'))

    # --- 4. scoring (artists only, optional) ----
    if mode == 'artists':
        if not cfg.features.scoring_enabled:
            pass  # silent skip; documented in config.toml.example
        elif not full_ok:
            steps.append(StepResult('score', False, 'no full image'))
        elif dry_run:
            steps.append(StepResult('score', False,
                                     'would score if missing'))
        else:
            try:
                if db.score_missing(conn, slug):
                    from . import scoring
                    value = scoring.score_one(conn, cfg, slug, imgname)
                    if value is None:
                        steps.append(StepResult('score', False,
                                                 'no image'))
                    else:
                        steps.append(StepResult(
                            'score', True, f'score={value:.3f}'))
                else:
                    steps.append(StepResult('score', False,
                                             'already scored'))
            except RuntimeError as e:
                steps.append(StepResult('score', False, f'FAILED: {e}'))

    # --- 5. version stamp ----
    if version_bumped and not dry_run:
        v = int(time.time())
        if mode == 'characters':
            db.bump_character_version(conn, slug, v)
        else:
            db.bump_artist_version(conn, slug, v)
        steps.append(StepResult('version', True, f'v={v}'))

    return RowResult(slug=slug, mode=mode, steps=steps)


def _stale(thumb_path_: str, full_path_: str) -> bool:
    try:
        return os.path.getmtime(thumb_path_) < os.path.getmtime(full_path_)
    except OSError:
        return True


def run_csv(cfg: Config, csv_path: str | Path, mode: str, *,
            limit: int = 0, only_slugs: Iterable[str] | None = None,
            dry_run: bool = False) -> dict:
    """Process every row of a CSV. Commits per row so an interrupt
    only loses the in-flight row. Returns a summary dict."""
    only = set(only_slugs) if only_slugs else None

    print(f'Pipeline: {csv_path} mode={mode} '
          f'{"(dry-run) " if dry_run else ""}'
          f'generation={"on" if generation.is_enabled(cfg) else "off"} '
          f'scoring={"on" if cfg.features.scoring_enabled else "off"}',
          flush=True)

    conn = db.connect_rw(cfg.paths.database)
    try:
        ok = failed = 0
        with open(csv_path, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            i = 0
            for row in reader:
                slug = ((row.get('character') if mode == 'characters'
                         else row.get('artist')) or '').strip()
                if only is not None and slug not in only:
                    continue
                if limit and i >= limit:
                    break
                i += 1
                try:
                    result = process_row(conn, cfg, row, mode,
                                          dry_run=dry_run)
                except Exception as e:  # noqa: BLE001 - never crash the whole run
                    failed += 1
                    print(f'  [{i}] {slug} -- FAILED: {e}', flush=True)
                    continue
                if result.failed:
                    failed += 1
                    print(f'  [{i}] {slug or "?"} -- skipped: '
                          f'{result.failed}', flush=True)
                else:
                    ok += 1
                    print(f'  [{i}] {result.slug}  {result.summary()}',
                          flush=True)
                if not dry_run:
                    conn.commit()
        return {'processed': ok, 'failed': failed}
    finally:
        conn.close()
