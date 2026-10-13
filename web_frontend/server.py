from qr_service import start_qr_task, active_sessions
import os, sys, time, re, threading, requests, img2pdf, zipfile, json, uuid, html, hashlib
from urllib.parse import urlparse, quote, parse_qs
from datetime import datetime
from flask import Flask, render_template_string, request, send_file, jsonify, abort

app = Flask(__name__)

# 统一 JSON 错误响应,绝不向客户端泄露堆栈信息
app.config['JSON_AS_ASCII'] = False
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

# --- 运行模式配置 (支持环境变量 RUN_MODE + 启动参数 --mode/--output-dir) ---
# RUN_MODE 可选值:
#   public : 公网在线版 (默认) —— 启用单 IP 限频、中央 Cookie 池、分享落地页
#   lan    : 家庭/内网自用版 —— 关闭限频、单账号本地 cookie.json、直接写入用户归档目录
RUN_MODE = os.environ.get("RUN_MODE", "public").strip().lower()
if RUN_MODE not in ("public", "lan"):
    RUN_MODE = "public"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 归档目录:lan 模式下可通过 --output-dir / XHS_OUTPUT_DIR 指定,
# public 模式默认仍落在 web_frontend/downloads 下。
_DEFAULT_DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
DOWNLOAD_DIR = os.environ.get("XHS_OUTPUT_DIR") or _DEFAULT_DOWNLOAD_DIR
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# --- 安全常量 ---
# 允许的小红书合法域名(含常见 CDN 子域兜底,但转换入口只放行合法主域)
ALLOWED_SHARE_HOSTS = ('xiaohongshu.com', 'xhslink.com', 'xhslink.cn')
MAX_BATCH_LINKS = 5            # 单次批量最多 5 条,防止超长数组耗尽内存
RATE_LIMIT_PER_MINUTE = 10     # 单 IP 每分钟最多 10 次 /api/convert
DOWNLOAD_TTL_DAYS = 7          # downloads/ 目录文件保留 7 天
FILE_TTL_DAYS = 7              # 历史临时/旧文件保留 7 天

# lan 模式使用本地单账号 cookie.json (扁平结构),public 模式使用中央 cookie 池
if RUN_MODE == "lan":
    COOKIE_FILE = os.path.join(DATA_DIR, "cookie.json")
else:
    COOKIE_FILE = os.path.join(DATA_DIR, "cookies.json")

# 笔记缓存：同一笔记重复解析直接复用上次的 PDF，不再请求小红书。
# 放在 data/ 下独立于 downloads/，不受每天 0 点的“3 天清理”影响。
NOTE_CACHE_DIR = os.path.join(DATA_DIR, "note_cache")
os.makedirs(NOTE_CACHE_DIR, exist_ok=True)

NOTE_ID_RE = re.compile(r'/(?:explore|discovery/item)/([0-9a-fA-F]{24})')


def extract_note_id(url):
    """从链接提取 24 位笔记 ID；短链等拿不到时返回 None。"""
    m = NOTE_ID_RE.search(url)
    return m.group(1) if m else None


NOTE_CACHE_TTL_DAYS = 7

# --- 友好错误文案(非图文笔记/异常链接)---
MSG_VIDEO_NOTE = '该笔记为纯视频笔记,暂不支持提取合成试卷 PDF'
MSG_NOTE_MISSING = '笔记不存在、已被删除或设为私密,请检查链接后重试'
MSG_NO_IMAGES = '未解析到该笔记的无水印图片内容'
# 页面命中这些文案即判定笔记不可访问(删除/私密/失效)
NOTE_MISSING_MARKERS = (
    '当前笔记暂时无法浏览',
    '你访问的页面不见了',
    '笔记不存在',
    '当前内容无法查看',
    '该内容已删除',
)  # 缓存保留 7 天，到期删除


def note_cache_cleanup():
    """删除 7 天前的笔记缓存（pdf + json 成对）。"""
    cutoff = time.time() - NOTE_CACHE_TTL_DAYS * 86400
    removed = 0
    try:
        for f in os.listdir(NOTE_CACHE_DIR):
            fp = os.path.join(NOTE_CACHE_DIR, f)
            if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                removed += 1
    except Exception:
        pass
    return removed


def cache_get(note_id):
    """命中缓存时返回 (pdf_path, title)，否则 (None, None)。"""
    if not note_id:
        return None, None
    pdf_path = os.path.join(NOTE_CACHE_DIR, note_id + '.pdf')
    meta_path = os.path.join(NOTE_CACHE_DIR, note_id + '.json')
    if not os.path.exists(pdf_path):
        return None, None
    title = "未命名笔记"
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            title = json.load(f).get('title') or title
    except Exception:
        pass
    return pdf_path, title


def cache_put(note_id, pdf_path, title):
    """把成品 PDF 存入缓存（复制，原件留在 downloads/ 供下载）。"""
    if not note_id:
        return
    try:
        dst = os.path.join(NOTE_CACHE_DIR, note_id + '.pdf')
        with open(pdf_path, 'rb') as fsrc, open(dst, 'wb') as fdst:
            fdst.write(fsrc.read())
        with open(os.path.join(NOTE_CACHE_DIR, note_id + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'title': title, 'cached_at': time.time()}, f, ensure_ascii=False)
    except Exception:
        pass
QR_CACHE = {} # qrcode_id -> {qr_id, qr_code_url, code, status, cookie, updated_at}

# --- 0. 安全防护:路径清洗 / 域名白名单 / 接口限频 ---
def client_ip():
    """获取真实客户端 IP,兼容反向代理。"""
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def safe_basename(filename):
    """仅保留文件名本身,彻底杜绝 ../ 路径穿越。

    返回 None 表示非法/危险的文件名。
    """
    if not filename or not isinstance(filename, str):
        return None
    # 统一分隔符后取 basename,并剥离 Windows 盘符式前缀
    name = os.path.basename(filename.replace('\\', '/'))
    name = os.path.basename(name)
    if name in ('', '.', '..'):
        return None
    # 二次校验:不允许残余的路径分隔、空字节或控制字符(防 CRLF 头注入)
    if os.sep in name or (os.altsep and os.altsep in name):
        return None
    if re.search(r'[\x00-\x1f\x7f]', name):
        return None
    return name


def safe_join_under(base_dir, filename):
    """在 base_dir 下安全定位文件:返回绝对路径或 None(越界)。"""
    name = safe_basename(filename)
    if not name:
        return None
    base_abs = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_abs, name))
    # 必须严格位于 base_dir 之内
    if target != base_abs and not target.startswith(base_abs + os.sep):
        return None
    if not os.path.isfile(target):
        return None
    return target


def short_code_for(filename):
    """为文件名生成稳定的 8 位短码(md5 前缀),同一文件名始终得到相同短码。"""
    if not filename or not isinstance(filename, str):
        return ''
    return hashlib.md5(filename.encode('utf-8')).hexdigest()[:8]


def resolve_download_file(code_or_filename):
    """把短码或文件名解析为 downloads/ 下的真实文件名。

    解析顺序:
      1) 直接匹配文件名(完全兼容历史长链接);
      2) 匹配 8 位 md5 前缀(短码)。
    命中返回真实文件名,否则返回 None。
    """
    if not code_or_filename or not isinstance(code_or_filename, str):
        return None
    # 1) 先当作完整文件名直接校验
    direct = safe_join_under(DOWNLOAD_DIR, code_or_filename)
    if direct:
        return safe_basename(code_or_filename)
    # 2) 再当作 8 位短码做 md5 前缀匹配
    token = code_or_filename.strip()
    if not re.fullmatch(r'[0-9a-fA-F]{8}', token):
        return None
    token = token.lower()
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            if not (f.endswith('.pdf') or f.endswith('.zip')):
                continue
            if short_code_for(f) == token:
                return f
    except Exception:
        pass
    return None


def is_allowed_share_url(url):
    """校验 URL 是否属于小红书合法域名。"""
    try:
        host = (urlparse(url).hostname or '').lower()
    except Exception:
        return False
    if not host:
        return False
    for allowed in ALLOWED_SHARE_HOSTS:
        if host == allowed or host.endswith('.' + allowed):
            return True
    return False


class _RateLimiter:
    """极简滑动窗口限频器:单 IP 每分钟 RATE_LIMIT_PER_MINUTE 次。"""

    def __init__(self, limit=RATE_LIMIT_PER_MINUTE, window=60):
        self.limit = limit
        self.window = window
        self._hits = {}          # ip -> [timestamps]
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = []
                self._hits[key] = bucket
            cutoff = now - self.window
            # 原地剔除过期时间戳
            bucket[:] = [t for t in bucket if t > cutoff]
            if len(bucket) >= self.limit:
                retry = int(self.window - (now - bucket[0])) + 1
                return False, max(retry, 1)
            bucket.append(now)
            # 防止字典无限膨胀
            if len(self._hits) > 10000:
                for k in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(k, None)
            return True, 0


convert_limiter = _RateLimiter()


