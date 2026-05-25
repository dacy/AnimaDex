"""CivitAI LoRA sync.

Pulls character LoRAs from CivitAI's public API, matches each to a
character slug already in the DB, and stores the result in `loras`.
Delta-syncs by default (only LoRAs published since the previous run
are fetched). Anonymous requests work but are rate-limited; setting
`civitai.api_key` raises the cap.

The matcher tokenises both the character slug and the LoRA's name/tags
into ASCII word sets and prefers an exact name match, falling back to
"a known character name appears inside the LoRA title."
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request

from .. import db
from ..config import Config

API_URL = 'https://civitai.com/api/v1/models'
ANIMA_LAUNCH = '2025-05-15'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/'
              '537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

_NOISE = {'anima', 'lora', 'loras', 'il', 'ilxl', 'illustrious', 'pony',
          'xl', 'sdxl', 'noob', 'noobai', 'edition', 'style', 'character',
          'oc', 'anime', 'the', 'of', 'and', 'for', 'from', 'ver',
          'version', 'outfit', 'outfits', 'skin', 'costume', 'model',
          'v1', 'v2', 'v3'}


def _api_get(params: dict, api_key: str) -> dict:
    url = API_URL + '?' + urllib.parse.urlencode(params)
    headers = {'User-Agent': USER_AGENT}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    req = urllib.request.Request(url, headers=headers)
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode('utf-8'))
        except (urllib.error.URLError, ValueError) as e:
            last = e
            time.sleep(2.5 * (attempt + 1))
    raise RuntimeError(f'CivitAI request failed: {last}')


# --- matcher -----------------------------------------------------------
def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    return [t for t in re.findall(r'[a-z0-9]+', text.lower())
            if len(t) > 1 and t not in _NOISE]


def _build_index(conn):
    multi, single, tok = {}, {}, {}
    for (slug,) in conn.execute('SELECT character FROM characters'):
        base = slug.replace('_', ' ').replace('(', ' ').replace(')', ' ')
        ws = frozenset(_tokens(base))
        if len(ws) >= 2:
            if ws not in multi:
                multi[ws] = slug
                for t in ws:
                    tok.setdefault(t, set()).add(ws)
        elif len(ws) == 1:
            single.setdefault(next(iter(ws)), slug)
    return multi, single, tok


def _candidates(lora):
    out = list(re.split(r'[/|()\[\]:;,]| - ', lora.get('name') or ''))
    out += lora.get('tags') or []
    for v in lora.get('modelVersions') or []:
        for tw in (v.get('trainedWords') or []):
            out += re.split(r'[,.]', tw)[:2]
            out += re.findall(r'\(([^)]+)\)', tw)
    return out


def _match_character(lora, idx):
    multi, single, tok = idx
    best = None
    for cand in _candidates(lora):
        ws = frozenset(_tokens(cand))
        if not ws:
            continue
        if len(ws) == 1:
            one = next(iter(ws))
            if one in single and best is None:
                best = (1, single[one])
            continue
        if ws in multi:
            return multi[ws]
        if len(ws) <= 6:
            seen = set()
            for t in ws:
                seen.update(tok.get(t, ()))
            for cw in seen:
                if cw <= ws and (best is None or len(cw) > best[0]):
                    best = (len(cw), multi[cw])
    return best[1] if best else None


def _latest_anima_version(lora):
    for v in lora.get('modelVersions') or []:
        if v.get('baseModel') == 'Anima' and v.get('publishedAt'):
            return v
    return None


def _version_thumb(version):
    imgs = version.get('images') or []
    sfw = [im for im in imgs if (im.get('nsfwLevel') or 99) <= 1]
    chosen = sfw or imgs
    if not chosen or not chosen[0].get('url'):
        return None
    return chosen[0]['url'].replace('original=true', 'width=200')


def sync(cfg: Config, *, full: bool = False) -> dict:
    """Run a (delta-)sync against CivitAI. Returns a summary dict."""
    conn = db.connect_rw(cfg.paths.database)
    try:
        idx = _build_index(conn)
        print(f'Indexed {len(idx[0]) + len(idx[1])} characters for '
              f'matching.', flush=True)

        cutoff = ANIMA_LAUNCH if full else (
            db.get_meta(conn, 'loras_synced_at') or ANIMA_LAUNCH)
        cutoff = cutoff[:10]
        print(f'Fetching Anima character LoRAs published since {cutoff} '
              f'...', flush=True)

        params = {'types': 'LORA', 'tag': 'character',
                  'baseModels': 'Anima', 'nsfw': 'false',
                  'sort': 'Newest', 'limit': 100}
        fetched = matched = 0
        rows, cursor = [], None
        for page in range(1, 1001):
            if cursor:
                params['cursor'] = cursor
            data = _api_get(params, cfg.civitai.api_key)
            items = data.get('items') or []
            if not items:
                break
            stop = False
            for lora in items:
                fetched += 1
                ver = _latest_anima_version(lora)
                if ver is None:
                    continue
                if (ver.get('publishedAt') or '')[:10] < cutoff:
                    stop = True
                    continue
                slug = _match_character(lora, idx)
                if not slug:
                    continue
                matched += 1
                rows.append((
                    slug, lora['id'], lora.get('name') or '?',
                    f"https://civitai.com/models/{lora['id']}",
                    _version_thumb(ver), ver.get('publishedAt')))
            cursor = (data.get('metadata') or {}).get('nextCursor')
            print(f'  page {page}: scanned {fetched}, matched {matched}',
                  flush=True)
            if stop or not cursor:
                break
            time.sleep(0.6)

        if rows:
            db.store_loras(conn, rows)
        db.set_meta(conn, 'loras_synced_at',
                    time.strftime('%Y-%m-%d'))
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM loras').fetchone()[0]
        chars = conn.execute(
            'SELECT COUNT(DISTINCT character) FROM loras').fetchone()[0]
        print(f'\nDone. scanned={fetched} matched={matched} '
              f'loras in DB={total} characters with a LoRA={chars}')
        return {'fetched': fetched, 'matched': matched,
                'total_loras': total, 'characters_with_loras': chars}
    finally:
        conn.close()
