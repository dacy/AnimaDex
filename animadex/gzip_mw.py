"""Gzip middleware -- compress text payloads to cut bandwidth.

Skip already-compressed images/fonts, skip tiny payloads where the
gzip header overhead outweighs the saving, and skip when the client
didn't ask for it. Sets `Vary: Accept-Encoding` so downstream caches
key correctly.
"""

from __future__ import annotations

import gzip

from flask import request

GZIP_TYPES = {'text/html', 'text/css', 'text/javascript',
              'application/javascript', 'application/json',
              'image/svg+xml'}


def compress_response(resp):
    if 'gzip' not in request.headers.get('Accept-Encoding', ''):
        return resp
    ctype = (resp.content_type or '').split(';')[0].strip()
    if ctype not in GZIP_TYPES or resp.headers.get('Content-Encoding'):
        return resp
    resp.direct_passthrough = False
    body = resp.get_data()
    if len(body) < 500:
        return resp
    packed = gzip.compress(body, 6)
    resp.set_data(packed)
    resp.headers['Content-Encoding'] = 'gzip'
    resp.headers['Content-Length'] = str(len(packed))
    resp.headers['Vary'] = 'Accept-Encoding'
    return resp