# --- 1. Cookie 管理与轮询池 ---
def load_cookies():
    """读取 Cookie。lan 模式读取扁平单账号 cookie.json,public 读取中央池。

    lan 模式下若 cookie.json 记录为 {cookie, web_session, ...} 扁平结构,
    统一归一化为池结构 [{cookie, status}] 供后续逻辑复用。
    """
    if not os.path.exists(COOKIE_FILE):
        return []
    try:
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        # 扁平单账号 (lan) 结构归一化
        cookie_str = data.get('cookie') or ''
        if not cookie_str and data.get('web_session'):
            cookie_str = 'web_session=' + data['web_session']
        if not cookie_str:
            return []
        return [{
            'id': data.get('id', 'local'),
            'cookie': cookie_str,
            'status': data.get('status', 'valid'),
            'added_at': data.get('added_at', time.time()),
            'user_id': data.get('user_id'),
        }]
    return data if isinstance(data, list) else []

def save_cookies(cookies):
    """写回 Cookie。lan 模式回写扁平单账号结构,public 写回池结构。"""
    if RUN_MODE == 'lan':
        flat = cookies[0] if cookies else {}
        with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
            json.dump(flat, f, ensure_ascii=False, indent=2)
        return
    with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)

def verify_cookie_health(cookie_str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Cookie': cookie_str,
        'Accept': 'application/json, text/plain, */*'
    }
    try:
        r = requests.get('https://www.xiaohongshu.com/explore', headers=headers, timeout=6)
        if '"loggedIn":true' in r.text:
            return True
        return False
    except Exception:
        return False

last_sync_time = 0
sync_time_lock = threading.Lock()

def sync_cookie_statuses(force=False):
    global last_sync_time
    with sync_time_lock:
        now = time.time()
        # 10 分钟限频 (600 秒)，除非强制更新 (如 3 小时定时巡检)
        if not force and (now - last_sync_time < 600):
            cookies = load_cookies()
            total = len(cookies)
            valid = len([c for c in cookies if c.get('status') == 'valid'])
            return total, valid, total - valid
        last_sync_time = now

    with cookie_lock:
        cookies = load_cookies()
        changed = False
        valid_count = 0
        invalid_count = 0
        for c in cookies:
            is_live = verify_cookie_health(c.get('cookie', ''))
            new_stat = 'valid' if is_live else 'invalid'
            if c.get('status') != new_stat:
                c['status'] = new_stat
                changed = True
            if is_live:
                valid_count += 1
            else:
                invalid_count += 1
        if changed:
            save_cookies(cookies)
        return len(cookies), valid_count, invalid_count

# 每 3 小时自动巡检一次 Cookie 状态
def cookie_inspector_loop():
    while True:
        try:
            sync_cookie_statuses(force=True)
            # 顺带补齐 user_id 缓存（仅限有效 cookie ——
            # 失效 cookie 拿不到登录态身份，硬探测只会写入垃圾 ID 污染去重）
            with cookie_lock:
                cookies = load_cookies()
                changed = False
                for c in cookies:
                    if not c.get('user_id') and c.get('status') == 'valid':
                        uid = cookie_user_id(c.get('cookie', ''))
                        if uid:
                            c['user_id'] = uid
                            changed = True
                # 清掉历史上可能被污染的垃圾 user_id（cookie 已失效的）
                for c in cookies:
                    if c.get('user_id') and c.get('status') != 'valid':
                        c['user_id'] = None
                        changed = True
                if changed:
                    save_cookies(cookies)
        except Exception:
            pass
        time.sleep(3 * 3600)

threading.Thread(target=cookie_inspector_loop, daemon=True).start()

cookie_index = 0
cookie_lock = threading.Lock()

def get_next_cookie():
    global cookie_index
    with cookie_lock:
        cookies = load_cookies()
        if RUN_MODE == 'lan':
            # 家庭自用版:单账号,直接返回唯一一条 cookie(不区分 valid/invalid,
            # 由 verify_cookie_health 现场校验)
            if not cookies:
                return None
            return cookies[0].get('cookie')
        valid = [c for c in cookies if c.get('status') == 'valid']
        if not valid:
            return None
        cookie_index = cookie_index % len(valid)
        chosen = valid[cookie_index]
        cookie_index = (cookie_index + 1) % len(valid)
        return chosen.get('cookie')

# --- 2. 每天 00:00 自动清理 7 天前生成的文件 ---
def disk_cleanup():
    """清理 downloads/ 下超过 7 天的旧 PDF/ZIP 及历史临时文件(temp_*.jpg)。

    返回清理数量。也供启动时立即执行一次,防止服务长时间不重启导致磁盘堆积。
    """
    removed = 0
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if not os.path.isfile(fp):
                continue
            name = f.lower()
            is_target = name.endswith(('.pdf', '.zip')) or name.startswith('temp_')
            if not is_target:
                continue
            try:
                # 临时文件更快过期(超过 1 天),历史成品保留 7 天
                ttl = 86400 if name.startswith('temp_') else DOWNLOAD_TTL_DAYS * 86400
                if os.path.getmtime(fp) < (time.time() - ttl):
                    os.remove(fp)
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def cleanup_worker():
    while True:
        now = datetime.now()
        # 等待到下一次 00:00:00
        seconds_until_midnight = ((24 - now.hour - 1) * 3600) + ((60 - now.minute - 1) * 60) + (60 - now.second)
        time.sleep(max(seconds_until_midnight, 10))
        try:
            disk_cleanup()
        except Exception:
            pass
        # 笔记缓存：保留 7 天，到期删除
        try:
            note_cache_cleanup()
        except Exception:
            pass
        time.sleep(60)

threading.Thread(target=cleanup_worker, daemon=True).start()
# 启动时顺带清一次过期文件与笔记缓存
try:
    disk_cleanup()
except Exception:
    pass
try:
    note_cache_cleanup()
except Exception:
    pass

