"""Admin authentication helpers. The /admin views use these so the
session cookie key stays consistent.
"""

from __future__ import annotations

import functools
import hmac

from flask import current_app, redirect, session, url_for


def admin_required(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin.login'))
        return fn(*args, **kwargs)
    return wrapped


def check_admin_password(username: str, password: str) -> bool:
    cfg = current_app.config['ANIMADEX']
    expected_user = cfg.admin.username
    expected_pw = cfg.admin.password
    if not expected_pw:
        return False
    return (username == expected_user
            and hmac.compare_digest(password, expected_pw))
