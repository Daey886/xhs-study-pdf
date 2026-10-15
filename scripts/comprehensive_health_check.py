#!/opt/projects/xiaohongshu-downloader/venv/bin/python3
# -*- coding: utf-8 -*-
"""小红书全链路全环境综合健康检查与巡检体系。

巡检覆盖五大核心维度：
1. 依赖与环境（Python 3.10+、Playwright 驱动、核心依赖库、加密通信库）
2. 服务与网络状态（Systemd 托管的 download-server.service、8088 端口与 HTTP API）
3. 存储与目录权限（web_frontend/data、downloads、cookies.json 读写及磁盘余量）
4. 核心解析与下载全流程闭环验证（针对提取器、HTML/State 解析、图片过滤清洗、PDF 合成引擎的端到端真实测试）
5. 账号登录态 (Cookie) 深度探活与告警链路（Bark + 飞书任务中枢专栏）
"""
import os
import sys
import time
import json
import socket
import shutil
import tempfile
import subprocess
import requests

sys.path.insert(0, "/opt/projects/bark-notification-service")
sys.path.insert(0, "/opt/projects/common")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from bark_sender import send_encrypted_bark
except Exception:
    send_encrypted_bark = None

try:
    from lark_notifier import send_task_hub_message
except Exception:
    send_task_hub_message = None

DATA_DIR = os.path.join(BASE_DIR, "web_frontend", "data")
COOKIE_FILE = os.path.join(DATA_DIR, "cookies.json")
STATE_FILE = os.path.join(BASE_DIR, "scripts", ".full_health_state.json")
SERVICE_NAME = "download-server.service"
LOCAL_URL = "http://127.0.0.1:8088"

BARK_PAYLOAD = {
    "title": "🚨 小红书运行环境异常告警",
    "group": "小红书解析项目",
    "ttl": 600
}

# 通知通道开关：
# 1. ENABLE_BARK: 保留 BARK 接口与模型定义供上传 GitHub 使用；本地生产环境默认可不启用 (False)
# 2. ENABLE_LARK: 本地生产默认接入全局飞书任务中枢通知 (True)
ENABLE_BARK = False
ENABLE_LARK = True

# 开关控制：当前已支持免登录解析，短期关闭 Cookie 状态检查与告警；后续若需强制 Cookie 登录解析时置为 True 即可。
ENABLE_COOKIE_CHECK = False

COOLDOWN_SECONDS = 6 * 3600  # 异常冷却提醒

def check_python_env():
    errors = []
    if sys.version_info < (3, 10):
        errors.append(f"Python 版本过低: {sys.version}")
    for pkg in ["requests", "playwright", "PIL", "reportlab"]:
        try:
            __import__(pkg)
        except ImportError:
            errors.append(f"缺少关键依赖: {pkg}")
    # 加密库兼容性检查: Crypto 或 Cryptodome
    try:
        import Crypto
    except ImportError:
        try:
            import Cryptodome
        except ImportError:
            errors.append("缺少关键依赖: pycryptodome (Crypto/Cryptodome)")
    pw_cli = os.path.join(BASE_DIR, "venv", "bin", "playwright")
    if not os.path.exists(pw_cli):
        errors.append("未找到 playwright 可执行脚本")
    return len(errors) == 0, errors

def check_systemd_and_port():
    res = subprocess.run(["systemctl", "is-active", SERVICE_NAME], capture_output=True, text=True)
    is_active = (res.stdout.strip() == "active")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    port_open = (sock.connect_ex(('127.0.0.1', 8088)) == 0)
    sock.close()
    
    http_ok = False
    try:
        r = requests.get(f"{LOCAL_URL}/api/cookies/status", timeout=5)
        http_ok = (r.status_code == 200)
    except Exception:
        http_ok = False
        
    errors = []
    if not is_active:
        errors.append(f"服务 {SERVICE_NAME} 未运行")
    if not port_open:
        errors.append("8088 端口未监听")
    if not http_ok:
        errors.append("本地 HTTP 接口异常")
    return len(errors) == 0, errors

def check_storage_writable():
    errors = []
    os.makedirs(DATA_DIR, exist_ok=True)
    test_file = os.path.join(DATA_DIR, ".write_test")
    try:
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
    except Exception as e:
        errors.append(f"数据目录无法写入: {e}")
        
    usage = shutil.disk_usage(BASE_DIR)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < 1.0:
        errors.append(f"磁盘剩余空间过低: 仅剩 {free_gb:.2f} GB")
    return len(errors) == 0, errors, free_gb

