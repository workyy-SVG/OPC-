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
    path = Path(os.environ.get('AI_QUOTA_DB', 'outputs/ai_usage.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute('CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, used INTEGER NOT NULL)')
    return connection


def usage():
    connection = connect()
    try:
        row = connection.execute('SELECT used FROM usage WHERE day=?', (today(),)).fetchone()
    finally:
        connection.close()
    used = row[0] if row else 0
    return used, max(0, LIMIT - used)


def reserve():
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
