"""HMAC-signed math captcha for the contact form.

The user gets a small arithmetic question + a signed token. Posting
back the answer with the matching token (and unexpired timestamp) is
the proof. Cheap, no third-party services, no JS required to solve.
"""

from __future__ import annotations

import hmac
import random
import time

CAPTCHA_TTL = 600  # seconds


def make(secret_key: str) -> tuple[str, str, int]:
    """Return (question_text, signed_token, expiry_unix)."""
    a = random.randint(2, 9)
    b = random.randint(1, 9)
    op = random.choice(('+', '-'))
    if op == '-' and b > a:
        a, b = b, a
    answer = a + b if op == '+' else a - b
    expires = int(time.time()) + CAPTCHA_TTL
    return f'{a} {op} {b}', _sign(secret_key, answer, expires), expires


def check(secret_key: str, answer, token, expires) -> bool:
    try:
        ans = int(answer)
        exp = int(expires)
    except (TypeError, ValueError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(_sign(secret_key, ans, exp), token or '')


def _sign(secret_key: str, answer: int, expires: int) -> str:
    msg = f'{int(answer)}:{int(expires)}'.encode()
    return hmac.new(secret_key.encode(), msg, 'sha256').hexdigest()
