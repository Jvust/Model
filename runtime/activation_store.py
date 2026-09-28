"""Durable local workflow journal. No OAuth credentials or runtime handles persisted.

Recovery is at completed-task boundaries, NOT at a diffusion denoising step.
The selected source must be re-authorized/re-scanned before an interrupted job
is resumed. SQLite lives beside the D:\\Model cache, never in a Drive source.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

STATES = {'queued', 'preparing', 'running', 'complete', 'ready', 'failed', 'cancelled', 'interrupted'}
ACTIVE = {'queued', 'preparing', 'running'}
SENSITIVE = {'access_token', 'refresh_token', 'authorization', 'cookie', 'api_key', 'password',
             'remote_token', 'resource_key', 'resourceKey', 'headers'}


def clean(value, depth=0):
    if depth > 24:
        raise ValueError('Task input is too deeply nested')
    if isinstance(value, dict):
        return {str(k): clean(v, depth+1) for k, v in value.items() if str(k).casefold() not in {x.casefold() for x in SENSITIVE}}
    if isinstance(value, list):
        return [clean(x, depth+1) for x in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError('Task input must contain JSON values only')


def encode(value):
    text = json.dumps(clean(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(text.encode()) > 16_000_000:
        raise ValueError('Saved task exceeds 16 MB')
    return text


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def check_id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('Invalid activation job ID')
    return value


class ActivationStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'lifecycle.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, identity TEXT NOT NULL, label TEXT NOT NULL,
                    route TEXT NOT NULL, request TEXT NOT NULL, request_hash TEXT NOT NULL,
                    request_key TEXT UNIQUE, state TEXT NOT NULL, cursor INTEGER NOT NULL,
                    total INTEGER NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL,
                    updated REAL NOT NULL, attempt INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS results (
                    job_id TEXT NOT NULL, step INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(job_id, step));
                CREATE TABLE IF NOT EXISTS workspace (
                    identity TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=20000')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        """Call once when a new owning Runtime starts; never from a GET request."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='interrupted', detail='Runtime 已重启；已完成项保留，请重新连接同一来源后接续', updated=? WHERE state IN ('queued','preparing','running')", (time.time(),))

    def create(self, identity, label, route, request, request_key=None):
        if not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('Invalid verified model identity')
        text = encode(request)
        request_hash = digest({'identity': identity, 'request': json.loads(text), 'route': route})
        items = request.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError('A job needs 1..100 task items')
        if request_key is not None and (not isinstance(request_key, str) or not re.fullmatch('[a-zA-Z0-9_-]{8,128}', request_key)):
            raise ValueError('Invalid idempotency key')
        now, job_id = time.time(), uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if request_key:
                row = db.execute('SELECT id,request_hash FROM jobs WHERE request_key=?', (request_key,)).fetchone()
                if row:
                    if row['request_hash'] != request_hash:
                        raise ValueError('Idempotency key reused with different task inputs')
                    return self.get(row['id'])
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (job_id, identity, str(label)[:512], route, text, request_hash, request_key,
                        'queued', 0, len(items), '', now, now, 0))
        return self.get(job_id)

    def get(self, job_id, private=False, with_results=True):
        check_id(job_id)
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError('Activation job not found')
            result = dict(row)
            result['results'] = [{**json.loads(x['body']), 'step': x['step']} for x in db.execute('SELECT step,body FROM results WHERE job_id=? ORDER BY step', (job_id,))] if with_results else []
        if private:
            result['request'] = json.loads(result['request'])
        else:
            result.pop('request', None)
        result.pop('request_hash', None)
        result.pop('request_key', None)
        result['can_resume'] = result['state'] in {'failed', 'cancelled', 'interrupted'} and result['cursor'] < result['total']
        result['resume_unit'] = 'task'
        return result

    def by_request_key(self, key):
        if not isinstance(key, str):
            return None
        with self.connect() as db:
            row = db.execute('SELECT id FROM jobs WHERE request_key=?', (key,)).fetchone()
        return self.get(row['id']) if row else None

    def history(self, limit=30):
        limit = max(1, min(100, int(limit)))
        with self.connect() as db:
            ids = [row['id'] for row in db.execute('SELECT id FROM jobs ORDER BY updated DESC LIMIT ?', (limit,))]
        return [self.get(job_id, with_results=False) for job_id in ids]

    def claim(self, job_id, identity):
        check_id(job_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state,identity,cursor,total FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError('Activation job not found')
            if row['identity'] != identity:
                raise ValueError('模型来源或文件版本已变化；请创建新任务，不混用旧进度')
            if row['state'] in {'preparing', 'running', 'complete', 'ready'} or row['cursor'] >= row['total']:
                raise RuntimeError('Job cannot be claimed in its current state')
            other = db.execute("SELECT id FROM jobs WHERE state IN ('preparing','running') AND id!=? LIMIT 1", (job_id,)).fetchone()
            if other:
                raise RuntimeError('Another activation is already active')
            db.execute("UPDATE jobs SET state='preparing',detail='',attempt=attempt+1,updated=? WHERE id=?", (time.time(), job_id))
        return self.get(job_id, private=True)

    def update(self, job_id, state, detail=''):
        check_id(job_id)
        if state not in STATES:
            raise ValueError('Invalid lifecycle state')
        with self.connect() as db:
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError('Activation job not found')
            if row['state'] in {'complete', 'ready'} and state != row['state']:
                raise ValueError('Completed jobs are immutable')
            db.execute('UPDATE jobs SET state=?,detail=?,updated=? WHERE id=?', (state, str(detail)[:2000], time.time(), job_id))

    def checkpoint(self, job_id, step, result, ready=False):
        check_id(job_id)
        body = encode(result)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT cursor,total,state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError('Activation job not found')
            old = db.execute('SELECT body FROM results WHERE job_id=? AND step=?', (job_id, step)).fetchone()
            if old:
                if old['body'] != body:
                    raise ValueError('Cannot overwrite a committed result')
                return
            if row['state'] not in {'preparing', 'running'} or step != row['cursor']:
                raise ValueError('Out-of-order checkpoint')
            cursor = step + 1
            state = ('ready' if ready else 'complete') if cursor == row['total'] else 'running'
            db.execute('INSERT INTO results VALUES (?,?,?)', (job_id, step, body))
            db.execute('UPDATE jobs SET cursor=?,state=?,detail=?,updated=? WHERE id=?', (cursor, state, f'{cursor}/{row["total"]}', time.time(), job_id))

    def save_workspace(self, identity, value):
        if not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('Invalid model identity')
        body = encode(value)
        if len(body.encode()) > 2_000_000:
            raise ValueError('Workspace exceeds 2 MB')
        with self.connect() as db:
            db.execute('INSERT INTO workspace VALUES (?,?,?) ON CONFLICT(identity) DO UPDATE SET body=excluded.body,updated=excluded.updated', (identity, body, time.time()))

    def load_workspace(self, identity):
        if not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('Invalid model identity')
        with self.connect() as db:
            row = db.execute('SELECT body FROM workspace WHERE identity=?', (identity,)).fetchone()
        return json.loads(row['body']) if row else {}