# --- 3. 极简前端界面 ---
HTML_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>小红书资料转pdf</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                     "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            background: linear-gradient(160deg, #f6f8fb 0%, #eef2f7 100%);
            color: #1a1a1a;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            padding: 30px 16px 80px;
        }
        .main-card {
            width: 100%;
            max-width: 500px;
            background: #fff;
            padding: 32px 28px;
            border-radius: 20px;
            border: 1px solid #eef1f5;
            box-shadow: 0 10px 34px rgba(30, 50, 90, 0.08);
            position: relative;
            overflow: hidden;
        }
        .main-card::before {
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 5px;
            background: linear-gradient(90deg, #ff5a6e, #ff8a5c);
        }
        .app-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: #ff5a6e;
            background: #fff1f3;
            padding: 5px 12px;
            border-radius: 20px;
            margin-bottom: 12px;
            font-weight: 600;
        }
        .card-header {
            text-align: center;
            margin-bottom: 20px;
        }
        h1 {
            font-size: 21px;
            font-weight: 700;
            color: #1a1a1a;
            letter-spacing: -0.5px;
        }
        textarea {
            width: 100%;
            height: 116px;
            padding: 14px 16px;
            border: 1.5px solid #e2e8f0;
            border-radius: 12px;
            font-size: 13.5px;
            line-height: 1.55;
            outline: none;
            resize: vertical;
            background: #f8fafc;
            color: #1e293b;
            margin-bottom: 16px;
            transition: all 0.2s;
        }
        textarea:focus {
            border-color: #ff7a4d;
            background: #fff;
            box-shadow: 0 0 0 3px rgba(255, 122, 77, 0.15);
        }
        .btn-submit {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #ff5a6e, #ff7a4d);
            color: #fff;
            border: none;
            border-radius: 12px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 6px 18px rgba(255, 90, 110, 0.28);
            transition: all 0.2s;
        }
        .btn-submit:hover {
            box-shadow: 0 8px 22px rgba(255, 90, 110, 0.38);
            transform: translateY(-1px);
        }
        .btn-submit:active { transform: scale(0.98); }
        .btn-submit:disabled {
            background: #cbd5e1;
            box-shadow: none;
            cursor: not-allowed;
            transform: none;
        }
        
        /* 历史下载记录列表 */
        .history-section {
            width: 100%;
            max-width: 500px;
            margin-top: 24px;
        }
        .history-title {
            font-size: 13px;
            font-weight: 600;
            color: #64748b;
            margin-bottom: 10px;
            padding-left: 4px;
        }
        .history-list {
            background: #fff;
            border-radius: 16px;
            border: 1px solid #eef1f5;
            box-shadow: 0 4px 16px rgba(30, 50, 90, 0.04);
            overflow: hidden;
        }
        .history-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 13px 18px;
            border-bottom: 1px solid #f1f5f9;
            font-size: 13.5px;
        }
        .history-item:last-child { border-bottom: none; }
        .history-name {
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            margin-right: 12px;
            color: #334155;
            font-weight: 500;
        }
        .history-actions { display: flex; gap: 8px; flex-shrink: 0; }
        .history-btn {
            font-size: 12px;
            font-weight: 600;
            color: #ff5a6e;
            background: #fff1f3;
            padding: 4px 10px;
            border-radius: 8px;
            text-decoration: none;
            transition: all 0.15s;
        }
        .history-btn:hover { background: #ffe4e8; }
        .history-btn.download {
            color: #2b6cb0;
            background: #edf2f7;
        }
        .history-btn.download:hover { background: #e2e8f0; }

        /* 底部免责声明 */
        .disclaimer {
            max-width: 500px;
            width: 100%;
            margin: 28px auto 0;
            padding: 0 8px;
            text-align: center;
            font-size: 11.5px;
            line-height: 1.7;
            color: #94a3b8;
        }
        .disclaimer strong { color: #64748b; font-weight: 600; }

        /* 生成结果弹窗 */
        .result-overlay {
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.45);
            backdrop-filter: blur(4px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 200;
            padding: 20px;
        }
        .result-overlay.open { display: flex; }
        .result-card {
            width: 100%;
            max-width: 380px;
            background: #fff;
            border-radius: 20px;
            padding: 28px 24px;
            box-shadow: 0 16px 40px rgba(15, 23, 42, 0.15);
            animation: fadeIn 0.2s ease-out;
            text-align: center;
            position: relative;
            overflow: hidden;
        }
        .result-card::before {
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 4px;
            background: linear-gradient(90deg, #ff5a6e, #ff8a5c);
        }
        .result-icon {
            width: 56px; height: 56px; margin: 0 auto 14px;
            border-radius: 18px;
            background: linear-gradient(135deg, #ffe3e7, #ffd4c4);
            display: flex; align-items: center; justify-content: center;
            font-size: 28px;
        }
        .result-title { font-size: 17px; font-weight: 700; color: #1e293b; margin-bottom: 6px; }
        .result-name { font-size: 12.5px; color: #64748b; word-break: break-all; margin-bottom: 20px; line-height: 1.4; }
        .result-card .btn-submit { margin-bottom: 10px; }
        .btn-share {
            width: 100%;
            padding: 14px;
            background: #fff;
            color: #ff5a6e;
            border: 1.5px solid #ffccd3;
            border-radius: 12px;
            font-size: 14.5px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-share:hover { background: #fff5f6; }
        .btn-share:active { transform: scale(0.98); }
        .result-close {
            display: block;
            text-align: center;
            margin-top: 14px;
            font-size: 13px;
            color: #94a3b8;
            cursor: pointer;
        }
        .result-close:hover { color: #64748b; }

        /* 底部 Cookie 状态药丸 */
        .cookie-pill {
            position: fixed;
            bottom: 20px;
            background: #fff;
            padding: 8px 18px;
            border-radius: 30px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
            cursor: pointer;
            font-size: 12.5px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 6px;
            user-select: none;
            transition: transform 0.15s;
        }
        .cookie-pill:active { transform: scale(0.97); }
        .c-blue { color: #2563eb; font-weight: 600; }
        .c-green { color: #16a34a; font-weight: 600; }
        .c-red { color: #dc2626; font-weight: 600; }

        /* 悬浮 Toast 提示 */
        #toast {
            position: fixed;
            top: 24px;
            background: #1e293b;
            color: #fff;
            padding: 11px 22px;
            border-radius: 10px;
            font-size: 13.5px;
            font-weight: 500;
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.2);
            display: none;
            z-index: 9999;
            animation: fadeIn 0.2s ease-out;
        }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }

        /* 底部抽屉效果 */
        .drawer-overlay {
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.35);
            backdrop-filter: blur(2px);
            display: none;
            z-index: 100;
        }
        .drawer {
            position: fixed;
            left: 0;
            right: 0;
            bottom: 0;
            background: #fff;
            border-radius: 24px 24px 0 0;
            padding: 26px 22px 38px;
            max-width: 500px;
            margin: 0 auto;
            transform: translateY(100%);
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            z-index: 101;
            box-shadow: 0 -10px 40px rgba(15, 23, 42, 0.12);
        }
        .drawer.open { transform: translateY(0); }
        .drawer-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .drawer-title { font-size: 16px; font-weight: 600; }
        .qr-box {
            text-align: center;
            padding: 16px;
            background: #f9f9f9;
            border-radius: 12px;
            margin-top: 14px;
            display: none;
        }
        .qr-box img { width: 160px; height: 160px; border-radius: 8px; margin-bottom: 8px; }
        .qr-desc { font-size: 12px; color: #666; }
    </style>
</head>
<body>

<div id="toast"></div>

<div class="main-card">
    <div class="card-header">
        <h1>小红书资料转 PDF</h1>
    </div>
    <textarea id="linksInput" placeholder="在此粘贴小红书笔记链接 (支持多条链接换行粘贴)..."></textarea>
    <button class="btn-submit" id="submitBtn" onclick="startConvert()">一键合成 PDF</button>
</div>

<div class="history-section">
    <div class="history-title">最近合成文件 (保留 7 天)</div>
    <div class="history-list" id="historyList">
        <div class="history-item" style="color:#999; justify-content:center;">暂无生成记录</div>
    </div>
</div>

<div class="disclaimer">
    <strong>版权免责声明</strong><br>
    本工具仅供个人学习交流使用,版权归原作者所有。
    <div style="margin-top:8px;">
        <span><a href="https://github.com/Daey886/xhs-study-pdf" target="_blank" style="color:#64748b;text-decoration:none;">GitHub 源码与反馈</a></span>
    </div>
</div>

<!-- 生成结果弹窗 -->
<div class="result-overlay" id="resultOverlay">
    <div class="result-card">
        <div class="result-icon">✓</div>
        <div class="result-title" id="resultTitle">PDF 合成完成</div>
        <div class="result-name" id="resultName"></div>
        <button class="btn-submit" onclick="downloadResult()">立即下载 PDF</button>
        <button class="btn-share" onclick="copyResultLink()">复制分享链接</button>
        <div class="result-close" onclick="closeResult()">关闭</div>
    </div>
</div>

<!-- 底部 Cookie 状态药丸 -->
<div class="cookie-pill" onclick="openDrawer()">
    <span>Cookie</span>
    <span id="pillCounts"><span class="c-blue">0</span>-<span class="c-green">0</span>-<span class="c-red">0</span></span>
</div>

<!-- 抽屉覆盖层与抽屉 -->
<div class="drawer-overlay" id="drawerOverlay" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer">
    <div class="drawer-header">
        <div class="drawer-title">Cookie 池状态</div>
        <div style="font-size: 18px; cursor: pointer; color: #888;" onclick="closeDrawer()">×</div>
    </div>
    <div style="font-size: 14px; color: #444; margin-bottom: 16px;">
        当前 <span id="drawerCounts"><span class="c-blue">0</span>-<span class="c-green">0</span>-<span class="c-red">0</span></span>
        <span style="font-size:12px; color:#888; margin-left:6px;">(共计-可用-失效)</span>
    </div>
    <button class="btn-submit" style="background:#2563eb;" onclick="getLoginQr()">获取登录二维码</button>
    <div class="qr-box" id="qrBox">
        <img id="qrImg" alt="二维码" style="display:none;">
        <div id="qrPlaceholder" style="width:160px;height:160px;border-radius:8px;margin:0 auto 8px;background:#eee;"></div>
        <div class="qr-desc" id="qrStatusText">请使用小红书 App 扫描上方二维码</div>
    </div>
</div>

<script>
function showToast(msg, isErr = false) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.background = isErr ? '#dc2626' : '#111';
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; }, 3500);
}

function shareUrlFor(shortCode, filename) {
    // 优先使用短码,回退到完整文件名(兼容历史链接)
    const token = shortCode || filename || '';
    return window.location.origin + '/s/' + encodeURIComponent(token);
}

function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
    }
    return new Promise((resolve, reject) => {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        try {
            document.execCommand('copy') ? resolve() : reject();
        } catch (e) {
            reject(e);
        }
        document.body.removeChild(ta);
    });
}

function copyShareLink(shortCode, filename) {
    if (!shortCode && !filename) { showToast('缺少文件标识,无法生成分享链接', true); return; }
    copyText(shareUrlFor(shortCode, filename))
        .then(() => showToast('已复制分享链接,可直接发给微信好友'))
        .catch(() => showToast('复制失败,请手动长按复制', true));
}

function refreshHistory() {
    fetch('/api/history')
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('historyList');
            if (!data.files || data.files.length === 0) {
                list.innerHTML = '<div class="history-item" style="color:#999; justify-content:center;">暂无生成记录</div>';
                return;
            }
            list.innerHTML = data.files.map(f => `
                <div class="history-item">
                    <span class="history-name" title="${f.name}">${f.name}</span>
                    <span class="history-actions">
                        <a class="history-btn share" href="javascript:void(0)" data-short="${f.short_code || ''}" data-file="${encodeURIComponent(f.filename)}">分享</a>
                        <a class="history-btn download" href="/downloads/${encodeURIComponent(f.short_code || f.filename)}" target="_blank">下载</a>
                    </span>
                </div>
            `).join('');
            list.querySelectorAll('[data-short]').forEach(el => {
                el.addEventListener('click', () => copyShareLink(el.dataset.short, decodeURIComponent(el.dataset.file)));
            });
        });
}

function refreshCookieStatus() {
    fetch('/api/cookies/status')
        .then(r => r.json())
        .then(data => {
            const html = `<span class="c-blue">${data.total}</span>-<span class="c-green">${data.valid}</span>-<span class="c-red">${data.invalid}</span>`;
            document.getElementById('pillCounts').innerHTML = html;
            document.getElementById('drawerCounts').innerHTML = html;
        });
}

let lastResult = { url: '', filename: '', short_code: '' };

function showResult(res) {
    lastResult = { url: res.download_url, filename: res.filename || '', short_code: res.short_code || '' };
    document.getElementById('resultName').innerText = res.filename || '';
    document.getElementById('resultTitle').innerText = res.cached ? '已复用上次解析结果' : 'PDF 合成完成';
    document.getElementById('resultOverlay').classList.add('open');
}
function closeResult() {
    document.getElementById('resultOverlay').classList.remove('open');
}
function downloadResult() {
    if (lastResult.url) window.location.href = lastResult.url;
}
function copyResultLink() {
    if (!lastResult.short_code && !lastResult.filename) { showToast('文件名缺失,无法生成分享链接', true); return; }
    copyShareLink(lastResult.short_code, lastResult.filename);
}

function startConvert() {
    const text = document.getElementById('linksInput').value.trim();
    if (!text) {
        showToast('请先输入小红书分享链接', true);
        return;
    }
    // 统计粘贴的链接条数,多链接时给出明确等待提示
    const linkCount = text.split(String.fromCharCode(10)).filter(line => line.indexOf('http') !== -1).length;
    const btn = document.getElementById('submitBtn');
    btn.disabled = true;
    if (linkCount > 1) {
        btn.innerText = `正在解析 ${linkCount} 篇笔记,请耐心等待...`;
        showToast(`正在解析多篇笔记(${linkCount} 条),请耐心等待...`);
    } else {
        btn.innerText = '正在抓取并合成 PDF...';
    }
    
    fetch('/api/convert', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: text})
    })
    .then(r => r.json())
    .then(res => {
        btn.disabled = false;
        btn.innerText = '一键合成 PDF';
        if (!res.success) {
            const errMsg = res.error || '合成失败';
            showToast(errMsg, true);
            // 若错误提示与需要登录相关，自动弹出登录引导
            if (res.need_login || errMsg.indexOf('登录') !== -1) {
                setTimeout(() => { openDrawer(); }, 1200);
            }
            return;
        }
        showToast(res.cached ? '已复用上次解析的结果' : '合成完成!');
        showResult(res);
        refreshHistory();
    })
    .catch(err => {
        btn.disabled = false;
        btn.innerText = '一键合成 PDF';
        showToast('网络请求异常，请重试', true);
    });
}