def check_core_pipeline():
    """验证全流程引擎真实运行能力（URL提取 -> HTML/State解析 -> 去杂清洗 -> PDF渲染生成）"""
    errors = []
    try:
        from core_engine.extractor import Extractor, extract_note_id
        from core_engine.cleaner import clean_exam_images
        from core_engine.pdf_builder import build_pdf
        from PIL import Image

        # 1. 验证 Note ID 提取与白名单识别
        test_url = "https://www.xiaohongshu.com/explore/65e94be9000000000b00c5c4?xsec_token=test"
        nid = extract_note_id(test_url)
        if nid != "65e94be9000000000b00c5c4":
            errors.append("核心 Note ID 提取逻辑异常")

        # 2. 验证 State 解析引擎
        fake_state = {
            "note": {
                "firstNoteId": "65e94be9000000000b00c5c4",
                "noteDetailMap": {
                    "65e94be9000000000b00c5c4": {
                        "note": {
                            "title": "健康巡检综合测试试卷",
                            "type": "normal",
                            "imageList": [
                                {"urlDefault": "https://sns-img-bd.xhscdn.com/1/2/3/4/5/token1!nd_dft"},
                                {"urlDefault": "https://sns-img-bd.xhscdn.com/1/2/3/4/5/token2!nd_dft"}
                            ]
                        }
                    }
                }
            }
        }
        e = Extractor()
        imgs = e._images_from_state(fake_state)
        title = e._title_from_state(fake_state)
        if len(imgs) != 2 or title != "健康巡检综合测试试卷":
            errors.append("State 标题与图片列表解析失败")

        # 3. 验证图片去杂与清洗逻辑
        with tempfile.TemporaryDirectory() as tmp_dir:
            img_paths = []
            for i in range(2):
                im = Image.new("RGB", (1200, 1600), (255, 255, 255))
                p = os.path.join(tmp_dir, f"page_{i}.jpg")
                im.save(p)
                img_paths.append(p)
            
            # 加入一张正方形测试图
            im_ad = Image.new("RGB", (600, 600), (220, 50, 50))
            p_ad = os.path.join(tmp_dir, "ad.jpg")
            im_ad.save(p_ad)
            img_paths.append(p_ad)

            cleaned = clean_exam_images(img_paths)
            if len(cleaned) != 2:
                errors.append("试卷图片去杂清洗引擎工作异常")

            # 4. 验证 PDF 构建引擎
            pdf_bytes = build_pdf(cleaned)
            if not pdf_bytes or len(pdf_bytes) < 500:
                errors.append("PDF 自动渲染合成引擎失败")
    except Exception as e:
        errors.append(f"全链路核心引擎执行异常: {e}")

    return len(errors) == 0, errors

def check_cookies():
    total, valid, invalid = 0, 0, 0
    try:
        r = requests.get(f"{LOCAL_URL}/api/cookies/status", timeout=5)
        if r.status_code == 200:
            d = r.json()
            total = int(d.get("total", 0))
            valid = int(d.get("valid", 0))
            invalid = int(d.get("invalid", 0))
    except Exception:
        pass
        
    if total == 0 and os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                c_list = json.load(f)
            total = len(c_list)
            for c in c_list:
                c_str = c.get("cookie", "")
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Cookie": c_str,
                    "Accept": "application/json, text/plain, */*"
                }
                r = requests.get("https://www.xiaohongshu.com/explore", headers=headers, timeout=6)
                if '"loggedIn":true' in r.text:
                    valid += 1
                else:
                    invalid += 1
        except Exception:
            pass

    return total, valid, invalid

def run_health_check():
    py_ok, py_errs = check_python_env()
    srv_ok, srv_errs = check_systemd_and_port()
    disk_ok, disk_errs, free_gb = check_storage_writable()
    pipe_ok, pipe_errs = check_core_pipeline()
    
    if ENABLE_COOKIE_CHECK:
        c_total, c_valid, c_invalid = check_cookies()
    else:
        c_total, c_valid, c_invalid = 0, 0, 0
    
    # 核心系统健康度(代码引擎、解析管道、系统服务、磁盘均正常)
    system_healthy = py_ok and srv_ok and disk_ok and pipe_ok
    
    summary = []
    summary.append(f"• 🐍 Python与运行环境: {'✅ 正常' if py_ok else '❌ 异常 ' + ', '.join(py_errs)}")
    summary.append(f"• ⚙️ Systemd服务与8088端口: {'✅ 正常运行' if srv_ok else '❌ 故障 ' + ', '.join(srv_errs)}")
    summary.append(f"• 🔬 解析/清洗/PDF生成全流程: {'✅ 正常闭环' if pipe_ok else '❌ 异常 ' + ', '.join(pipe_errs)}")
    summary.append(f"• 💾 存储可写性与磁盘剩余: {'✅ 正常' if disk_ok else '❌ 警告 ' + ', '.join(disk_errs)} (可用 {free_gb:.1f} GB)")
    if ENABLE_COOKIE_CHECK:
        summary.append(f"• 🍪 账号登录态 (Cookie): {'✅ 正常有效' if c_valid > 0 else '⚠️ 全部失效需更新'} (总数: {c_total}, 有效: {c_valid}, 失效: {c_invalid})")
    else:
        summary.append("• 🍪 账号登录态 (Cookie): ⏸️ 检查已挂起（当前为免登录解析模式）")
    
    details_str = "\n".join(summary)
    
    now = time.time()
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            pass
            
    last_state = state.get("last_state")
    last_alert_at = state.get("last_alert_at", 0)
    
    # 出现系统故障时告警
    if not system_healthy:
        is_new_alert = (last_state != "error")
        cooldown_passed = (now - last_alert_at) >= COOLDOWN_SECONDS
        if is_new_alert or cooldown_passed:
            if ENABLE_BARK and send_encrypted_bark:
                send_encrypted_bark(
                    title="🚨 小红书解析全流程出现故障",
                    body=f"小红书解析服务全流程自检出现故障，请尽快排查:\n{details_str}",
                    group=BARK_PAYLOAD["group"],
                    ttl=BARK_PAYLOAD["ttl"]
                )
            if ENABLE_LARK and send_task_hub_message:
                send_task_hub_message(
                    "alert",
                    "🚨 小红书解析全流程故障告警",
                    f"小红书核心解析与下载全流程出现异常：\n\n{details_str}\n\n<font color='grey'>— 小红书自动化健康巡检探针</font>"
                )
            state["last_alert_at"] = now
        state["last_state"] = "error"
    else:
        state["last_state"] = "healthy"
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Core pipeline and system are healthy.")
            
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)
        
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Health check report:")
    print(details_str)
    return 0 if system_healthy else 1

if __name__ == "__main__":
    sys.exit(run_health_check())
