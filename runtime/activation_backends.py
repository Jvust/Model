"""Allowlisted bridge from model selection to existing Model Runtime adapters."""
from __future__ import annotations
import hashlib
import json
import re
import time
import threading
import uuid
from pathlib import Path
try:
    from . import image_runtime as image_module, task_runtime as task_module, video_runtime as video_module
    from .native_worker import CATALOG, validate_input, integer
    from .package_runtime import normalize_manifest
    from .package_integrity import safe_relative
    from .desktop_source import PREFIX, payload_specs
    from .drive_cache import DriveFileSpec
    from .runtime_variants import RuntimeVariants, PROFILES
    from .hardware import gguf_preflight
    from .activation_store import digest
except ImportError:
    import image_runtime as image_module, task_runtime as task_module, video_runtime as video_module
    from native_worker import CATALOG, validate_input, integer
    from package_runtime import normalize_manifest
    from package_integrity import safe_relative
    from desktop_source import PREFIX, payload_specs
    from drive_cache import DriveFileSpec
    from runtime_variants import RuntimeVariants, PROFILES
    from hardware import gguf_preflight
    from activation_store import digest

SCALAR_SELECTION = {'name', 'model_id', 'category', 'backend', 'package_path', 'relative_path', 'variant'}
FILE_KEYS = {'drive_file_id', 'file_name', 'size', 'md5_checksum', 'resource_key', 'relative_path', 'modified_time'}
COMMON_KEYS = {'image', 'images', 'values', 'csv', 'column', 'texts', 'query', 'documents'}
ITEM_KEYS = {'prompt', 'negative_prompt', 'width', 'height', 'steps', 'cfg', 'seed', 'frames',
             'horizon', 'frequency', 'max_new_tokens', 'format', 'top_n'}
CHAT_CATEGORIES = {'llm', 'reasoning', 'code', 'novel', 'chat'}


def selection_payload(value):
    if not isinstance(value, dict):
        raise ValueError('先选择已扫描到的模型')
    if set(value) - SCALAR_SELECTION - {'files', 'manifest_files'}:
        raise ValueError('Unexpected model-selection fields')
    result = {}
    for key in SCALAR_SELECTION:
        item = value.get(key)
        if item is not None:
            if not isinstance(item, str) or len(item) > 4096:
                raise ValueError('Invalid selection metadata')
            result[key] = item
    for key in ('files', 'manifest_files'):
        raw = value.get(key, [])
        if not isinstance(raw, list) or len(raw) > 4096:
            raise ValueError('Invalid model manifest')
        result[key] = []
        for item in raw:
            if not isinstance(item, dict) or set(item) - FILE_KEYS:
                raise ValueError('Unexpected file metadata')
            normalized = {k: v for k, v in item.items() if v is not None}
            spec = DriveFileSpec.from_payload(normalized)
            if not spec.size or spec.size < 1:
                raise ValueError('每个权重/配置文件都需要实际文件大小')
            path = normalized.get('relative_path', spec.name)
            safe_relative(path)
            normalized['relative_path'] = path
            modified = normalized.get('modified_time', '')
            if not isinstance(modified, str) or len(modified) > 100:
                raise ValueError('Invalid source version')
            result[key].append(normalized)
    if not result['manifest_files']:
        result['manifest_files'] = list(result['files'])
    if not result['manifest_files']:
        raise ValueError('没有扫描到实际文件，空目录不能启用模型')
    return result


def strip_model_wrapper(payload):
    """Resolve exactly one model_index.json package root; never guess between models."""
    raw = payload['manifest_files']
    roots = [str(item['relative_path'])[:-len('model_index.json')]
             for item in raw if str(item['relative_path']).split('/')[-1] == 'model_index.json']
    if len(roots) != 1:
        raise ValueError('Diffusers 包必须有且只有一个 model_index.json；请选择更具体的模型目录')
    prefix = roots[0]
    selected = []
    for item in raw:
        path = item['relative_path']
        if path.startswith(prefix):
            relative = path[len(prefix):]
            # metadata-only runtime folders must never be interpreted as weights.
            if relative.split('/')[0] in {'inputs', 'outputs', 'offload', 'logs', '.cache'}:
                continue
            selected.append({**item, 'relative_path': relative})
    return {**payload, 'manifest_files': selected}