function addManualCookie() {
    const val = document.getElementById('manualCookieInput').value.trim();
    if (!val) { showToast('请输入 Cookie 内容', true); return; }
    fetch('/api/cookies/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({cookie: val})
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showToast('✔ Cookie 已成功添加进池！');
            document.getElementById('manualCookieInput').value = '';
            refreshCookieStatus();
        } else {
            showToast('添加失败', true);
        }
    });
}

function pasteCookie() {
    const input = document.getElementById('manualCookieInput');
    if (!navigator.clipboard || !window.isSecureContext) {
        showToast('当前环境不支持剪贴板读取,请手动粘贴', true);
        return;
    }
    navigator.clipboard.readText()
        .then(text => {
            const cleaned = (text || '').trim();
            if (!cleaned) { showToast('剪贴板为空', true); return; }
            // 若用户粘贴的是纯 web_session 值(无 key=),自动补全为 web_session=
            input.value = cleaned.includes('=') ? cleaned : 'web_session=' + cleaned;
            showToast('已粘贴,请点击「保存并验活」');
        })
        .catch(() => showToast('读取剪贴板失败,请手动 Ctrl+V 粘贴', true));
}

function saveManualCookie() {
    const val = document.getElementById('manualCookieInput').value.trim();
    const resultBox = document.getElementById('manualCookieResult');
    if (!val) { showToast('请先填入或粘贴 Cookie / web_session', true); return; }
    const btn = event ? event.target : null;
    if (btn) { btn.disabled = true; btn.innerText = '验活中...'; }
    fetch('/api/cookies/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({cookie: val})
    })
    .then(r => r.json())
    .then(res => {
        if (btn) { btn.disabled = false; btn.innerText = '保存并验活'; }
        if (res.success) {
            resultBox.style.display = 'block';
            resultBox.style.color = '#16a34a';
            resultBox.innerText = '✔ Cookie 验证通过,已持久化保存,重启依然有效';
            document.getElementById('manualCookieInput').value = '';
            showToast('✔ Cookie 已保存并通过验活');
            refreshCookieStatus();
        } else {
            resultBox.style.display = 'block';
            resultBox.style.color = '#dc2626';
            resultBox.innerText = '✘ 验活失败: ' + (res.error || 'Cookie 无效或已过期,请重新获取');
            showToast(res.error || 'Cookie 验活失败', true);
        }
    })
    .catch(() => {
        if (btn) { btn.disabled = false; btn.innerText = '保存并验活'; }
        showToast('网络请求异常', true);
    });
}

// 抽屉交互
function openDrawer() {
    document.getElementById('drawerOverlay').style.display = 'block';
    document.getElementById('drawer').classList.add('open');
    refreshCookieStatus();
}
function closeDrawer() {
    document.getElementById('drawerOverlay').style.display = 'none';
    document.getElementById('drawer').classList.remove('open');
    document.getElementById('qrBox').style.display = 'none';
}

// 扫码登录轮询
let qrPollTimer = null;
function getLoginQr() {
    const qrBox = document.getElementById('qrBox');
    const qrImg = document.getElementById('qrImg');
    const qrText = document.getElementById('qrStatusText');
    qrBox.style.display = 'block';
    qrImg.style.display = 'none';
    qrImg.removeAttribute('src');
    document.getElementById('qrPlaceholder').style.display = 'block';
    qrText.innerText = '正在向小红书申请登录二维码...';
    
    fetch('/api/qrcode/create')
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                qrText.innerText = '获取二维码失败: ' + (data.error || '超时，请重试');
                document.getElementById('qrPlaceholder').style.display = 'none';
                showToast('获取二维码失败: ' + (data.error || '超时'), true);
                return;
            }
            qrImg.src = data.qr_url;
            qrImg.style.display = 'inline-block';
            document.getElementById('qrPlaceholder').style.display = 'none';
            qrText.innerText = '请使用小红书 App 扫描上方二维码';
            
            if (qrPollTimer) clearInterval(qrPollTimer);
            qrPollTimer = setInterval(() => {
                fetch('/api/qrcode/check?qr_id=' + data.qr_id)
                    .then(r => r.json())
                    .then(poll => {
                        if (poll.status === 'success') {
                            clearInterval(qrPollTimer);
                            qrText.innerText = '✔ 登录成功！Cookie 已写入存储池。';
                            refreshCookieStatus();
                            showToast('✔ 登录成功！账号已存入 Cookie 池');
                            setTimeout(() => {
                                closeDrawer();
                            }, 1500);
                        } else if (poll.status === 'expired') {
                            clearInterval(qrPollTimer);
                            qrText.innerText = '二维码已失效，请重新点击获取。';
                        }
                    });
            }, 2000);
        })
        .catch(() => {
            qrText.innerText = '获取二维码异常';
        });
}

refreshHistory();
refreshCookieStatus();
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

# --- 4. 核心下载与 PDF / ZIP 合成逻辑 ---

# 小红书现役图片 CDN 域名（sns-img-* 已废弃），ci.xiaohongshu.com 为旧站兜底
IMG_HOST_RE = re.compile(
    r'https?://(?:sns-webpic|sns-img|sns-webpic-hd|ci)[a-zA-Z0-9\-]*\.'
    r'(?:xhscdn\.com|xiaohongshu\.com)/[a-zA-Z0-9/_\-\.!]+'
)
YAML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PC_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# 下载图片专用：CDN 有防盗链，必须带 Referer
IMG_HEADERS = {
    'User-Agent': PC_UA,
    'Referer': 'https://www.xiaohongshu.com/',
    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
}


def _normalize_img_url(u, keep_suffix=False):
    """规范化图片 URL。

    keep_suffix=True 时保留 `!nd_dft_wlte` 等签名后缀 —— 小红书图片 CDN
    对去掉后缀或改了协议的 URL 会直接返回 403，所以下载用的链接必须原样保留。
    """
    if not u:
        return None
    cu = u if keep_suffix else u.split('!')[0]
    if cu.startswith('//'):
        return 'https:' + cu
    return cu


def _extract_initial_state(html):
    """从 window.__INITIAL_STATE__ 提取笔记数据。

    小红书的 state 不是严格 JSON：含 `new Map([])`、`undefined` 等 JS 字面量，
    所以用 YAML 容错解析（与 core_engine 的 Converter 同思路）。
    """
    m = re.search(r'<script>window\.__INITIAL_STATE__\s*=\s*(.*?)</script>', html, re.S)
    if not m:
        return None
    raw = m.group(1).strip().rstrip(';')
    cleaned = YAML_ILLEGAL_RE.sub('', raw)
    cleaned = cleaned.replace('new Map([])', '[]').replace('undefined', 'null')
    try:
        import yaml
        return yaml.safe_load(cleaned)
    except Exception:
        return None


def _extract_image_token(url):
    """从 CDN URL 提取图片 token（照上游 XHS-Downloader 的做法，见
    core_engine/source/application/image.py 的 __extract_image_token）。"""
    if not url or not isinstance(url, str) or '://' not in url:
        return None
    return '/'.join(url.split('/')[5:]).split('!')[0]


