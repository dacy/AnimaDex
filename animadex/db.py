"""SQLite layer for AnimaDex.

Schema is in `schema.sql` (kept separate so it can be applied via
sqlite3's executescript or hand-inspected). Everything here is plain
SQL via the stdlib `sqlite3` module -- no ORM, no migrations framework.

A `_migrate()` step on open adds columns introduced after a database was
first created, so users upgrading from an older AnimaDex don't have to
drop and rebuild.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

# --- tag classification (the danbooru core_tags vocabulary) -------------
ILLEGAL_FS_CHARS = '<>:"/\\|?*'

HAIR_COLOR_TAGS = {
    'aqua hair', 'black hair', 'blonde hair', 'blue hair', 'brown hair',
    'dark blue hair', 'gradient hair', 'green hair', 'grey hair',
    'light blue hair', 'light brown hair', 'light green hair',
    'light purple hair', 'multicolored hair', 'orange hair', 'pink hair',
    'purple hair', 'red hair', 'silver hair', 'split-color hair',
    'streaked hair', 'two-tone hair', 'white hair',
}
HAIR_LENGTH_ORDER = ('very short hair', 'short hair', 'medium hair',
                     'long hair', 'very long hair', 'absurdly long hair')
HAIR_LENGTH_TAGS = set(HAIR_LENGTH_ORDER)
EYE_COLOR_TAGS = {
    'aqua eyes', 'black eyes', 'blue eyes', 'brown eyes', 'gradient eyes',
    'green eyes', 'grey eyes', 'multicolored eyes', 'orange eyes',
    'pink eyes', 'purple eyes', 'red eyes', 'two-tone eyes', 'yellow eyes',
}
GENDER_TAGS = {'1boy': 'Male', '1girl': 'Female', '1other': 'Ambiguous',
               'no humans': 'Non-Human'}

CHARACTER_FACETS = ('character', 'copyright', 'hair_color', 'hair_length',
                    'eye_color', 'gender')
ARTIST_FACETS = ('artist', 'score', 'category')
FACET_LABELS = {
    'character': 'Character', 'copyright': 'Copyright',
    'hair_color': 'Hair Color', 'hair_length': 'Hair Length',
    'eye_color': 'Eye Color', 'gender': 'Gender', 'artist': 'Artist',
    'score': 'Score', 'category': 'Classifications',
}
_TRAIT_FACETS = ('hair_color', 'hair_length', 'eye_color', 'gender')
_HAIR_LENGTH_RANK = {t: i for i, t in enumerate(HAIR_LENGTH_ORDER)}

SCORE_BUCKETS = (
    ('5', '50% and up', 0.50, 1.01),
    ('4', '40 - 50%',   0.40, 0.50),
    ('3', '30 - 40%',   0.30, 0.40),
    ('2', '20 - 30%',   0.20, 0.30),
    ('1', 'Under 20%',  0.00, 0.20),
)

CSV_COLUMNS = ('character', 'copyright', 'name', 'name_lower',
               'copyright_name', 'trigger', 'core_tags', 'count', 'url',
               'imgname', 'thumbname', 'search_blob')
ARTIST_CSV_COLUMNS = ('artist', 'name', 'name_lower', 'trigger', 'count',
                      'url', 'imgname', 'thumbname', 'search_blob')

_CHAR_RESULT = ('character', 'copyright', 'name', 'copyright_name',
                'trigger', 'core_tags', 'count', 'url',
                'imgname', 'thumbname', 'image_version')
_ARTIST_RESULT = ('artist', 'name', 'trigger', 'count', 'url', 'score',
                  'imgname', 'thumbname', 'image_version')


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


# --- CSV -> row derivation ---------------------------------------------
def sanitize_filename(name: str) -> str:
    """Reproduce the on-disk filename format the pipeline uses."""
    cleaned = ''.join('_' if c in ILLEGAL_FS_CHARS else c for c in name)
    return cleaned.rstrip(' .') or 'unnamed'


def _cap_word(word: str) -> str:
    for i, ch in enumerate(word):
        if ch.isalpha():
            return word[:i] + ch.upper() + word[i + 1:]
    return word


def titlecase(text: str) -> str:
    return ' '.join(_cap_word(w) for w in text.split(' '))


def _trait_label(tag: str) -> str:
    return titlecase(tag.rsplit(' ', 1)[0])


def parse_character_row(row: dict) -> tuple[dict, list] | None:
    """Turn a character CSV row into (fields, traits), or None if empty."""
    character = (row.get('character') or '').strip()
    if not character:
        return None
    copyright_ = (row.get('copyright') or '').strip()
    trigger = (row.get('trigger') or '').strip()
    core = (row.get('core_tags') or '').strip()

    if ', ' in trigger:
        nm, cp = trigger.split(', ', 1)
    else:
        nm, cp = trigger, ''
    name = titlecase(nm) if nm else titlecase(character.replace('_', ' '))
    copyright_name = (titlecase(cp) if cp
                      else titlecase(copyright_.replace('_', ' ')))
    try:
        count = int(row.get('count') or 0)
    except ValueError:
        count = 0
    stem = sanitize_filename(trigger)

    traits = []
    for t in (t.strip() for t in core.split(',')):
        if not t:
            continue
        if t in HAIR_COLOR_TAGS:
            traits.append(('hair_color', t, _trait_label(t)))
        elif t in HAIR_LENGTH_TAGS:
            traits.append(('hair_length', t, _trait_label(t)))
        elif t in EYE_COLOR_TAGS:
            traits.append(('eye_color', t, _trait_label(t)))
        elif t in GENDER_TAGS:
            traits.append(('gender', t, GENDER_TAGS[t]))

    fields_ = {
        'character': character,
        'copyright': copyright_,
        'name': name,
        'name_lower': name.lower(),
        'copyright_name': copyright_name,
        'trigger': trigger,
        'core_tags': core,
        'count': count,
        'url': (row.get('url') or '').strip(),
        'imgname': stem + '.png',
        'thumbname': stem + '.webp',
        'search_blob': ' '.join((character, copyright_, trigger,
                                 core)).lower(),
    }
    return fields_, traits


def parse_artist_row(row: dict) -> dict | None:
    artist = (row.get('artist') or '').strip()
    if not artist:
        return None
    trigger = (row.get('trigger') or '').strip() \
        or artist.replace('_', ' ')
    name = titlecase(trigger)
    try:
        count = int(row.get('count') or 0)
    except ValueError:
        count = 0
    stem = sanitize_filename(trigger)
    return {
        'artist': artist,
        'name': name,
        'name_lower': name.lower(),
        'trigger': trigger,
        'count': count,
        'url': (row.get('url') or '').strip(),
        'imgname': stem + '.png',
        'thumbname': stem + '.webp',
        'search_blob': ' '.join((artist, trigger)).lower(),
    }


def upsert_statement(table: str, columns: tuple, key_col: str) -> str:
    """INSERT ... ON CONFLICT that refreshes only the given (CSV) columns."""
    placeholders = ','.join('?' * len(columns))
    updates = ', '.join(f'{c}=excluded.{c}' for c in columns
                        if c != key_col)
    return (f'INSERT INTO {table} ({",".join(columns)}) '
            f'VALUES ({placeholders}) '
            f'ON CONFLICT({key_col}) DO UPDATE SET {updates}')


# --- connections -------------------------------------------------------
def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a DB was first created. Cheap: each
    PRAGMA returns the current schema and we only ALTER if a column is
    actually missing."""
    char_cols = {r[1] for r in conn.execute('PRAGMA table_info(characters)')}
    if 'image_version' not in char_cols:
        conn.execute('ALTER TABLE characters ADD COLUMN '
                     'image_version INTEGER NOT NULL DEFAULT 0')
    artist_cols = {r[1] for r in conn.execute('PRAGMA table_info(artists)')}
    if 'score' not in artist_cols:
        conn.execute('ALTER TABLE artists ADD COLUMN score REAL')
    if 'image_version' not in artist_cols:
        conn.execute('ALTER TABLE artists ADD COLUMN '
                     'image_version INTEGER NOT NULL DEFAULT 0')


