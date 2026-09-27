"""Real localhost/Chromium checks with a filesystem fixture, not a DriveFS mount."""
import json
import os
import struct
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as temporary:
    base=Path(temporary)
    os.environ['MODEL_CACHE_ROOT']=str(base/'cache')
    os.environ['MODEL_DESKTOP_CONFIG']=str(base/'desktop-source.json')
    from runtime import application
    vault=base/'Drive desktop fixture'/'AI-Model-Vault'
    model=vault/'llm'/'Desktop Test'/'test.gguf';model.parent.mkdir(parents=True)
    model.write_bytes(struct.pack('<4sIQQ', b'GGUF', 3, 1, 1)+b'fixture')
    application.DESKTOP.configure(str(vault))
    server=ThreadingHTTPServer(('127.0.0.1',8765),application.ApplicationHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    evidence=Path(os.environ.get('EVIDENCE_DIR','qa-evidence'));evidence.mkdir(parents=True,exist_ok=True)
    errors=[];oauth_requests=[]
    try:
        with sync_playwright() as p:
            options={'headless':True}
            if os.environ.get('CHROMIUM_PATH'):options['executable_path']=os.environ['CHROMIUM_PATH']
            browser=p.chromium.launch(**options)
            page=browser.new_page(viewport={'width':1280,'height':1000})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:oauth_requests.append(request.url) if any(host in request.url for host in ['googleapis.com','accounts.google.com','workers.dev']) else None)
            page.add_init_script("localStorage.setItem('model_source_mode','desktop');")
            page.goto('http://127.0.0.1:8765',wait_until='networkidle')
            assert page.locator('#loginBtn').is_hidden()
            assert page.locator('#modelSourceSelect').input_value()=='desktop'
            page.locator('#desktopScan').click()
            page.wait_for_function("document.querySelector('#desktopStatus').textContent.includes('扫描完成')")
            assert 'Desktop Test' in page.locator('#modelList').inner_text()
            assert page.locator('#modelList').get_by_text('测试桌面读取').is_visible()
            page.locator('#modelList').get_by_text('测试桌面读取').click()
            page.wait_for_function("document.querySelector('#status').textContent.includes('桌面读取成功')")
            assert not oauth_requests,oauth_requests
            page.locator('#desktopSource').scroll_into_view_if_needed()
            page.screenshot(path=str(evidence/'desktop-source.png'))
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(evidence/'desktop-source-mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            page.locator('#modelSourceSelect').select_option('api')
            page.wait_for_function("window.ModelApp.source()==='api'")
            assert page.locator('#loginBtn').is_visible()
            assert 'Desktop Test' not in page.locator('#modelList').inner_text()
            page.locator('#modelSourceSelect').select_option('desktop')
            page.wait_for_function("window.ModelApp.source()==='desktop'")
            assert 'Desktop Test' not in page.locator('#modelList').inner_text()
            assert not errors,errors
            browser.close()
        report={'passed':True,'transport':'real localhost HTTP + Chromium','source':'temporary local filesystem fixture','actual_google_drive_desktop_tested':False,'inference_tested':False,
                'checks':['desktop-mode','API-controls-hidden','scan-renders-package','bounded-read-probe','zero-OAuth-or-Drive-API-requests','mobile-overflow','switch-to-API','stale-index-cleared','no-page-errors'],
                'oauth_requests':oauth_requests,'page_errors':errors}
        (evidence/'desktop-browser.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        server.shutdown();server.server_close();thread.join(3)