class ActivationBackends:
    def __init__(self, bridge, native, desktop):
        self.bridge, self.native, self.desktop = bridge, native, desktop
        self.cache = bridge.DRIVE_CACHE
        self.variants = RuntimeVariants(self.cache.root)

    def authorize(self, selection):
        payload = selection_payload(selection)
        self.cache.access_token(payload, self.bridge.DRIVE_SESSION.get)
        return payload

    def identity(self, payload, route):
        entries = []
        seen = set()
        source_types = set()
        for item in payload['manifest_files']:
            spec = DriveFileSpec.from_payload(item)
            relative = item['relative_path']
            if relative.casefold() in seen:
                raise ValueError('模型包包含同名/大小写冲突路径')
            seen.add(relative.casefold())
            if spec.file_id.startswith(PREFIX):
                source = self.desktop.resolve(spec)
                source_types.add('desktop')
                entry = {'source': 'desktop', 'root_identity': str(source.root), 'relative': source.relative,
                         'version': source.version, 'package_relative': relative}
            else:
                source_types.add('api')
                # Resource keys / access tokens deliberately excluded from durable identity.
                entry = {'source': 'api', 'id': spec.file_id, 'size': spec.size, 'md5': spec.md5_checksum,
                         'modified_time': item.get('modified_time', ''), 'package_relative': relative}
            entries.append(entry)
        if len(source_types) != 1:
            raise ValueError('不能把 API 与桌面来源混进同一个模型任务')
        return digest({'files': sorted(entries, key=lambda x: x['package_relative']), 'route': route,
                       'variant': payload.get('variant'), 'variant_manifest': self.variants.manifest(payload['variant']) if payload.get('variant') else None, 'contract': '0.19-activation-rc1'})

    def _route(self, payload):
        key = str(payload.get('model_id') or '')
        hay = ' '.join(str(payload.get(k, '')) for k in ('name', 'model_id', 'package_path')).lower()
        normalized = re.sub('[^a-z0-9]+', '_', hay)
        if 'qwen_image_edit_2511' in normalized:
            if payload.get('variant') != 'qwen2511_gguf_q4km':
                return 'qwen-original-unsupported', payload
            return 'image:qwen_image_edit_2511_gguf', {**payload, 'model_id': 'qwen_image_edit_2511_gguf'}
        if payload.get('variant'):
            raise ValueError('量化运行方案只适用于 Qwen-Image-Edit-2511')
        paths = [item['relative_path'] for item in payload['manifest_files']]
        if 'flux' in hay and 'klein' in hay and '4b' in hay and any(path.endswith('model_index.json') for path in paths):
            payload = strip_model_wrapper(payload)
            return 'native:flux2_klein_4b_diffusers', {**payload, 'model_id': 'flux2_klein_4b_diffusers'}
        if key in CATALOG:
            if key.startswith('wan22_'):
                payload = strip_model_wrapper(payload)
            return 'native:' + key, payload
        matched = task_module.adapter_for(payload.get('name', ''), key, payload.get('package_path', ''))
        if matched:
            return 'task:' + matched[0], payload
        matched = image_module.adapter_for(payload.get('name', ''), key, payload.get('package_path', ''))
        if matched:
            return 'image:' + matched[0], payload
        matched = video_module.adapter_for(payload.get('name', ''), key)
        if matched:
            return 'video:' + matched[0], payload
        # Image GGUFs are NEVER dispatched as chat by extension alone.
        if payload.get('backend') == 'llama.cpp' and str(payload.get('category', '')).lower() in CHAT_CATEGORIES:
            files = [item for item in payload['files'] or payload['manifest_files'] if item['file_name'].lower().endswith('.gguf')]
            if len(files) != 1:
                raise ValueError('聊天启动需要选择一个明确的 GGUF 文件，而不是多个量化版本')
            return 'chat', {**payload, **files[0]}
        return 'unsupported', payload

    def plan(self, selection):
        payload = selection_payload(selection)
        route, target = self._route(payload)
        if payload.get('variant'):
            # Pin public metadata before assigning a durable job identity, not during inference.
            # This downloads no weight bytes and never replaces a Drive source file.
            self.variants.resolve(payload['variant'], threading.Event())
        identity = self.identity(target, route)
        reasons, warnings = [], []
        kind = 'unknown'
        variant = payload.get('variant')
        remaining = None
        if route == 'qwen-original-unsupported':
            reasons.append('该入口不把 Qwen2511 原始全精度模型强塞进 8GB/T4；请选择下方独立 GGUF 运行副本，或连接已验证的大显存适配器')
            kind = 'image'
        elif route == 'unsupported':
            reasons.append('该模型家族尚无经过登记的运行适配器；不能只凭文件扩展名或完成标记启用')
        elif route.startswith('native:'):
            result = self.native.plan(target)
            reasons += result['reasons']
            remaining = result['remaining_bytes']
            kind = CATALOG[route.split(':', 1)[1]]['kind']
        elif route.startswith('image:'):
            adapter = image_module.ADAPTERS[route.split(':', 1)[1]]
            result = image_module.managed_comfy_hardware(adapter)
            if not result['supported']:
                reasons.append(str(result['detail']))
            if not variant:
                try:
                    image_module.artifact_specs(target, adapter)
                except (ValueError, FileNotFoundError) as error:
                    reasons.append(str(error))
            else:
                warnings.append(PROFILES[variant]['note'])
                stored = self.variants.manifest(variant)
                remaining = stored['total_bytes'] if stored else None
            kind = 'image'
        elif route.startswith('video:'):
            adapter = video_module.ADAPTERS[route.split(':', 1)[1]]
            result = video_module.adapter_hardware(adapter)
            if not result['supported']:
                reasons.append(str(result['detail']))
            kind = 'video'
            warnings.append('该视频适配器使用登记工作流及其固定依赖；首次依赖下载计入本地缓存')
        elif route.startswith('task:') or route == 'chat':
            if not self.bridge.resolve_llama_server():
                reasons.append('本机 llama-server 未安装；请使用完整 Model Windows 包')
            try:
                spec = self._llama_spec(route, target)
                cached = self.cache.cached_path(spec)
                pre = gguf_preflight(self.cache.root, expected_bytes=spec.size,
                    partial_bytes=spec.size if cached else self.cache.describe(spec).get('partial_bytes', 0),
                    threads=self.bridge.MODEL_THREADS)
                if not pre['ok']:
                    reasons.append(pre['reason'])
                warnings += pre['warnings']
                remaining = pre['remaining_bytes']
            except (ValueError, FileNotFoundError) as error:
                reasons.append(str(error))
            kind = 'chat' if route == 'chat' else task_module.TASK_ADAPTERS[route.split(':', 1)[1]]['kind']
        return {'identity': identity, 'label': payload.get('name') or payload.get('model_id') or route,
                'route': route, 'kind': kind, 'ready': not reasons, 'reasons': reasons, 'warnings': warnings,
                'requires_variant_consent': bool(variant), 'estimated_new_variant_bytes': remaining if variant else None,
                'remaining_bytes': remaining, 'model_execution_verified': False, 'resume_unit': 'task',
                'supported_variant': 'qwen2511_gguf_q4km' if kind == 'image' and 'qwen' in route else None}

    def _llama_spec(self, route, target):
        if route == 'chat':
            return DriveFileSpec.from_payload(target)
        return task_module.task_file_spec(target, task_module.TASK_ADAPTERS[route.split(':', 1)[1]])

    def busy(self):
        # Resident chat/task engines are not automatically stopped; the user sees an explicit stop button.
        engines = [self.bridge.STATE, self.bridge.TASK, self.bridge.IMAGE, self.bridge.VIDEO,
                   self.bridge.PACKAGE, self.native]
        return any(item.snapshot().get('running') or item.snapshot().get('phase') in
                   {'downloading', 'loading', 'starting', 'cancelling'} for item in engines)

    def validate_common(self, route, value):
        if not isinstance(value, dict) or set(value) - COMMON_KEYS - ITEM_KEYS:
            raise ValueError('Invalid task input fields')
        # Scalars are validated per item. Image bytes and tabular data occur only once in the journal.
        return dict(value)

    def validate_item(self, route, common, item):
        if not isinstance(item, dict) or set(item) - ITEM_KEYS:
            raise ValueError('Invalid per-task overrides')
        task = {**common, **item}
        task.setdefault('seed', 0)  # Stable seed on interrupted task restart.
        if route.startswith('native:'):
            validated = validate_input(route.split(':', 1)[1], task)
            return {k: v for k, v in validated.items() if k not in COMMON_KEYS}
        if route.startswith('image:'):
            adapter_id = route.split(':', 1)[1]
            if adapter_id == 'qwen_image_edit_2511_gguf':
                image_module.validate_image_uploads(task)
                test = {**task, '_image_names': ['model_' + '0'*32 + '_0.png']}
                image_module.build_prompt({k: v['name'] for k, v in image_module.ADAPTERS[adapter_id]['artifacts'].items()}, test, image_module.ADAPTERS[adapter_id], '0'*32)
            else:
                if task.get('images') or task.get('image'):
                    raise ValueError('该旧 Comfy 适配器是文生图；多参考编辑请选 FLUX2 Diffusers 或 Qwen2511 方案')
                image_module.build_prompt({}, task, image_module.ADAPTERS[adapter_id], '0'*32)
            return {k: v for k, v in task.items() if k in ITEM_KEYS}
        if route.startswith('video:'):
            adapter_id = route.split(':', 1)[1]
            adapter = video_module.ADAPTERS[adapter_id]
            graph = json.loads(video_module.resource_path(adapter['workflow']).read_text(encoding='utf-8'))
            video_module.customize_workflow(graph, adapter_id, task, '0'*32)
            return {k: v for k, v in task.items() if k in ITEM_KEYS}
        if route.startswith('task:'):
            kind = task_module.TASK_ADAPTERS[route.split(':', 1)[1]]['kind']
            if kind == 'embedding':
                values = task.get('texts')
                if not isinstance(values, list) or not 1 <= len(values) <= 64 or any(not isinstance(s, str) or not s.strip() or len(s) > 16000 for s in values):
                    raise ValueError('Embedding 需要 1..64 条文本，每条不超过 16000 字符')
            else:
                docs, query = task.get('documents'), task.get('query')
                if not isinstance(query, str) or not query.strip() or not isinstance(docs, list) or not 1 <= len(docs) <= 100 or any(not isinstance(d, str) or not d.strip() or len(d)+len(query)>16000 for d in docs):
                    raise ValueError('Rerank 需要查询和 1..100 个候选文档')
            return {k: v for k, v in task.items() if k in ITEM_KEYS}
        if route == 'chat':
            return {}  # Launch into the existing chat workspace; not an implicit prompt request.
        raise ValueError('Unsupported task route')

    def _wait(self, engine, cancel, progress, ready=False, timeout=7200):
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            if cancel.is_set():
                engine.stop()
                raise InterruptedError('任务已取消')
            state = engine.snapshot()
            message = str(state.get('phase', '')) + ' ' + str(state.get('detail') or '')
            if message != last:
                progress(message)
                last = message
            if ready and state.get('ready') or not ready and state.get('phase') == 'complete':
                # A terminal phase is not enough: wait for worker cleanup to release references.
                if ready or not state.get('running'):
                    if not ready:
                        thread = getattr(engine, 'job_thread', None) or getattr(engine, 'thread', None)
                        if thread:
                            thread.join(timeout=10)
                            if thread.is_alive():
                                raise RuntimeError('后端尚未释放，拒绝启动下一项')
                    return state
            if state.get('phase') in {'failed', 'error', 'cancelled'} or state.get('last_error'):
                raise RuntimeError(state.get('error') or state.get('last_error') or '后端任务失败')
            time.sleep(0.2)
        engine.stop()
        raise TimeoutError('等待模型后端超时，已发出取消指令')

    def prepare(self, plan, selection, cancel, progress):
        payload = self.authorize(selection)
        route, target = self._route(payload)
        if self.identity(target, route) != plan['identity']:
            raise ValueError('来源在启用前已变化')
        progress('校验来源、缓存及后端依赖')
        trusted = None
        if payload.get('variant'):
            trusted = self.variants.prepare(payload['variant'], cancel, progress)
        if route.startswith('native:'):
            self.native.prepare(target, cancel, progress)
        elif route.startswith('image:'):
            self.bridge.IMAGE.prepare(target, cancel, trusted)
            self.bridge.IMAGE._set_phase('ready', '后端和已校验权重已准备')
            self.bridge.VIDEO._set_phase('idle', '')
        elif route.startswith('video:'):
            engine = self.bridge.VIDEO
            engine.cancel = cancel
            adapter_id = route.split(':', 1)[1]
            graph = json.loads(video_module.resource_path(video_module.ADAPTERS[adapter_id]['workflow']).read_text(encoding='utf-8'))
            graph, keep = video_module.customize_workflow(graph, adapter_id, {'prompt':'prepare', 'seed':0}, uuid.uuid4().hex)
            portable, _, _ = engine._ensure_comfyui()
            engine._ensure_models(portable, graph, keep)
            engine._ensure_comfyui_server()
            video_module.workflow_to_api_prompt(graph, video_module.json_request(video_module.COMFY_BASE+'/object_info', timeout=60), keep)
            engine._set_phase('ready', '视频后端已准备')
        else:
            spec = self._llama_spec(route, target)
            token = self.cache.access_token(target, self.bridge.DRIVE_SESSION.get)
            def transfer(n, total):
                if cancel.is_set():
                    raise InterruptedError('Task cancelled')
                progress(f'准备权重 {n}/{total or "?"} bytes')
            local = self.cache.cached_path(spec) or self.cache.download(spec, token, transfer)
            task_module.inspect_gguf(local)
        return {'target': target, 'trusted': trusted}

    def execute(self, plan, selection, prepared, task, cancel, progress):
        route, target = plan['route'], prepared['target']
        if route.startswith('native:'):
            self.native.start({**target, 'input': task})
            state = self._wait(self.native, cancel, progress)
            path = self.native.result_path(state['job_id'])
            data = json.loads(path.read_text(encoding='utf-8'))
            paths = [path]
            if data.get('kind') in {'video', 'image'}:
                paths.append(self.native.result_path(state['job_id'], media=True))
            return {'paths': paths, 'data': data, 'backend_job_id': state['job_id']}
        if route.startswith('image:') or route.startswith('video:'):
            engine = self.bridge.IMAGE if route.startswith('image:') else self.bridge.VIDEO
            if route.startswith('image:'):
                engine.start({**target, **task}, trusted_artifacts=prepared['trusted'])
            else:
                engine.start({**target, **task})
            state = self._wait(engine, cancel, progress)
            return {'paths': [engine.output_file(state['job_id'])], 'backend_job_id': state['job_id']}
        if route.startswith('task:'):
            self.bridge.TASK.start(target)
            self._wait(self.bridge.TASK, cancel, progress, ready=True)
            kind = task_module.TASK_ADAPTERS[route.split(':', 1)[1]]['kind']
            data = self.bridge.TASK.embeddings({'texts': task['texts']}) if kind == 'embedding' else self.bridge.TASK.rerank(task)
            return {'data': data}
        if route == 'chat':
            token = self.cache.access_token(target, self.bridge.DRIVE_SESSION.get)
            self.bridge.STATE.start_drive(self._llama_spec(route, target), target.get('name', ''), target.get('package_path', ''), token)
            self._wait(self.bridge.STATE, cancel, progress, ready=True, timeout=600)
            return {'data': {'ready': True, 'open_workspace': 'chat'}}
        raise ValueError('Unsupported execution route')

    def finish(self, plan, prepared, cancelled=False):
        if plan['route'].startswith('task:'):
            self.bridge.TASK.stop()
        # Native/Comfy inference remains demand-driven; completed tasks have no active worker.