def _apply_schema(conn: sqlite3.Connection) -> None:
    with open(_SCHEMA_PATH, encoding='utf-8') as f:
        conn.executescript(f.read())


def connect_rw(path: str | Path) -> sqlite3.Connection:
    """Open a writable connection and ensure the schema exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA synchronous = NORMAL')
    _apply_schema(conn)
    _migrate(conn)
    return conn


def connect_ro(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 3000')
    conn.execute('PRAGMA query_only = 1')
    return conn


# --- queries -----------------------------------------------------------
def _like(term: str) -> str:
    term = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + term + '%'


def parse_query(q: str) -> list[str]:
    return [t.strip().lower() for t in q.split(',') if t.strip()]


def count_characters(conn) -> int:
    return conn.execute('SELECT COUNT(*) FROM characters').fetchone()[0]


def count_artists(conn) -> int:
    return conn.execute('SELECT COUNT(*) FROM artists').fetchone()[0]


def count_copyrights(conn) -> int:
    return conn.execute(
        'SELECT COUNT(DISTINCT copyright) FROM characters').fetchone()[0]


def get_character(conn, slug):
    return conn.execute(
        'SELECT imgname, thumbname, image_version FROM characters '
        'WHERE character = ?', (slug,)).fetchone()


def get_artist(conn, slug):
    return conn.execute(
        'SELECT imgname, thumbname, image_version FROM artists '
        'WHERE artist = ?', (slug,)).fetchone()


def character_detail(conn, slug):
    """Full row for the /c/<slug> SEO landing page."""
    return conn.execute(
        'SELECT character, copyright, name, copyright_name, trigger, '
        '       core_tags, count, url, imgname, thumbname, image_version '
        'FROM characters WHERE character = ?', (slug,)).fetchone()


def all_character_slugs(conn):
    """For sitemap generation."""
    return conn.execute(
        'SELECT character AS slug, image_version FROM characters '
        'ORDER BY count DESC, name_lower').fetchall()


def copyright_version(conn, filename: str) -> int:
    row = conn.execute(
        'SELECT version FROM copyright_versions WHERE filename = ?',
        (filename,)).fetchone()
    return int(row[0]) if row else 0


def _seed(value):
    try:
        s = int(value or 0)
    except (TypeError, ValueError):
        s = 0
    return ((s * 2654435761 + 1) % 2147483647) or 1


SHUFFLE_ORDER = '((rowid * ?) % 2147483647)'
SHUFFLE_GROUP_ORDER = '((MIN(rowid) * ?) % 2147483647)'


def search_characters(conn, q='', sort='count', filters=None,
                      page=1, page_size=72, seed=None):
    filters = filters or {}
    where, params = [], []

    for token in parse_query(q):
        where.append("search_blob LIKE ? ESCAPE '\\'")
        params.append(_like(token))

    for column in ('character', 'copyright'):
        chosen = filters.get(column)
        if chosen:
            where.append(f'{column} IN ({",".join("?" * len(chosen))})')
            params.extend(chosen)

    for facet in _TRAIT_FACETS:
        chosen = filters.get(facet)
        if chosen:
            where.append(
                'character IN (SELECT character FROM traits '
                f'WHERE facet = ? AND value IN '
                f'({",".join("?" * len(chosen))}))')
            params.append(facet)
            params.extend(chosen)

    if filters.get('loras'):
        where.append('character IN (SELECT character FROM loras)')

    wsql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = conn.execute(
        'SELECT COUNT(*) FROM characters' + wsql, params).fetchone()[0]

    if sort == 'random':
        order, sort_params = SHUFFLE_ORDER, [_seed(seed)]
    elif sort == 'az':
        order, sort_params = 'name_lower', []
    else:
        order, sort_params = 'count DESC, name_lower', []
    rows = conn.execute(
        f'SELECT {",".join(_CHAR_RESULT)} FROM characters{wsql} '
        f'ORDER BY {order} LIMIT ? OFFSET ?',
        params + sort_params + [page_size,
                                 (page - 1) * page_size]).fetchall()
    return total, rows


def search_artists(conn, q='', sort='count', filters=None,
                   page=1, page_size=72, seed=None):
    filters = filters or {}
    where, params = [], []

    for token in parse_query(q):
        where.append("search_blob LIKE ? ESCAPE '\\'")
        params.append(_like(token))

    chosen = filters.get('artist')
    if chosen:
        where.append(f'artist IN ({",".join("?" * len(chosen))})')
        params.extend(chosen)

    score_sel = filters.get('score')
    if score_sel:
        ranges = [b for b in SCORE_BUCKETS if b[0] in score_sel]
        if ranges:
            where.append('(' + ' OR '.join(
                '(score >= ? AND score < ?)' for _ in ranges) + ')')
            for _, _, lo, hi in ranges:
                params += [lo, hi]

    cat_sel = filters.get('category')
    if cat_sel:
        where.append(
            'artist IN (SELECT artist FROM artist_categories '
            f'WHERE category IN ({",".join("?" * len(cat_sel))}))')
        params.extend(cat_sel)

    wsql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = conn.execute(
        'SELECT COUNT(*) FROM artists' + wsql, params).fetchone()[0]

    if sort == 'random':
        order, sort_params = SHUFFLE_ORDER, [_seed(seed)]
    elif sort == 'az':
        order, sort_params = 'name_lower', []
    elif sort == 'score':
        order, sort_params = 'score DESC, name_lower', []
    else:
        order, sort_params = 'count DESC, name_lower', []
    rows = conn.execute(
        f'SELECT {",".join(_ARTIST_RESULT)} FROM artists{wsql} '
        f'ORDER BY {order} LIMIT ? OFFSET ?',
        params + sort_params + [page_size,
                                 (page - 1) * page_size]).fetchall()
    return total, rows


def _row_facet(conn, table, value_col, where, params, limit):
    total = conn.execute(
        f'SELECT COUNT(*) FROM {table}{where}', params).fetchone()[0]
    rows = conn.execute(
        f'SELECT {value_col} AS value, name AS label, count AS n '
        f'FROM {table}{where} ORDER BY n DESC, label LIMIT ?',
        params + [limit]).fetchall()
    return total, rows


def character_facet_values(conn, name, q='', limit=30):
    if name not in CHARACTER_FACETS:
        return None
    q = q.strip().lower()
    if name == 'character':
        where, params = '', []
        if q:
            where = (" WHERE (name_lower LIKE ? ESCAPE '\\' "
                     "OR character LIKE ? ESCAPE '\\')")
            params = [_like(q), _like(q)]
        total, rows = _row_facet(conn, 'characters', 'character',
                                 where, params, limit)
    elif name == 'copyright':
        where, params = '', []
        if q:
            where = (" WHERE (copyright LIKE ? ESCAPE '\\' "
                     "OR copyright_name LIKE ? ESCAPE '\\')")
            params = [_like(q), _like(q)]
        total = conn.execute(
            'SELECT COUNT(DISTINCT copyright) FROM characters' + where,
            params).fetchone()[0]
        rows = conn.execute(
            'SELECT copyright AS value, copyright_name AS label, '
            'COUNT(*) AS n FROM characters' + where +
            ' GROUP BY copyright ORDER BY n DESC, label LIMIT ?',
            params + [limit]).fetchall()
    else:
        where, params = 'WHERE facet = ?', [name]
        if q:
            where += (" AND (value LIKE ? ESCAPE '\\' "
                      "OR label LIKE ? ESCAPE '\\')")
            params += [_like(q), _like(q)]
        total = conn.execute(
            'SELECT COUNT(DISTINCT value) FROM traits ' + where,
            params).fetchone()[0]
        rows = conn.execute(
            'SELECT value, label, COUNT(*) AS n FROM traits ' + where +
            ' GROUP BY value, label ORDER BY n DESC, label LIMIT ?',
            params + [limit]).fetchall()

    values = [{'value': r['value'], 'label': r['label'], 'count': r['n']}
              for r in rows]
    if name == 'hair_length':
        values.sort(key=lambda v: _HAIR_LENGTH_RANK.get(v['value'], 99))
    return {'label': FACET_LABELS[name], 'total': total, 'values': values}


def artist_facet_values(conn, name, q='', limit=30):
    if name == 'score':
        values = []
        for value, label, lo, hi in SCORE_BUCKETS:
            n = conn.execute(
                'SELECT COUNT(*) FROM artists '
                'WHERE score >= ? AND score < ?',
                (lo, hi)).fetchone()[0]
            values.append({'value': value, 'label': label, 'count': n})
        return {'label': FACET_LABELS['score'],
                'total': len(values), 'values': values}
    if name == 'category':
        rows = conn.execute(
            'SELECT c.name, (SELECT COUNT(*) FROM artist_categories ac '
            'WHERE ac.category = c.name) AS n '
            'FROM categories c ORDER BY c.name').fetchall()
        values = [{'value': r[0], 'label': r[0], 'count': r[1]}
                  for r in rows]
        return {'label': FACET_LABELS['category'],
                'total': len(values), 'values': values}
    if name != 'artist':
        return None
    q = q.strip().lower()
    where, params = '', []
    if q:
        where = (" WHERE (name_lower LIKE ? ESCAPE '\\' "
                 "OR artist LIKE ? ESCAPE '\\')")
        params = [_like(q), _like(q)]
    total, rows = _row_facet(conn, 'artists', 'artist',
                              where, params, limit)
    values = [{'value': r['value'], 'label': r['label'], 'count': r['n']}
              for r in rows]
    return {'label': FACET_LABELS['artist'], 'total': total,
            'values': values}


def copyright_exists(conn, slug):
    return conn.execute(
        'SELECT 1 FROM characters WHERE copyright = ? LIMIT 1',
        (slug,)).fetchone() is not None


def search_copyrights(conn, q='', sort='count', page=1, page_size=72,
                      seed=None):
    conds, params = [], []
    for token in parse_query(q):
        conds.append("(copyright LIKE ? ESCAPE '\\' "
                     "OR copyright_name LIKE ? ESCAPE '\\')")
        params += [_like(token), _like(token)]
    where = (' WHERE ' + ' AND '.join(conds)) if conds else ''
    total = conn.execute(
        'SELECT COUNT(DISTINCT copyright) FROM characters' + where,
        params).fetchone()[0]
    if sort == 'random':
        order, sort_params = SHUFFLE_GROUP_ORDER, [_seed(seed)]
    elif sort == 'az':
        order, sort_params = 'label', []
    else:
        order, sort_params = 'n DESC, label', []
    rows = conn.execute(
        'SELECT copyright AS value, copyright_name AS label, '
        'COUNT(*) AS n FROM characters' + where +
        ' GROUP BY copyright ORDER BY ' + order + ' LIMIT ? OFFSET ?',
        params + sort_params + [page_size,
                                 (page - 1) * page_size]).fetchall()
    return total, rows


# --- artist categories -------------------------------------------------
def add_category(conn, name):
    conn.execute('INSERT OR IGNORE INTO categories(name) VALUES (?)',
                 (name,))


def remove_category(conn, name):
    conn.execute('DELETE FROM artist_categories WHERE category = ?',
                 (name,))
    conn.execute('DELETE FROM categories WHERE name = ?', (name,))


def category_members(conn, name):
    return [r[0] for r in conn.execute(
        'SELECT artist FROM artist_categories WHERE category = ?',
        (name,))]


def toggle_category(conn, artist, category):
    if conn.execute(
            'SELECT 1 FROM artist_categories '
            'WHERE artist = ? AND category = ?',
            (artist, category)).fetchone():
        conn.execute(
            'DELETE FROM artist_categories '
            'WHERE artist = ? AND category = ?', (artist, category))
        return False
    conn.execute('INSERT OR IGNORE INTO categories(name) VALUES (?)',
                 (category,))
    conn.execute(
        'INSERT INTO artist_categories(artist, category) VALUES (?, ?)',
        (artist, category))
    return True


# --- meta + LoRAs ------------------------------------------------------
def get_meta(conn, key, default=None):
    row = conn.execute('SELECT value FROM meta WHERE key = ?',
                       (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
    conn.execute(
        'INSERT INTO meta(key, value) VALUES(?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, str(value)))


def store_loras(conn, rows):
    conn.executemany(
        'INSERT OR REPLACE INTO '
        'loras(character, model_id, name, url, thumb, published) '
        'VALUES (?, ?, ?, ?, ?, ?)', rows)


def loras_for(conn, slugs):
    slugs = list(slugs)
    if not slugs:
        return {}
    out = {}
    qs = ','.join('?' * len(slugs))
    for r in conn.execute(
            'SELECT character, name, url, thumb FROM loras '
            f'WHERE character IN ({qs}) ORDER BY published DESC, name',
            slugs):
        out.setdefault(r['character'], []).append(
            {'name': r['name'], 'url': r['url'], 'thumb': r['thumb']})
    return out


# --- contact messages --------------------------------------------------
def add_message(conn, reason, message, ip, user_agent):
    conn.execute(
        'INSERT INTO messages(created_at, reason, message, ip, user_agent) '
        "VALUES(strftime('%Y-%m-%d %H:%M:%S', 'now'), ?, ?, ?, ?)",
        (reason, message, ip, user_agent))


def list_messages(conn):
    return conn.execute(
        'SELECT id, created_at, reason, message, ip, user_agent '
        'FROM messages ORDER BY id DESC').fetchall()


def delete_message(conn, msg_id):
    conn.execute('DELETE FROM messages WHERE id = ?', (msg_id,))


# --- pipeline helpers (version bumps + lookups) ------------------------
def bump_character_version(conn, slug: str, version: int | None = None) -> int:
    """Stamp characters.image_version, raising it to `version` (default
    = current epoch). Returns the value written."""
    v = int(version if version is not None else time.time())
    conn.execute(
        'UPDATE characters '
        'SET image_version = MAX(image_version, ?) WHERE character = ?',
        (v, slug))
    return v


def bump_artist_version(conn, slug: str, version: int | None = None) -> int:
    v = int(version if version is not None else time.time())
    conn.execute(
        'UPDATE artists '
        'SET image_version = MAX(image_version, ?) WHERE artist = ?',
        (v, slug))
    return v


def bump_copyright_version(conn, filename: str,
                           version: int | None = None) -> int:
    """Upsert the copyright_versions side-table."""
    v = int(version if version is not None else time.time())
    conn.execute(
        'INSERT INTO copyright_versions(filename, version) VALUES (?, ?) '
        'ON CONFLICT(filename) DO UPDATE SET '
        'version = MAX(copyright_versions.version, excluded.version)',
        (filename, v))
    return v


def set_artist_score(conn, slug: str, score: float) -> None:
    conn.execute('UPDATE artists SET score = ? WHERE artist = ?',
                 (score, slug))


def score_missing(conn, slug: str) -> bool:
    row = conn.execute(
        'SELECT score FROM artists WHERE artist = ?', (slug,)).fetchone()
    return row is None or row[0] is None
