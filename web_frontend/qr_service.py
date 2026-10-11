import asyncio, time, uuid, os, json
from playwright.async_api import async_playwright

active_sessions = {} # session_id -> {status, qr_data_url, cookie, expires_at, page, browser}

async def launch_qr_session(session_id, cookie_save_callback):
    try:
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process'
            ]
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            permissions=['geolocation']
        )
        page = await context.new_page()
        # 深度注入伪装
        await page.add_init_script('''
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                ]
            });
            window.chrome = {
                app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
                runtime: { OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' } }
            };
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Google Inc. (NVIDIA)';
                if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                return getParameter.apply(this, [parameter]);
            };
        ''')
        
        # 访问探索首页并自然触发登录弹窗
        await page.goto('https://www.xiaohongshu.com/explore', wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(1500)
        
        login_btn = await page.query_selector('.login-btn, button:has-text("登录")')
        if login_btn:
            await login_btn.click()
            await page.wait_for_timeout(2500)
            
        # 精准匹配二维码元素 (.qrcode-img 宽高128x128)
        qr_elem = await page.wait_for_selector('.qrcode-img', timeout=12000)
        if not qr_elem:
            active_sessions[session_id]['status'] = 'failed'
            await browser.close()
            await p.stop()
            return
            
        src = await qr_elem.get_attribute('src')
        if not src:
            buf = await qr_elem.screenshot()
            import base64
            src = "data:image/png;base64," + base64.b64encode(buf).decode('utf-8')
            
        active_sessions[session_id]['qr_data_url'] = src
        active_sessions[session_id]['status'] = 'ready'
        
        # 轮询监听扫码登录状态 (最长等待 120 秒)
        start_wait = time.time()
        while time.time() - start_wait < 120:
            await page.wait_for_timeout(1500)
            cookies = await context.cookies()
            # 严格判断：用户在手机上点击确认后，登录弹窗会消失，或者 URL 改变，或者出现用户专属的 web_session
            url_now = page.url
            modal_gone = await page.evaluate('''() => {
                const modal = document.querySelector('.login-container');
                const logged = document.querySelector('.user-nickname, .avatar-wrapper');
                return (!modal && !window.location.href.includes('/login')) || !!logged;
            }''')
            has_session = any(c.get('name') == 'web_session' for c in cookies)
            if modal_gone and has_session:
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                active_sessions[session_id]['status'] = 'success'
                active_sessions[session_id]['cookie'] = cookie_str
                cookie_save_callback(cookie_str)
                break
                
        await browser.close()
        await p.stop()
    except Exception as e:
        if session_id in active_sessions:
            active_sessions[session_id]['status'] = 'failed'

def start_qr_task(session_id, callback):
    active_sessions[session_id] = {
        'status': 'starting',
        'qr_data_url': None,
        'cookie': None,
        'created_at': time.time()
    }
    def run():
        asyncio.run(launch_qr_session(session_id, callback))
    import threading
    t = threading.Thread(target=run, daemon=True)
    t.start()
