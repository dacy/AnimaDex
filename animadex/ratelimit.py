"""Per-IP sliding-window rate limit middleware.

Two windows per IP: a short burst and a longer sustained one. Either
tripping starts a configurable cool-off in which further requests get
HTTP 429 + Retry-After. Dev routes, /static, and localhost are exempt
so iteration is never throttled.

Configured from `[ratelimit]` in config.toml.
"""

from __future__ import annotations

import collections
import threading
import time

from flask import jsonify, request

from .config import RateLimit

_RATE_EXEMPT_IPS = frozenset({'127.0.0.1', '::1', 'localhost', ''})


def _parse_pair(spec: str, default_count: int, default_window: int) -> tuple[int, int]:
    try:
        n, s = spec.split('/')
        return int(n), int(s)
    except (ValueError, AttributeError):
        return default_count, default_window


class RateLimiter:
    """Reusable limiter. The Flask app builds one in app.py and wires
    it through `before_request`."""

    def __init__(self, cfg: RateLimit, *, trust_proxy: bool = False):
        self.enabled = cfg.enabled
        self.burst_count, self.burst_window = _parse_pair(
            cfg.burst, 150, 10)
        self.sustained_count, self.sustained_window = _parse_pair(
            cfg.sustained, 2000, 300)
        self.block_seconds = int(cfg.block_seconds)
        self.trust_proxy = trust_proxy
        self._lock = threading.Lock()
        self._hits: dict[str, collections.deque] = {}
        self._blocks: dict[str, float] = {}
        self._clean_next = 0.0

    def client_ip(self) -> str:
        if self.trust_proxy:
            fwd = request.headers.get('X-Forwarded-For', '')
            if fwd:
                return fwd.split(',')[0].strip()
        return request.remote_addr or ''

    def check(self):
        """Return a 429 response object if the caller should be blocked,
        or None to allow the request through."""
        if not self.enabled:
            return None
        path = request.path
        if path.startswith('/api/dev/') or path.startswith('/static/'):
            return None
        ip = self.client_ip()
        if ip in _RATE_EXEMPT_IPS:
            return None

        now = time.monotonic()
        retry_after = None
        log_msg = None

        with self._lock:
            until = self._blocks.get(ip)
            if until is not None:
                if until > now:
                    retry_after = until - now
                else:
                    self._blocks.pop(ip, None)

            if retry_after is None:
                dq = self._hits.setdefault(ip, collections.deque())
                dq.append(now)
                cutoff = now - self.sustained_window
                while dq and dq[0] < cutoff:
                    dq.popleft()
                burst_start = now - self.burst_window
                burst_hits = sum(1 for t in dq if t >= burst_start)
                sustained_hits = len(dq)
                if (burst_hits > self.burst_count
                        or sustained_hits > self.sustained_count):
                    self._blocks[ip] = now + self.block_seconds
                    dq.clear()
                    retry_after = self.block_seconds
                    log_msg = (
                        f'[rate-limit] {ip} blocked for {self.block_seconds}s '
                        f'(burst {burst_hits}/{self.burst_window}s, '
                        f'sustained {sustained_hits}/'
                        f'{self.sustained_window}s)')

            if now >= self._clean_next:
                self._clean_next = now + 60
                stale = [k for k, v in self._hits.items() if not v]
                for k in stale:
                    self._hits.pop(k, None)

        if log_msg:
            print(log_msg, flush=True)
        if retry_after is None:
            return None
        retry = max(1, int(retry_after))
        resp = jsonify(error='Too many requests', retry_after=retry)
        resp.status_code = 429
        resp.headers['Retry-After'] = str(retry)
        return resp