def _images_from_state(state):
    """从 note.noteDetailMap.*.note.imageList 生成无水印原图链接。

    页面里的直链（sns-webpic-*, WB_DFT）是带小红书 logo+账号水印的展示图；
    上游 XHS-Downloader 的做法是提取图片 token 后拼原图 CDN：
        https://sns-img-bd.xhscdn.com/{token}
    实测（2026-10）：token 链 702KB 无水印，WB_DFT 直链 256KB 有水印。
    """
    imgs = []
    if not isinstance(state, dict):
        return imgs
    nd = (state.get('note') or {}).get('noteDetailMap') or {}
    if not isinstance(nd, dict):
        return imgs
    for _k, v in nd.items():
        note = (v or {}).get('note') or {}
        for im in note.get('imageList') or []:
            token = None
            for ukey in ('urlDefault', 'urlPre', 'url'):
                token = _extract_image_token(im.get(ukey))
                if token:
                    break
            if not token:
                # infoList 兜底
                for info in im.get('infoList') or []:
                    token = _extract_image_token(info.get('url'))
                    if token:
                        break
            if token:
                imgs.append('https://sns-img-bd.xhscdn.com/' + token)
    return imgs


def _title_from_state(state):
    if not isinstance(state, dict):
        return None
    nd = (state.get('note') or {}).get('noteDetailMap') or {}
    if not isinstance(nd, dict):
        return None
    for _k, v in nd.items():
        t = ((v or {}).get('note') or {}).get('title')
        if t:
            return t
    return None


def note_kind_from_state(state):
    """判断笔记类型:返回 'image' / 'video' / None(无法确定)。

    仅当页面明确给出笔记详情时才做类型判定,避免把「删除/私密/网络抖动」
    误判成纯视频。判定依据:
      - note.type == 'video' 或存在 video 字段 -> 'video';
      - 存在 imageList -> 'image'。
    """
    if not isinstance(state, dict):
        return None
    nd = (state.get('note') or {}).get('noteDetailMap') or {}
    if not isinstance(nd, dict):
        return None
    for _k, v in nd.items():
        note = (v or {}).get('note') or {}
        if not isinstance(note, dict):
            continue
        imgs = note.get('imageList') or []
        note_type = (note.get('type') or '').lower()
        # 纯视频笔记优先判定:即便带封面图也不做试卷合成
        if note_type == 'video' or note.get('video'):
            return 'video'
        if imgs:
            return 'image'
    return None


def note_missing_in_html(html):
    """页面命中「笔记不可访问」文案时返回 True(已删除/私密/失效)。"""
    if not html:
        return False
    return any(marker in html for marker in NOTE_MISSING_MARKERS)


def clean_exam_images(temp_imgs):
    """
    智能过滤非试卷杂质图（如博主插播广告、求点赞关注卡片、正方形二维码等）。
    使用无模型轻量启发式算法：尺寸聚类 + A4比例检测 + 色彩饱和度/白底检测。
    """
    if len(temp_imgs) <= 1:
        return temp_imgs

    from PIL import Image, ImageStat
    meta = []
    for p in temp_imgs:
        try:
            with Image.open(p) as im:
                w, h = im.size
                ratio = h / float(w) if w > 0 else 0
                hsv = im.convert('HSV')
                stat = ImageStat.Stat(hsv)
                sat = stat.mean[1]      # 饱和度 (试卷通常接近 0)
                bright = stat.mean[2]   # 亮度 (白底试卷通常 > 235)
                meta.append({'path': p, 'w': w, 'h': h, 'ratio': ratio, 'sat': sat, 'bright': bright})
        except Exception:
            meta.append({'path': p, 'w': 0, 'h': 0, 'ratio': 0, 'sat': 0, 'bright': 0})

    # 统计主导尺寸（绝大多数试卷都是统一标准分辨率）
    sizes = {}
    for m in meta:
        if m['w'] > 0:
            k = (m['w'], m['h'])
            sizes[k] = sizes.get(k, 0) + 1
    
    dominant_size = max(sizes.items(), key=lambda x: x[1])[0] if sizes else None
    dom_count = sizes[dominant_size] if dominant_size else 0

    kept = []
    for m in meta:
        # 判定是否为广告/引流卡片:
        # 1. 长宽比异常 (正方形卡片 ratio < 1.15，横版/极窄 banner ratio > 2.2)
        is_square_or_banner = (m['ratio'] < 1.15 or m['ratio'] > 2.2)
        # 2. 尺寸非主流且色彩丰富/背景非白
        is_isolated_size = (dom_count >= 2 and (m['w'], m['h']) != dominant_size)
        is_colorful_or_dark = (m['sat'] > 3.0 or m['bright'] < 220)

        if is_square_or_banner or (is_isolated_size and is_colorful_or_dark):
            # 识别为博主广告/非试卷杂图，自动剔除并清理临时文件
            try:
                os.remove(m['path'])
            except Exception:
                pass
            continue
        kept.append(m['path'])

    # 兜底：如果全部被过滤（例如全是艺术卡片），则回退全部保留，防止空文件
    if not kept:
        return temp_imgs
    return kept


def make_pdf(temp_imgs):
    """合成 PDF，自动过滤非试卷广告图，并容忍 EXIF 旋转标记。"""
    temp_imgs = clean_exam_images(temp_imgs)
    import img2pdf as _i2p
    try:
        return _i2p.convert(temp_imgs)
    except Exception:
        # 非法 EXIF orientation（rotation=0 之类）会让 img2pdf 直接抛错，
        # 回退成 rotation=ifvalid 忽略掉这些坏标记
        return _i2p.convert(temp_imgs, rotation=_i2p.Rotation.ifvalid)


