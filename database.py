import os
import sqlite3
from contextlib import contextmanager

_CONN = None
_DB_URL = None

def init_db(url):
    global _CONN, _DB_URL
    _DB_URL = url
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):]
        _CONN = sqlite3.connect(path, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.execute("PRAGMA foreign_keys=ON")
        _schema_sqlite()
    elif url.startswith("postgres://") or url.startswith("postgresql://"):
        try:
            import psycopg
            from psycopg.rows import dict_row
            _CONN = psycopg.connect(url, autocommit=True, row_factory=dict_row)
            _schema_postgres()
        except ImportError as e:
            raise RuntimeError("Install psycopg[binary] for PostgreSQL") from e
    else:
        raise ValueError("DATABASE_URL must be sqlite:///... or postgresql://...")

def _schema_sqlite():
    _CONN.executescript("""
    CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, first_seen TEXT);
    CREATE TABLE IF NOT EXISTS deals(
      trade_id TEXT PRIMARY KEY, amount TEXT NOT NULL, fee TEXT NOT NULL, release_amount TEXT NOT NULL,
      fee_percent TEXT NOT NULL, fee_mode TEXT NOT NULL, buyer_name TEXT, seller_name TEXT,
      buyer_username TEXT, seller_username TEXT, buyer_id INTEGER, seller_id INTEGER, info TEXT,
      until_text TEXT, method TEXT, escrowed_by_id INTEGER NOT NULL, escrowed_by_username TEXT,
      status TEXT NOT NULL, created_at TEXT NOT NULL, closed_at TEXT, closed_by_id INTEGER,
      group_id INTEGER, card_message_id INTEGER);
    CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT NOT NULL, file_id TEXT NOT NULL, submitted_by INTEGER NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL, action TEXT NOT NULL, trade_id TEXT, details TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS user_sessions(user_id INTEGER PRIMARY KEY, state TEXT, data TEXT);
    """)
    _CONN.commit()

def _schema_postgres():
    with _CONN.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(user_id BIGINT PRIMARY KEY, username TEXT, full_name TEXT, first_seen TEXT);
        CREATE TABLE IF NOT EXISTS deals(
          trade_id TEXT PRIMARY KEY, amount TEXT NOT NULL, fee TEXT NOT NULL, release_amount TEXT NOT NULL,
          fee_percent TEXT NOT NULL, fee_mode TEXT NOT NULL, buyer_name TEXT, seller_name TEXT,
          buyer_username TEXT, seller_username TEXT, buyer_id BIGINT, seller_id BIGINT, info TEXT,
          until_text TEXT, method TEXT, escrowed_by_id BIGINT NOT NULL, escrowed_by_username TEXT,
          status TEXT NOT NULL, created_at TEXT NOT NULL, closed_at TEXT, closed_by_id BIGINT,
          group_id BIGINT, card_message_id BIGINT);
        CREATE TABLE IF NOT EXISTS evidence(id BIGSERIAL PRIMARY KEY, trade_id TEXT NOT NULL, file_id TEXT NOT NULL, submitted_by BIGINT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_log(id BIGSERIAL PRIMARY KEY, actor_id BIGINT NOT NULL, action TEXT NOT NULL, trade_id TEXT, details TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS user_sessions(user_id BIGINT PRIMARY KEY, state TEXT, data TEXT);
        """)

def db(sql, params=(), one=False):
    if _CONN is None:
        raise RuntimeError("Database not initialized")
    if _DB_URL.startswith("sqlite:///"):
        cur = _CONN.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        _CONN.commit()
    else:
        # Translate common SQLite placeholders to PostgreSQL placeholders.
        sql = sql.replace("?", "%s")
        with _CONN.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
    if one:
        return rows[0] if rows else None
    return rows

def get_connection():
    return _CONN
