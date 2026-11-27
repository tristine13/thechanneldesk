"""MySQL / MariaDB helpers (XAMPP). One connection per request.

Login comes from DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME in .env.
"""
import os

import pymysql
from flask import g
from pymysql.cursors import DictCursor


def get_db():
    if "db" not in g:
        g.db = pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "channeldesk"),
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
        )
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else list(rows)


def execute(sql, args=()):
    """Run INSERT/UPDATE/DELETE and return the new row id (for inserts)."""
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid
