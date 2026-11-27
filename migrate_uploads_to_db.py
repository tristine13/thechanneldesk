"""One-time helper: copy the pictures in the local `uploads` folder into the database (table image_files).

Run:  python migrate_uploads_to_db.py
It asks for the TiDB host, user and password (nothing is saved). Safe to run more than once.
"""
import getpass
import mimetypes
import os

import certifi
import pymysql

host = input("TiDB host: ").strip()
port = int(input("Port [4000]: ").strip() or "4000")
user = input("User (looks like xxxx.root): ").strip()
password = getpass.getpass("Password (typing is hidden): ")
folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")

conn = pymysql.connect(host=host, port=port, user=user, password=password, database="channeldesk",
                       charset="utf8mb4", ssl={"ca": certifi.where()}, autocommit=True)
with conn.cursor() as cur:
    cur.execute("CREATE TABLE IF NOT EXISTS image_files (name VARCHAR(255) NOT NULL PRIMARY KEY, "
                "mime VARCHAR(100) NOT NULL, data MEDIUMBLOB NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    done = skipped = 0
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        if len(data) > 5 * 1024 * 1024:
            print("  skipped (over 5 MB):", name)
            skipped += 1
            continue
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        cur.execute("REPLACE INTO image_files (name, mime, data) VALUES (%s, %s, %s)", (name, mime, data))
        done += 1
        print("  copied:", name)
print(f"Done: {done} pictures copied, {skipped} skipped.")
conn.close()
