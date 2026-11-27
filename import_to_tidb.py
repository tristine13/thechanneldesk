"""One-time helper: load a phpMyAdmin .sql export into TiDB Cloud.

Run:  python import_to_tidb.py
It asks for the host, user, password and the .sql file. Nothing is saved to disk.
"""
import getpass
import os
import re

import certifi
import pymysql
from pymysql.constants import CLIENT

host = input("TiDB host: ").strip()
port = int(input("Port [4000]: ").strip() or "4000")
user = input("User (looks like xxxx.root): ").strip()
password = getpass.getpass("Password (typing is hidden): ")
path = input("Path to channeldesk.sql: ").strip().strip('"')

with open(os.path.expanduser(path), "r", encoding="utf8") as f:
    sql = f.read()

# make the MariaDB / XAMPP export acceptable to TiDB
sql = sql.replace("latin1_swedish_ci", "utf8mb4_general_ci")
sql = re.sub(r"\blatin1\b", "utf8mb4", sql)                       # old latin1 tables -> utf8mb4
sql = re.sub(r"DELIMITER \$\$.*?DELIMITER ;", "", sql, flags=re.S)    # triggers are not supported
sql = re.sub(r"DROP TRIGGER IF EXISTS `[^`]*`;", "", sql)
sql = re.sub(r"/\*M!.*?\*/", "", sql, flags=re.S)                  # MariaDB-only comments

conn = pymysql.connect(host=host, port=port, user=user, password=password, database="channeldesk",
                       charset="utf8mb4", ssl={"ca": certifi.where()},
                       client_flag=CLIENT.MULTI_STATEMENTS, autocommit=True)
with conn.cursor() as cur:
    cur.execute(sql)                  # the server splits the statements itself, so ';' inside text is safe
    n = 1
    while cur.nextset():
        n += 1
print(f"Done: {n} statements ran.")
with conn.cursor() as cur:
    cur.execute("SHOW TABLES")
    for row in cur.fetchall():
        print(" -", list(row)[0])
conn.close()
