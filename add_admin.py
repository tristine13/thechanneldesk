"""Seed an admin (replaces add_user.php).

Usage:  python add_admin.py "Abigail Patiluna" zhangkyuu@gmail.com
"""
import sys

import pymysql
from dotenv import load_dotenv

load_dotenv()
import os  # noqa: E402

if len(sys.argv) != 3:
    sys.exit('Usage: python add_admin.py "Abigail Patiluna" zhangkyuu@gmail.com')

name, email = sys.argv[1], sys.argv[2].lower()
conn = pymysql.connect(
    host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3307")),
    user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "channeldesk"), autocommit=True)
with conn.cursor() as cur:
    cur.execute("INSERT INTO users (email, role) VALUES (%s, 'admin') "
                "ON DUPLICATE KEY UPDATE role='admin'", (email,))
    cur.execute("INSERT INTO registrations (name, email, is_verified) VALUES (%s, %s, 1) "
                "ON DUPLICATE KEY UPDATE is_verified=1, name=VALUES(name)", (name, email))
print("Admin added:", email)
