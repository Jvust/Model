"""Restart-safe, explicit user-driven activation and task-level continuation.

The journal records validated inputs and completed outputs, never tokens or Drive
handles. It does NOT claim to resume an image halfway through denoising. Adapters
remain the only execution authority; an arbitrary checkpoint is not executable.
"""
from __future__ import annotations
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
try:
    from .activation_store import ActivationStore, check_id, clean
except ImportError:
    from activation_store import ActivationStore, check_id, clean


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as handle:
            json.dump(clean(value), handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def hash_file(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        while chunk := handle.read(4 * 1024**2):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('任务已取消')
            digest.update(chunk)
    return digest.hexdigest()


class ActivationRuntime:
    """Facade is dependency-injected so restart/cancellation can be tested on CPU."""
    def __init__(self, root, facade, recover=True):
        self.store = ActivationStore(Path(root) / 'activation')
        self.facade = facade
        self.root = self.store.root
        self.lock = threading.RLock()
        self.thread = None
        self.cancel = threading.Event()
        self.job_id = None
        if recover:
            self.store.recover()

    def active(self):
        with self.lock:
            return bool(self.thread and self.thread.is_alive())

    def snapshot(self):
        with self.lock:
            job_id = self.job_id
        return {'running': self.active(), 'job': self.store.get(job_id) if job_id else None,
                'resume_unit': 'task', 'automatic_restart': False}

    def plan(self, selection):
        return self.facade.plan(selection)

    def start(self, body):
        if not isinstance(body, dict):
            raise ValueError('JSON object required')
        selection = body.get('selection')
        plan = self.plan(selection)
        if not plan['ready']:
            raise ValueError('; '.join(plan['reasons']))
        self.facade.authorize(selection)
        prepare_only = body.get('prepare_only', False)
        if type(prepare_only) is not bool:
            raise ValueError('prepare_only must be boolean')
        if plan.get('requires_variant_consent') and body.get('accept_variant') is not True:
            raise ValueError('该方案需要另存量化权重；请明确确认下载运行副本，不会替换 Drive 原始模型')
        if prepare_only:
            items = [{}]
            inputs = {}
        else:
            inputs = self.facade.validate_common(plan['route'], body.get('input', {}))
            raw_items = body.get('items', [{}])
            if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 100:
                raise ValueError('每批需要 1..100 个任务')
            items = [self.facade.validate_item(plan['route'], inputs, item) for item in raw_items]
        request = {'input': inputs, 'items': items, 'prepare_only': prepare_only,
                   'variant': selection.get('variant'), 'accept_variant': body.get('accept_variant') is True}
        with self.lock:
            if self.active():
                # A double-click with the same key returns the existing job without launching it twice.
                existing = self.store.by_request_key(body.get('request_key'))
                if existing:
                    # create() only verifies the already-known request key here; no new queued record.
                    existing = self.store.create(plan['identity'], plan['label'], plan['route'], request, body['request_key'])
                    return existing
                raise RuntimeError('已有启用/批量任务进行中，请先等待或取消')
            if self.facade.busy():
                raise RuntimeError('其他模型仍在运行；请先停止它再启用当前模型')
            job = self.store.create(plan['identity'], plan['label'], plan['route'], request, body.get('request_key'))
            if job['state'] != 'queued':
                return job  # Idempotent duplicate, including already-completed jobs.
            return self._launch(job['id'], plan, selection)

    def resume(self, body):
        if not isinstance(body, dict):
            raise ValueError('JSON object required')
        job_id = check_id(body.get('job_id'))
        selection = body.get('selection')
        plan = self.plan(selection)
        if not plan['ready']:
            raise ValueError('; '.join(plan['reasons']))
        self.facade.authorize(selection)
        job = self.store.get(job_id, private=True)
        if not job['can_resume']:
            raise ValueError('此任务没有可接续的未完成项')
        if job['identity'] != plan['identity'] or job['route'] != plan['route']:
            raise ValueError('模型来源、版本或运行方案不一致，不能接续旧任务')
        if plan.get('requires_variant_consent') and not job['request'].get('accept_variant'):
            raise ValueError('旧任务未授权量化运行副本')
        # Re-verify already committed outputs, not just their journal names.
        for result in job['results']:
            for entry in result.get('files', []):
                path = self.media_path(job_id, result['step'], entry['name'])
                if hash_file(path) != entry['sha256']:
                    raise ValueError('历史结果已变化，请保留原任务并新建任务')
        with self.lock:
            if self.active() or self.facade.busy():
                raise RuntimeError('其他任务正在运行，不能并行接续')
            return self._launch(job_id, plan, selection)

    def _launch(self, job_id, plan, selection):
        job = self.store.claim(job_id, plan['identity'])
        self.cancel = threading.Event()
        self.job_id = job_id
        # Selection contains short-lived source handles only in memory.
        self.thread = threading.Thread(target=self._run, args=(job, plan, selection), daemon=True)
        self.thread.start()
        return self.store.get(job_id)

    def stop(self):
        with self.lock:
            if self.active():
                self.cancel.set()
        return self.snapshot()

    def shutdown(self):
        self.stop()
        thread = self.thread
        if thread:
            thread.join(timeout=10)

    def _receipt(self, job_id, step):
        return self.root / 'jobs' / check_id(job_id) / f'{step:04d}' / 'receipt.json'

    def _read_receipt(self, job, step):
        path = self._receipt(job['id'], step)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('identity') != job['identity'] or value.get('step') != step:
            raise ValueError('Result receipt identity mismatch')
        for entry in value['result'].get('files', []):
            file = path.parent / entry['name']
            if Path(entry['name']).name != entry['name'] or file.is_symlink() or not file.is_file() or hash_file(file) != entry['sha256']:
                raise ValueError('Result receipt file is missing or changed')
        return value['result']

    def _persist_result(self, job, step, result):
        directory = self._receipt(job['id'], step).parent
        directory.mkdir(parents=True, exist_ok=True)
        entries = []
        for n, source in enumerate(result.get('paths', [])):
            source = Path(source)
            if not source.is_file() or source.is_symlink():
                raise ValueError('Backend did not return a regular result file')
            if source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm', '.mkv', '.gif', '.json', '.csv', '.txt'}:
                raise ValueError('Unsupported result file format')
            target = directory / f'result-{n:02d}{source.suffix.lower()}'
            if target.exists():
                # The only valid pre-existing receipt is recovered before execution.
                raise ValueError('Uncommitted result file exists; do not overwrite it')
            tmp = target.with_suffix(target.suffix + '.part')
            with source.open('rb') as src, tmp.open('wb') as dst:
                while chunk := src.read(4 * 1024**2):
                    if self.cancel.is_set():
                        raise InterruptedError('任务已取消')
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            tmp.replace(target)
            entries.append({'name': target.name, 'size': target.stat().st_size, 'sha256': hash_file(target)})
        data = clean(result.get('data', {}))
        data_text = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(data_text.encode('utf-8')) > 24_000:
            target = directory / 'full-result.json'
            atomic_json(target, data)
            entries.append({'name': target.name, 'size': target.stat().st_size, 'sha256': hash_file(target)})
            data = {'preview': data_text[:4000], 'truncated': True, 'full_result': target.name}
        persisted = {'files': entries, 'data': data,
                     'backend_job_id': result.get('backend_job_id'), 'completed_at': time.time()}
        atomic_json(self._receipt(job['id'], step), {'identity': job['identity'], 'step': step, 'result': persisted})
        return persisted

    def _run(self, job, plan, selection):
        request, job_id = job['request'], job['id']
        prepared = None
        try:
            def progress(detail):
                if self.cancel.is_set():
                    raise InterruptedError('任务已取消')
                self.store.update(job_id, 'preparing', detail)
            # Preparation is idempotent; source is re-authorized for every attempt.
            prepared = self.facade.prepare(plan, selection, self.cancel, progress)
            for step in range(job['cursor'], job['total']):
                if self.cancel.is_set():
                    raise InterruptedError('任务已取消')
                result = self._read_receipt(job, step)
                if result is None:
                    directory = self._receipt(job_id, step).parent
                    if directory.exists():
                        # Preserve output from a crash before receipt commit, never overwrite it.
                        directory.rename(directory.with_name(directory.name + '-interrupted-' + uuid.uuid4().hex))
                    self.store.update(job_id, 'running', f'{step+1}/{job["total"]}')
                    if request['prepare_only']:
                        raw = {'data': {'prepared': True, 'generation_tested': False, 'route': plan['route']}}
                    else:
                        task = {**request['input'], **request['items'][step]}
                        raw = self.facade.execute(plan, selection, prepared, task, self.cancel,
                            lambda message: self.store.update(job_id, 'running', str(message)))
                    result = self._persist_result(job, step, raw)
                self.store.checkpoint(job_id, step, result, ready=request['prepare_only'] or plan['route'] == 'chat')
        except BaseException as error:
            # Progress/error strings come from internal adapters, never include request payloads/tokens.
            self.store.update(job_id, 'cancelled' if self.cancel.is_set() else 'failed', str(error)[:2000])
        finally:
            self.facade.finish(plan, prepared, cancelled=self.cancel.is_set())
            selection = None

    def media_path(self, job_id, step, name):
        check_id(job_id)
        try:
            step = int(step)
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid result index') from error
        result = next((r for r in self.store.get(job_id)['results'] if r['step'] == step), None)
        if result is None or not any(e['name'] == name for e in result.get('files', [])):
            raise FileNotFoundError('Unknown committed result')
        root = self._receipt(job_id, step).parent.resolve()
        path = root / name
        if Path(name).name != name or path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise FileNotFoundError('Result is unavailable')
        return path