def parse_note(url, cookie=None):
    """解析笔记,返回 (title, imgs, kind, missing)。

    kind:    'image'(图文) / 'video'(纯视频) / None(解析失败或无法判定)。
    missing: True 表示页面命中「笔记不存在/已删除/私密」文案。
    """
    headers = {
        'User-Agent': PC_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    if cookie:
        headers['Cookie'] = cookie

    # 预处理短链与登录重定向链接:
    # 1. 若为短链(如 xhslink.cn / xhslink.com), 优先单步请求获取真实重定向地址
    # 2. 若直接重定向到了 /login?redirectPath=..., 提取实际的笔记地址
    real_url = url
    try:
        p_init = urlparse(url)
        host_init = (p_init.hostname or '').lower()
        if 'xhslink' in host_init:
            r_hop = requests.get(url, headers={'User-Agent': PC_UA}, allow_redirects=False, timeout=10)
            if r_hop.status_code in (301, 302, 303, 307, 308):
                loc = r_hop.headers.get('Location', '')
                if loc:
                    real_url = loc
    except Exception:
        pass

    try:
        p_check = urlparse(real_url)
        if '/login' in p_check.path and 'redirectPath=' in p_check.query:
            qs = parse_qs(p_check.query)
            red_path = qs.get('redirectPath', [None])[0]
            if red_path:
                real_url = red_path
    except Exception:
        pass

    resp = requests.get(real_url, headers=headers, allow_redirects=True, timeout=15)
    html = resp.text

    # 标题:优先 INITIAL_STATE,回退 <title>
    state = _extract_initial_state(html)
    kind = note_kind_from_state(state)
    title = _title_from_state(state)
    if not title:
        t_match = re.search(r'<title>([^<]+)</title>', html)
        if t_match:
            title = t_match.group(1).split('-')[0].strip()
    if not title:
        title = "未命名笔记"
    clean_title = re.sub(r'[\\/\\\\:\\*\\?"<>\\|]', '_', title).strip()
    title = (clean_title or "未命名笔记")[:30]  # 缩短文件名

    # 1) 首选:INITIAL_STATE 的 imageList(无水印原图,最可靠)
    #    注意:保留 CDN 签名后缀,否则下载 403
    clean_imgs = []
    for u in _images_from_state(state):
        cu = _normalize_img_url(u, keep_suffix=True)
        if cu and cu not in clean_imgs:
            clean_imgs.append(cu)

    # 纯视频笔记直接跳过图片兜底,避免误抓封面图当试卷
    if kind != 'video':
        # 2) 兜底:正则扫页面里的图片 CDN 链接(覆盖 sns-webpic-* 等新域名)
        if not clean_imgs:
            for u in IMG_HOST_RE.findall(html):
                cu = _normalize_img_url(u, keep_suffix=True)
                if not cu:
                    continue
                # 跳过明显不是笔记图的静态资源
                if any(s in cu for s in ('formula-static', '/fe-platform/', '.js', '.ico', '.png')):
                    continue
                if cu not in clean_imgs:
                    clean_imgs.append(cu)

    # 类型兜底:无 INITIAL_STATE 类型但有视频标记时判定为视频
    if kind is None and not clean_imgs:
        if re.search(r'"type"\s*:\s*"video"', html) or '"video"' in html and 'videoUrl' in html:
            kind = 'video'
    elif kind is None and clean_imgs:
        kind = 'image'

    # 笔记被删除/私密/链接无效/需要登录
    need_login = False
    missing = False
    if not clean_imgs and kind is None:
        missing = note_missing_in_html(html)
        if not missing and ('登录' in html or '/login' in getattr(resp, 'url', '')):
            need_login = True

    return title, clean_imgs, kind, missing, need_login

@app.route('/api/convert', methods=['POST'])
def convert():
    # 0) 单 IP 限频 (仅 public 模式):防止刷爆小红书 Cookie 导致封号。
    #    lan 家庭自用版关闭限频。
    if RUN_MODE == 'public':
        allowed, retry_after = convert_limiter.allow(client_ip())
        if not allowed:
            resp = jsonify({
                'success': False,
                'error': f'请求过于频繁,请 {retry_after} 秒后再试(每分钟最多 {RATE_LIMIT_PER_MINUTE} 次)'
            })
            resp.status_code = 429
            resp.headers['Retry-After'] = str(retry_after)
            return resp

    # 用户使用时先轻量同步/刷新 Cookie 状态
    try:
        sync_cookie_statuses()
    except Exception:
        pass

    data = request.json or {}
    text = data.get('text', '')
    if not isinstance(text, str):
        return jsonify({'success': False, 'error': '参数格式错误'})

    raw_urls = re.findall(r'https?://[a-zA-Z0-9\./_\-?&=%:]+', text)

    # 1) 域名白名单:只允许小红书合法域名
    urls = [u for u in raw_urls if is_allowed_share_url(u)]
    if not urls:
        if raw_urls:
            return jsonify({'success': False, 'error': '仅支持小红书链接 (xiaohongshu.com / xhslink.com)'})
        return jsonify({'success': False, 'error': '未找到有效的小红书链接'})

    # 2) 批量上限:最多 5 条,防止超长数组耗尽内存
    if len(urls) > MAX_BATCH_LINKS:
        return jsonify({
            'success': False,
            'error': f'单次最多转换 {MAX_BATCH_LINKS} 条链接,当前 {len(urls)} 条,请分批提交'
        })

    ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
    headers = IMG_HEADERS
    
    # 1. 单个链接：直接生成标题 PDF
    if len(urls) == 1:
        u = urls[0]
        # 命中缓存：直接复用上次解析好的 PDF，不再请求小红书
        nid = extract_note_id(u)
        cpdf, ctitle = cache_get(nid)
        if cpdf:
            clean_ctitle = re.sub(r'[\/\\:\*\?"<>\|]', '_', ctitle).strip()
            out_name = (clean_ctitle or "未命名笔记")[:30] + '.pdf'
            out_path = os.path.join(DOWNLOAD_DIR, out_name)
            with open(cpdf, 'rb') as fsrc, open(out_path, 'wb') as fdst:
                fdst.write(fsrc.read())
            return jsonify({
                'success': True,
                'download_url': f'/downloads/{out_name}',
                'filename': out_name,
                'short_code': short_code_for(out_name),
                'cached': True
            })
        cookie = get_next_cookie()
        title, imgs, kind, missing, need_login = parse_note(u, cookie)
        # 纯视频笔记:友好拦截,不做试卷合成
        if kind == 'video':
            return jsonify({'success': False, 'error': MSG_VIDEO_NOTE})
        if not imgs:
            # 笔记删除/私密/链接无效:直接给出明确友好提示
            if missing:
                return jsonify({'success': False, 'error': MSG_NOTE_MISSING})
            if need_login:
                return jsonify({'success': False, 'error': '该笔记需登录后查看，请在弹出的登录窗口中扫码', 'need_login': True})
            # 解析失败不一定是 Cookie 失效(可能是笔记删除/链接错误),
            # 先做真实健康检查,确认失效才标红,避免误伤
            if cookie and not verify_cookie_health(cookie):
                with cookie_lock:
                    cookies = load_cookies()
                    for c in cookies:
                        if c.get('cookie') == cookie:
                            c['status'] = 'invalid'
                    save_cookies(cookies)
            return jsonify({'success': False, 'error': MSG_NO_IMAGES})
            
        temp_imgs = []
        for idx, img_url in enumerate(imgs):
            try:
                r = requests.get(img_url, headers=IMG_HEADERS, timeout=15)
                if r.status_code == 200:
                    tp = os.path.join(DOWNLOAD_DIR, f"temp_{uuid.uuid4().hex[:6]}_{idx}.jpg")
                    with open(tp, 'wb') as f:
                        f.write(r.content)
                    temp_imgs.append(tp)
            except Exception:
                pass
                
        if not temp_imgs:
            return jsonify({'success': False, 'error': '图片下载失败'})
            
        pdf_filename = f"{title}.pdf"
        pdf_path = os.path.join(DOWNLOAD_DIR, pdf_filename)
        with open(pdf_path, 'wb') as f:
            f.write(make_pdf(temp_imgs))

        for tp in temp_imgs:
            try: os.remove(tp)
            except Exception: pass

        # 写入笔记缓存，下次同一链接直接复用
        cache_put(nid, pdf_path, title)

        return jsonify({
            'success': True,
            'download_url': f'/downloads/{pdf_filename}',
            'filename': pdf_filename,
            'short_code': short_code_for(pdf_filename)
        })

    # 2. 多个链接：序号命名打包成 ZIP
    zip_filename = f"{ts_now}.zip"
    zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)

    generated_pdfs = []
    skipped_video = 0
    skipped_missing = 0
    for idx, u in enumerate(urls, 1):
        nid = extract_note_id(u)
        cpdf, ctitle = cache_get(nid)
        if cpdf:
            # 命中缓存:直接复用,不重新解析
            clean_ctitle = re.sub(r'[\/\\:\*\?"<>\|]', '_', ctitle).strip()
            title = (clean_ctitle or "未命名笔记")[:30]
            seq_str = f"{idx:02d}"
            note_pdf_name = f"{seq_str}-{title}.pdf"
            note_pdf_path = os.path.join(DOWNLOAD_DIR, note_pdf_name)
            with open(cpdf, 'rb') as fsrc, open(note_pdf_path, 'wb') as fdst:
                fdst.write(fsrc.read())
            generated_pdfs.append((note_pdf_path, note_pdf_name))
            continue
        cookie = get_next_cookie()
        title, imgs, kind, missing, need_login = parse_note(u, cookie)
        # 纯视频笔记 / 已删除私密笔记:跳过并累计原因,不透传 500
        if kind == 'video':
            skipped_video += 1
            continue
        if not imgs:
            if missing:
                skipped_missing += 1
            continue
        temp_imgs = []
        for img_idx, img_url in enumerate(imgs):
            try:
                r = requests.get(img_url, headers=IMG_HEADERS, timeout=15)
                if r.status_code == 200:
                    tp = os.path.join(DOWNLOAD_DIR, f"temp_{uuid.uuid4().hex[:6]}_{img_idx}.jpg")
                    with open(tp, 'wb') as f:
                        f.write(r.content)
                    temp_imgs.append(tp)
            except Exception:
                pass
                
        if temp_imgs:
            seq_str = f"{idx:02d}"
            note_pdf_name = f"{seq_str}-{title}.pdf"
            note_pdf_path = os.path.join(DOWNLOAD_DIR, note_pdf_name)
            with open(note_pdf_path, 'wb') as f:
                f.write(make_pdf(temp_imgs))
            generated_pdfs.append((note_pdf_path, note_pdf_name))
            cache_put(nid, note_pdf_path, title)
            
            for tp in temp_imgs:
                try: os.remove(tp)
                except Exception: pass
                
    if not generated_pdfs:
        if skipped_video and not skipped_missing:
            return jsonify({'success': False, 'error': MSG_VIDEO_NOTE})
        if skipped_missing and not skipped_video:
            return jsonify({'success': False, 'error': MSG_NOTE_MISSING})
        if skipped_video and skipped_missing:
            return jsonify({'success': False, 'error': '所选链接中,部分为纯视频笔记、部分已失效或私密,均无法合成试卷'})
        return jsonify({'success': False, 'error': '所有链接均未能解析出无水印图片'})
        
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for fpath, fname in generated_pdfs:
            zipf.write(fpath, arcname=fname)
            try: os.remove(fpath) # 放入 ZIP 后删除单体 PDF，避免冗余
            except Exception: pass
            
    return jsonify({
        'success': True,
        'download_url': f'/downloads/{zip_filename}',
        'filename': zip_filename,
        'short_code': short_code_for(zip_filename)
    })

# --- 5. 历史记录与 Cookie 状态接口 ---
@app.route('/api/history')
def get_history():
    files = []
    try:
        for f in sorted(os.listdir(DOWNLOAD_DIR), key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)), reverse=True):
            if f.endswith('.pdf') or f.endswith('.zip'):
                files.append({
                    'name': f,
                    'filename': f,
                    'short_code': short_code_for(f),
                    'time': os.path.getmtime(os.path.join(DOWNLOAD_DIR, f))
                })
    except Exception:
        pass
    return jsonify({'files': files[:10]})

