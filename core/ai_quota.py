"""Atomic shared daily request budget for a single application instance."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LIMIT = 100


def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def connect():
    from .storage import secret
    path = Path(secret('AI_QUOTA_DB', 'outputs/ai_usage.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute('CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, used INTEGER NOT NULL)')
    return connection


def usage():
    from . import storage
    if storage.remote_enabled(): return storage.usage()
    connection = connect()
    try:
        row = connection.execute('SELECT used FROM usage WHERE day=?', (today(),)).fetchone()
    finally:
        connection.close()
    used = row[0] if row else 0
    return used, max(0, LIMIT - used)


def display_usage():
    """Reuse remote sidebar status for 30s; reservations always query storage."""
    from . import storage
    if not storage.remote_enabled():
        return usage()
    import time
    import streamlit as st
    now, date = time.monotonic(), today()
    cached = st.session_state.get('_quota_display')
    if cached and cached['day'] == date and now - cached['at'] < 30:
        if 'error' in cached:
            raise storage.StorageError(cached['error'])
        return cached['value']
    try:
        result = usage()
    except storage.StorageError as error:
        st.session_state['_quota_display'] = dict(day=date, at=time.monotonic(), error=str(error))
        raise
    st.session_state['_quota_display'] = dict(day=date, at=time.monotonic(), value=result)
    return result


def reserve():
    from . import storage
    if storage.remote_enabled(): return storage.reserve()
    connection = connect()
    try:
        connection.execute('BEGIN IMMEDIATE')
        date = today()
        connection.execute('INSERT OR IGNORE INTO usage VALUES (?,0)', (date,))
        cursor = connection.execute('UPDATE usage SET used=used+1 WHERE day=? AND used<?', (date,LIMIT))
        if cursor.rowcount != 1:
            raise ValueError('今日千问和DeepSeek合计100次额度已用完，北京时间零点重置；手动功能仍可使用')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

APP_REVISION = "purchase-submit-20261105"
