"""Contact form -- captcha + write to messages table."""

from __future__ import annotations

from flask import (Blueprint, abort, current_app, g, jsonify, request)

from .. import captcha, db

contact_bp = Blueprint('contact', __name__)


REASONS = {'lora_takedown', 'content', 'bug', 'feedback', 'other'}
REASON_LABELS = {
    'lora_takedown': 'LoRA takedown request',
    'content':       'Content concern',
    'bug':           'Bug report',
    'feedback':      'Feedback / suggestion',
    'other':         'Other',
}


def _contact_enabled():
    return current_app.config['ANIMADEX'].features.contact_enabled


@contact_bp.route('/api/contact/captcha')
def contact_captcha():
    if not _contact_enabled():
        abort(404)
    secret = current_app.config['ANIMADEX'].server.secret_key
    q, token, expires = captcha.make(secret)
    return jsonify(question=q, token=token, expires=expires)


@contact_bp.route('/api/contact', methods=['POST'])
def contact_submit():
    if not _contact_enabled():
        abort(404)
    data = request.get_json(silent=True) or {}
    if (data.get('honeypot') or '').strip():
        return jsonify(ok=True)
    reason = (data.get('reason') or '').strip()
    message = (data.get('message') or '').strip()
    if reason not in REASONS:
        return jsonify(ok=False, error='Pick a reason.'), 400
    if len(message) < 10:
        return jsonify(ok=False, error='Message is too short.'), 400
    if len(message) > 5000:
        return jsonify(ok=False, error='Message is too long.'), 400
    secret = current_app.config['ANIMADEX'].server.secret_key
    if not captcha.check(secret, data.get('answer'),
                          data.get('token'), data.get('expires')):
        return jsonify(ok=False,
                       error='Captcha failed -- please try again.'), 400
    ua = (request.headers.get('User-Agent') or '')[:300]
    db_path = current_app.config['ANIMADEX'].paths.database
    conn = db.connect_rw(db_path)
    try:
        with conn:
            ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip() \
                if current_app.config['ANIMADEX'].server.trust_proxy \
                else (request.remote_addr or '')
            db.add_message(conn, reason, message, ip, ua)
    finally:
        conn.close()
    return jsonify(ok=True)
