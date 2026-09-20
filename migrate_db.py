import sqlite3
import os
from werkzeug.security import generate_password_hash

path = os.path.abspath('instance/database.db')
print('db', path)
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute('PRAGMA table_info(user)')
cols = [row[1] for row in cur.fetchall()]
print('columns', cols)
if 'password_hash' not in cols:
    print('adding password_hash column...')
    cur.execute('ALTER TABLE user ADD COLUMN password_hash VARCHAR(200)')
    cur.execute('SELECT id, password FROM user')
    rows = cur.fetchall()
    print('rows to migrate', len(rows))
    for row_id, password in rows:
        if password is not None:
            hashed = generate_password_hash(password)
            cur.execute('UPDATE user SET password_hash = ? WHERE id = ?', (hashed, row_id))
    conn.commit()
    print('migrated', len(rows), 'rows')
else:
    print('already migrated')
conn.close()
