"""Real browser/HTTP UI smoke check; no OAuth or model inference is simulated as real."""
import functools
import json
import os
import tempfile
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright

os.environ['MODEL_CACHE_ROOT'] = tempfile.mkdtemp(prefix='model-ui-')
from runtime.application import ApplicationHandler
root = Path(__file__).resolve().parents[1]
evidence = Path(os.environ.get('EVIDENCE_DIR', str(root / 'qa-evidence')))
evidence.mkdir(parents=True, exist_ok=True)
web = ThreadingHTTPServer(('127.0.0.1', 8000), functools.partial(SimpleHTTPRequestHandler, directory=str(root)))
api = ThreadingHTTPServer(('127.0.0.1', 8765), ApplicationHandler)
for server in (web, api):
    threading.Thread(target=server.serve_forever, daemon=True).start()
errors = []
try:
    with sync_playwright() as p:
        options = {'headless': True}
        if os.environ.get('CHROMIUM_PATH'):
            options['executable_path'] = os.environ['CHROMIUM_PATH']
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={'width': 1280, 'height': 950})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8000', wait_until='networkidle')
        page.locator('#nativeRefresh').click()
        page.wait_for_function("document.querySelector('#nativeModel').options.length === 5")
        assert page.locator('#nativeRun').is_disabled()
        page.locator('#nativeModel').select_option('chronos_2')
        assert page.locator('#nativeSeries').is_visible()
        assert '缺少此模型包' in page.locator('#nativeStatus').inner_text()
        page.locator('#nativeModel').select_option('got_ocr2')
        assert page.locator('#nativeImage').is_visible()
        page.locator('#nativeWorkspaces').scroll_into_view_if_needed()
        page.screenshot(path=str(evidence / 'desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(evidence / 'mobile.png'))
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        assert not errors, errors
        browser.close()
    report = {'passed': True, 'transport': 'real-localhost-http', 'model_inference_tested': False, 'checks': ['page-load', 'five-families', 'missing-model-block', 'forecast-input', 'ocr-input', 'mobile-overflow', 'javascript-errors'], 'page_errors': errors}
    (evidence / 'browser.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
finally:
    for server in (web, api):
        server.shutdown()
        server.server_close()
