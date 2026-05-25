"""Admin login + message inbox.

Disabled (returns a "not configured" notice) when admin.password is
empty -- that's the public-repo default. A self-hoster who wants the
admin panel sets the password in config.toml (or the env override) and
restarts.
"""

from __future__ import annotations

from flask import (Blueprint, current_app, g, redirect, render_template,
                   request, session, url_for)

from .. import db
from ..auth import admin_required, check_admin_password
from ..routes.contact import REASON_LABELS

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    cfg = current_app.config['ANIMADEX']
    if request.method == 'GET':
        return render_template('admin_login.html', error=None)
    if not cfg.admin.password:
        return render_template(
            'admin_login.html',
            error='Admin is not configured -- set [admin].password in '
                  'config.toml (or ANIMADEX_ADMIN_PASSWORD) and restart.'
        ), 503
    if check_admin_password(request.form.get('username', ''),
                             request.form.get('password', '')):
        session['admin'] = True
        return redirect(url_for('admin.messages'))
    return render_template('admin_login.html',
                           error='Wrong username or password.'), 401


@admin_bp.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect(url_for('admin.login'))


@admin_bp.route('/messages')
@admin_required
def messages():
    rows = db.list_messages(g.db)
    return render_template('admin_messages.html', messages=rows,
                           reason_labels=REASON_LABELS)


@admin_bp.route('/messages/<int:msg_id>/delete', methods=['POST'])
@admin_required
def delete(msg_id):
    cfg = current_app.config['ANIMADEX']
    conn = db.connect_rw(cfg.paths.database)
    try:
        with conn:
            db.delete_message(conn, msg_id)
    finally:
        conn.close()
    return redirect(url_for('admin.messages'))
