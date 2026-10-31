"""Small, durable JSON document store over SQLite for a single local workspace."""
import json
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get('SITEBRIEF_DATA_DIR') or Path(__file__).parent / 'data').resolve()

class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory or DATA_DIR).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / 'uploads').mkdir(exist_ok=True)
        self.path = self.directory / 'sitebrief.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE IF NOT EXISTS documents (collection TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(collection,id))')
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con
    def all(self, collection):
        with self.connect() as con:
            return [json.loads(r['body']) for r in con.execute('SELECT body FROM documents WHERE collection=? ORDER BY rowid', (collection,))]
    def get(self, collection, ident):
        with self.connect() as con:
            row = con.execute('SELECT body FROM documents WHERE collection=? AND id=?', (collection,ident)).fetchone()
            return json.loads(row['body']) if row else None
    def put(self, collection, item):
        self.batch([(collection,item)])
        return item
    def batch(self, items):
        with self.lock, self.connect() as con:
            for collection,item in items:
                con.execute('INSERT INTO documents(collection,id,body) VALUES(?,?,?) ON CONFLICT(collection,id) DO UPDATE SET body=excluded.body', (collection,item['id'],json.dumps(item,ensure_ascii=False)))
    def delete(self, collection, ident):
        with self.lock, self.connect() as con:
            con.execute('DELETE FROM documents WHERE collection=? AND id=?',(collection,ident))
