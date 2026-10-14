"""PostgreSQL storage adapter; browser clients never access the private schema."""
import re
import sqlite3
from contextlib import contextmanager
import psycopg
import threading
import atexit
from psycopg_pool import ConnectionPool

_pools = {}
_pool_lock = threading.Lock()

def close_pools():
    for pool in list(_pools.values()):pool.close()
    _pools.clear()

atexit.register(close_pools)

class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

def row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    return lambda values: Row(zip(names, values))

def translate(sql):
    sql = re.sub(r"json_extract\((\w+(?:\.\w+)?),'\$\.(\w+)'\)", r"(\1::jsonb ->> '\2')", sql)
    sql = sql.replace('instr(', 'strpos(')
    ignore = 'INSERT OR IGNORE INTO' in sql
    sql = sql.replace('INSERT OR IGNORE INTO', 'INSERT INTO') + (' ON CONFLICT DO NOTHING' if ignore else '')
    if 'INSERT OR REPLACE INTO email_challenges' in sql:
        sql = sql.replace('INSERT OR REPLACE INTO', 'INSERT INTO') + ' ON CONFLICT(actor,purpose) DO UPDATE SET email=excluded.email,code=excluded.code,expires=excluded.expires,attempts=0,created=excluded.created'
    sql = sql.replace('SET views=views+1', 'SET views=work_metrics.views+1')
    return sql.replace('%', '%%').replace('?', '%s')

class Database:
    def __init__(self, connection):
        self.connection = connection;self.ready = False
    def setup(self):
        if not self.ready:
            self.connection.execute("SET LOCAL search_path TO huanchang,pg_catalog")
            self.connection.execute("SET LOCAL statement_timeout TO '12000ms'")
            self.ready = True
    def execute(self, sql, args=()):
        self.setup()
        if sql == 'BEGIN IMMEDIATE':sql = 'SELECT pg_advisory_xact_lock(1432897120)'
        try:return self.connection.execute(translate(sql), args, prepare=False)
        except psycopg.IntegrityError as error:raise sqlite3.IntegrityError('Database constraint rejected this change') from error
        except psycopg.Error as error:raise sqlite3.OperationalError('Database request failed') from error
    def commit(self):
        self.connection.commit();self.ready = False

@contextmanager
def connect(url):
    with _pool_lock:
        if url not in _pools:
            _pools[url] = ConnectionPool(url, min_size=0, max_size=2, timeout=8, open=True,
                kwargs={'connect_timeout':8,'prepare_threshold':None,'row_factory':row_factory},
                check=ConnectionPool.check_connection)
        pool = _pools[url]
    with pool.connection() as raw:
        yield Database(raw)
