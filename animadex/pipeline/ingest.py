"""CSV -> SQLite ingest. Single-row callable; the orchestrator uses
`upsert_one()` per row, and the standalone `build-db` CLI command
batches through a whole CSV with a single transaction."""

from __future__ import annotations

import csv
import time
from pathlib import Path

from .. import db


def upsert_character(conn, csv_row: dict) -> dict | None:
    """Parse and upsert one character CSV row. Returns the parsed
    fields dict (suitable for downstream image/thumb steps) or None if
    the row was empty."""
    parsed = db.parse_character_row(csv_row)
    if parsed is None:
        return None
    fields, traits = parsed
    conn.execute(db.upsert_statement('characters', db.CSV_COLUMNS,
                                      'character'),
                 [fields[c] for c in db.CSV_COLUMNS])
    conn.execute('DELETE FROM traits WHERE character = ?',
                 (fields['character'],))
    if traits:
        conn.executemany(
            'INSERT INTO traits(character, facet, value, label) '
            'VALUES (?, ?, ?, ?)',
            [(fields['character'], fa, va, la) for (fa, va, la) in traits])
    return fields


def upsert_artist(conn, csv_row: dict) -> dict | None:
    fields = db.parse_artist_row(csv_row)
    if fields is None:
        return None
    conn.execute(db.upsert_statement('artists', db.ARTIST_CSV_COLUMNS,
                                      'artist'),
                 [fields[c] for c in db.ARTIST_CSV_COLUMNS])
    return fields


def upsert_one(conn, csv_row: dict, mode: str) -> dict | None:
    if mode == 'characters':
        return upsert_character(conn, csv_row)
    if mode == 'artists':
        return upsert_artist(conn, csv_row)
    raise ValueError(f'unknown mode {mode!r}')


def build_db(conn, csv_path: str | Path, mode: str) -> int:
    """Bulk ingest. Returns the count of rows upserted."""
    count = 0
    with conn:
        with open(csv_path, encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                if upsert_one(conn, row, mode) is not None:
                    count += 1
        db.set_meta(conn, f'{mode}_built_at',
                    time.strftime('%Y-%m-%d %H:%M:%S'))
    return count