def cookie_user_id(cookie_str):
    """用 cookie 访问 explore，提取「登录者本人」的 userId 作为身份标识。

    注意：页面里到处都是 "userId"（笔记作者、评论者都有），
    必须 1) 确认 loggedIn:true 处于登录态，2) 只取 userInfo/user 块里的 ID。
    未登录时一律返回 None，绝不返回页面里其它 userId（否则去重会被污染）。
    """
    try:
        headers = {
            'User-Agent': PC_UA,
            'Cookie': cookie_str,
            'Accept': 'text/html,*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        r = requests.get('https://www.xiaohongshu.com/explore', headers=headers, timeout=10)
        if '"loggedIn":true' not in r.text:
            return None
        # 只认「登录者本人」的 userId。实测页面里几十个 "userId" 都是 feed 作者，
        # 唯一属于本人的标记是 "guest":false 紧邻的那个。
        # 绝不能退回通用 re.search('"userId"')，那会抓到 feed 作者导致去重错乱。
        m = (re.search(r'"userInfo":\{[^{}]*?"userId":"([^"]+)"', r.text)
             or re.search(r'"guest":false,"userId":"([^"]+)"', r.text))
        return m.group(1) if m else None
    except Exception:
        return None


def upsert_cookie(new_cookie_str):
    """写入/恢复一条 cookie：同一账号（同 userId）就替换旧记录（含失效的），
    新账号才追加。返回 ('replaced', old_id) 或 ('added', new_id)。
    """
    with cookie_lock:
        cookies = load_cookies()
        uid = cookie_user_id(new_cookie_str)
        if uid:
            # 旧记录里也缓存了 user_id，先比对缓存的
            # （只信有效记录 —— 失效记录的缓存可能是垃圾 ID）
            for c in cookies:
                if c.get('status') == 'valid' and c.get('user_id') == uid:
                    c['cookie'] = new_cookie_str
                    c['status'] = 'valid'
                    c['added_at'] = time.time()
                    save_cookies(cookies)
                    return 'replaced', c['id']
            # 缓存没有 user_id 的旧记录：现场解析每条的 user_id 再比对
            for c in cookies:
                old_uid = c.get('user_id') or cookie_user_id(c.get('cookie', ''))
                if old_uid:
                    c['user_id'] = old_uid
                    if old_uid == uid:
                        c['cookie'] = new_cookie_str
                        c['status'] = 'valid'
                        c['added_at'] = time.time()
                        save_cookies(cookies)
                        return 'replaced', c['id']
            save_cookies(cookies)  # 顺带把补齐的 user_id 持久化
        new_id = str(uuid.uuid4())[:8]
        cookies.append({
            'id': new_id,
            'cookie': new_cookie_str,
            'status': 'valid',
            'added_at': time.time(),
            'user_id': uid,
        })
        save_cookies(cookies)
        return 'added', new_id


@app.route('/api/cookies/add', methods=['POST'])
def add_cookie():
    data = request.json or {}
    raw = data.get('cookie', '').strip()
    if not raw:
        return jsonify({'success': False})
    action, _ = upsert_cookie(raw)
    return jsonify({'success': True, 'action': action})


def _normalize_cookie_input(raw):
    """把用户输入归一化为标准 cookie 串。

    支持:
      - 完整 cookie 串 ("a=1; b=2; web_session=xxx")
      - 仅 web_session 值 ("xxx" 或 "web_session=xxx")
      - 浏览器导出的请求头 Cookie 字符串(允许跨行)
    返回规范化后的 cookie 串;无法解析返回 None。
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().strip(';').strip()
    if not text:
        return None
    # 去掉可能的换行与多余空白,统一分号分隔
    text = re.sub(r'[\r\n]+', ';', text)
    parts = [p.strip() for p in text.split(';') if p.strip()]
    pairs = []
    web_session_val = None
    for p in parts:
        if '=' in p:
            k, _, v = p.partition('=')
            k = k.strip()
            if not k:
                continue
            pairs.append(f"{k}={v.strip()}")
            if k == 'web_session':
                web_session_val = v.strip()
        else:
            # 无 '=' 的片段视为 web_session 的值
            web_session_val = p.strip()
    if web_session_val and not any(k.startswith('web_session=') for k in pairs):
        pairs.append(f"web_session={web_session_val}")
    if not pairs:
        return None
    return "; ".join(pairs)


@app.route('/api/cookies/save', methods=['POST'])
def save_cookie():
    """手动填入/粘贴 Cookie (或仅 web_session),验活成功后持久化保存。

    lan 模式:写入扁平单账号 cookie.json,重启依然有效。
    public 模式:同样走 upsert_cookie 写入中央池。
    """
    data = request.json or {}
    raw = data.get('cookie', '') or data.get('web_session', '')
    cookie_str = _normalize_cookie_input(raw)
    if not cookie_str:
        return jsonify({'success': False, 'error': 'Cookie 内容为空或格式无法解析'})

    # 先验活:只有真实登录态(含 web_session 且 explore 返回 loggedIn:true)才写入
    if not verify_cookie_health(cookie_str):
        return jsonify({
            'success': False,
            'error': 'Cookie 验活失败:未检测到登录态,可能已过期或填写有误,请重新获取',
        })

    action, _ = upsert_cookie(cookie_str)
    return jsonify({'success': True, 'action': action})


def verify_cookie_health(cookie_str):
    # 通过访问用户自省或通知接口核验 Cookie 是否真实有效
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Cookie': cookie_str,
        'Accept': 'application/json, text/plain, */*'
    }
    try:
        r = requests.get('https://www.xiaohongshu.com/explore', headers=headers, timeout=6)
        # 如果未登录，HTML 源码中用户字段 userInfo.userId 为 None 或跳出登录限制
        # 只有在小红书真实处于登录成功态（包含 loggedIn:true）时才判定为有效，否则标红为失效
        if '"loggedIn":true' in r.text:
            return True
        return False
    except Exception:
        return False

@app.route('/api/cookies/status')
def cookie_status():
    cookies = load_cookies()
    changed = False
    valid_count = 0
    invalid_count = 0
    
    for c in cookies:
        is_live = verify_cookie_health(c.get('cookie', ''))
        new_stat = 'valid' if is_live else 'invalid'
        if c.get('status') != new_stat:
            c['status'] = new_stat
            changed = True
        if is_live:
            valid_count += 1
        else:
            invalid_count += 1
            
    if changed:
        save_cookies(cookies)
        
    return jsonify({
        'total': len(cookies),
        'valid': valid_count,
        'invalid': invalid_count
    })

# --- 6. 扫码登录生成与轮询 ---
@app.route('/api/qrcode/create')
def qrcode_create():
    session_id = str(uuid.uuid4())
    def save_cb(cookie_str):
        # 同一账号重新登录 -> 替换旧的失效记录，恢复账号状态
        upsert_cookie(cookie_str)

    start_qr_task(session_id, save_cb)
    # 等待前台拿到二维码
    for _ in range(30):
        time.sleep(0.5)
        sess = active_sessions.get(session_id, {})
        if sess.get('status') == 'ready' and sess.get('qr_data_url'):
            return jsonify({'success': True, 'qr_id': session_id, 'qr_url': sess['qr_data_url']})
        if sess.get('status') == 'failed':
            break

    return jsonify({'success': False, 'error': '生成二维码超时，请重试'})

@app.route('/api/qrcode/check')
def qrcode_check():
    qr_id = request.args.get('qr_id')
    sess = active_sessions.get(qr_id, {})
    status = sess.get('status', 'expired')
    if status == 'success':
        return jsonify({'status': 'success'})
    elif status == 'failed':
        return jsonify({'status': 'expired'})
    return jsonify({'status': 'waiting'})

@app.route('/downloads/<path:filename>')
def serve_download(filename):
    # 支持短码与原始文件名;路径穿越防范:严格清洗,越界一律 404
    real_name = resolve_download_file(filename)
    if not real_name:
        abort(404)
    safe_path = safe_join_under(DOWNLOAD_DIR, real_name)
    if not safe_path:
        abort(404)
    # download_name 会按 RFC 5987 自动编码,还原原始中文文件名
    return send_file(safe_path, as_attachment=True, download_name=real_name)


# --- 7. 转发分享:专属落地页 ---
LANDING_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>__TITLE__ - 试卷分享</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                     "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            background: linear-gradient(160deg, #f6f8fb 0%, #eef2f7 100%);
            color: #1a1a1a;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 28px 18px 40px;
        }
        .wrap { width: 100%; max-width: 480px; }
        .card {
            background: #fff;
            border-radius: 20px;
            padding: 30px 26px 26px;
            box-shadow: 0 10px 34px rgba(30, 50, 90, 0.08);
            border: 1px solid #eef1f5;
            text-align: center;
            position: relative;
            overflow: hidden;
        }
        .card::before {
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 5px;
            background: linear-gradient(90deg, #ff5a6e, #ff8a5c);
        }
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: #ff5a6e;
            background: #fff1f3;
            padding: 5px 12px;
            border-radius: 20px;
            margin-bottom: 18px;
            font-weight: 600;
        }
        .file-icon {
            width: 66px; height: 66px;
            margin: 0 auto 16px;
            border-radius: 18px;
            background: linear-gradient(135deg, #ffe3e7, #ffd4c4);
            display: flex; align-items: center; justify-content: center;
            font-size: 32px;
        }
        .file-name {
            font-size: 18px;
            font-weight: 700;
            line-height: 1.5;
            word-break: break-word;
            margin-bottom: 12px;
            letter-spacing: -0.3px;
        }
        .meta {
            display: flex;
            justify-content: center;
            gap: 18px;
            font-size: 12.5px;
            color: #8a94a6;
            margin-bottom: 26px;
            flex-wrap: wrap;
        }
        .meta span b { color: #4a5568; font-weight: 600; }
        .btn {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            width: 100%;
            padding: 15px;
            border-radius: 13px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            transition: transform 0.12s, box-shadow 0.2s;
            border: none;
        }
        .btn:active { transform: scale(0.98); }
        .btn-primary {
            background: linear-gradient(135deg, #ff5a6e, #ff7a4d);
            color: #fff;
            box-shadow: 0 8px 20px rgba(255, 90, 110, 0.3);
            margin-bottom: 12px;
        }
        .btn-secondary {
            background: #fff;
            color: #2b6cb0;
            border: 1.5px solid #d6e3f5;
        }
        .wechat-tip {
            display: none;
            margin-top: 18px;
            padding: 14px 16px;
            background: #fff8e6;
            border: 1px solid #ffe0a3;
            border-radius: 12px;
            font-size: 13px;
            line-height: 1.7;
            color: #8a6d1a;
            text-align: left;
        }
        .wechat-tip b { color: #b8860b; }
        .wechat-tip .arrow {
            display: inline-block;
            float: right;
            font-size: 18px;
            animation: bounce 1s infinite;
        }
        @keyframes bounce {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-4px); }
        }
        .promo {
            margin-top: 22px;
            text-align: center;
            font-size: 12.5px;
            color: #97a1b2;
        }
        .promo a {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            margin-top: 12px;
            padding: 10px 22px;
            background: #fff;
            color: #ff5a6e;
            border: 1.5px solid #ffd4da;
            border-radius: 24px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            transition: background 0.2s;
        }
        .promo a:active { background: #fff5f6; }
        .footer-note {
            margin-top: 20px;
            font-size: 11px;
            color: #b6bec9;
            text-align: center;
            line-height: 1.6;
        }
        .notfound {
            text-align: center;
            padding: 60px 20px;
            color: #8a94a6;
        }
        .notfound h2 { font-size: 18px; color: #4a5568; margin-bottom: 10px; }
        .notfound a { color: #ff5a6e; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
<div class="wrap">
    __BODY__
</div>
<script>
(function () {
    var ua = navigator.userAgent.toLowerCase();
    var isWechat = ua.indexOf('micromessenger') !== -1;
    if (isWechat) {
        var tip = document.getElementById('wechatTip');
        if (tip) tip.style.display = 'block';
    }
})();
</script>
</body>
</html>
"""


def _human_size(num_bytes):
    try:
        mb = num_bytes / (1024.0 * 1024.0)
        if mb < 0.1:
            return f"{max(num_bytes / 1024.0, 0.1):.0f} KB"
        return f"{mb:.2f} MB"
    except Exception:
        return "未知"


def _render_landing(title, filename, size_text, time_text):
    # 优先使用短码生成预览/下载链接,链接更短更清爽
    code = short_code_for(filename) or quote(filename)
    safe_title = html.escape(title)
    body = f"""
    <div class="card">
        <div class="badge">📄 试卷已就绪</div>
        <div class="file-icon">📝</div>
        <div class="file-name">{safe_title}</div>
        <div class="meta">
            <span>大小 <b>{size_text}</b></span>
            <span>生成于 <b>{time_text}</b></span>
        </div>
        <a class="btn btn-primary" href="/preview/{code}" target="_blank" rel="noopener">
            👁 在线预览
        </a>
        <a class="btn btn-secondary" href="/downloads/{code}">
            ⬇️ 立即下载 PDF
        </a>
        <div class="wechat-tip" id="wechatTip">
            <span class="arrow">↗</span>
            你正在微信中打开。请点击右上角 <b>「···」</b>,选择
            <b>「在浏览器中打开」</b>,即可正常预览与下载试卷。
        </div>
    </div>
    <div class="promo">
        由「小红书资料转PDF 工具」免费提取
        <br>
        <a href="/">✨ 我也要提取资料</a>
    </div>
    <div class="footer-note">
        本工具仅供个人学习交流使用,版权归原作者所有
        <div style="margin-top:8px;">
            <span><a href="https://github.com/Daey886/xhs-study-pdf" target="_blank" style="color:#64748b;text-decoration:none;">GitHub 源码与反馈</a></span>
        </div>
    </div>
    """
    return (LANDING_PAGE
            .replace('__TITLE__', title)
            .replace('__BODY__', body))


def _render_landing_missing():
    body = """
    <div class="card notfound">
        <div class="file-icon" style="background:#f1f3f6;">🔍</div>
        <h2>文件不存在或已过期</h2>
        <p>分享的文件可能已被清理(保留 7 天)。</p>
        <p style="margin-top:18px;"><a href="/">前往首页重新提取 →</a></p>
    </div>
    """
    return (LANDING_PAGE
            .replace('__TITLE__', '文件不存在')
            .replace('__BODY__', body))


@app.route('/s/<path:filename>')
def share_landing(filename):
    real_name = resolve_download_file(filename)
    if not real_name:
        return _render_landing_missing(), 404
    safe_path = safe_join_under(DOWNLOAD_DIR, real_name)
    if not safe_path:
        return _render_landing_missing(), 404

    title = os.path.splitext(real_name)[0]
    try:
        size_text = _human_size(os.path.getsize(safe_path))
    except Exception:
        size_text = "未知"
    try:
        mtime = os.path.getmtime(safe_path)
        dt = datetime.fromtimestamp(mtime)
        time_text = dt.strftime("%Y-%m-%d %H:%M") if dt.date() != datetime.now().date() else dt.strftime("今天 %H:%M")
    except Exception:
        time_text = "未知"

    return _render_landing(title, real_name, size_text, time_text)


@app.route('/preview/<path:filename>')
def preview_pdf(filename):
    # 支持短码与原始文件名;内联预览(非附件下载),路径同样严格清洗
    real_name = resolve_download_file(filename)
    if not real_name:
        abort(404)
    safe_path = safe_join_under(DOWNLOAD_DIR, real_name)
    if not safe_path:
        abort(404)
    resp = send_file(safe_path, mimetype='application/pdf', as_attachment=False, download_name=real_name)
    resp.headers['Content-Disposition'] = f"inline; filename*=UTF-8''{quote(real_name)}"
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


# --- 8. 全局异常处理:屏蔽堆栈,返回友好统一响应 ---
@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': '接口不存在'}), 404
    return ("""
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>页面不存在</title>
    <style>body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f8fb;
    color:#4a5568;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
    .box{text-align:center;padding:40px 24px;}.box .code{font-size:54px;font-weight:800;color:#ff5a6e;}
    .box p{color:#8a94a6;margin:12px 0 22px;}
    .box a{display:inline-block;padding:11px 26px;background:linear-gradient(135deg,#ff5a6e,#ff7a4d);
    color:#fff;border-radius:24px;text-decoration:none;font-weight:600;}</style></head>
    <body><div class="box"><div class="code">404</div>
    <p>你访问的页面不存在或已被移除</p><a href="/">返回首页</a></div></body></html>
    """, 404)


@app.errorhandler(500)
def handle_500(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': '服务器内部错误,请稍后重试'}), 500
    return ("""
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>服务器繁忙</title>
    <style>body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f8fb;
    color:#4a5568;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
    .box{text-align:center;padding:40px 24px;}.box .code{font-size:54px;font-weight:800;color:#ff8a5c;}
    .box p{color:#8a94a6;margin:12px 0 22px;}
    .box a{display:inline-block;padding:11px 26px;background:linear-gradient(135deg,#ff5a6e,#ff7a4d);
    color:#fff;border-radius:24px;text-decoration:none;font-weight:600;}</style></head>
    <body><div class="box"><div class="code">500</div>
    <p>服务器开小差了,请稍后重试</p><a href="/">返回首页</a></div></body></html>
    """, 500)


@app.errorhandler(Exception)
def handle_uncaught(e):
    # HTTPException 交给各自的 handler;其余一律屏蔽堆栈
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unhandled error")
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': '服务器内部错误,请稍后重试'}), 500
    return handle_500(e)

def _parse_args(args=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="xhs-study-pdf 统一 Web 服务 (公网/局域网/NAS/Docker 共享)"
    )
    parser.add_argument(
        "--mode",
        choices=("public", "lan"),
        default=RUN_MODE,
        help="运行模式:public 公网在线版(默认, 启用限频) / lan 家庭自用版(关闭限频)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("XHS_OUTPUT_DIR") or _DEFAULT_DOWNLOAD_DIR,
        help="归档目录(生成的 PDF/ZIP 存放位置),lan 模式推荐指定用户目录",
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址(默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5088, help="监听端口(默认 5088)")
    return parser.parse_args(args)


def run():
    """程序入口:解析参数并应用运行模式/归档目录后启动 Flask。"""
    args = _parse_args()
    global RUN_MODE, DOWNLOAD_DIR

    # 运行模式:参数优先级高于环境变量
    if args.mode in ("public", "lan"):
        RUN_MODE = args.mode

    # 归档目录:参数优先级高于环境变量
    DOWNLOAD_DIR = os.path.abspath(args.output_dir or _DEFAULT_DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    print(f"[xhs-study-pdf] RUN_MODE = {RUN_MODE}")
    print(f"[xhs-study-pdf] output_dir = {DOWNLOAD_DIR}")
    print(f"[xhs-study-pdf] listening on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)


if __name__ == '__main__':
    run()
