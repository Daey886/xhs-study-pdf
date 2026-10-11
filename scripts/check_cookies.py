#!/opt/projects/xiaohongshu-downloader/venv/bin/python3
# -*- coding: utf-8 -*-
"""小红书 Cookie 探活监控脚本。

逻辑:
  1. 优先请求本地服务 `http://127.0.0.1:8088/api/cookies/status`;
  2. 本地服务不可用时,回退直接读取 `web_frontend/data/cookies.json`
     并逐条探活(xiaohongshu.com/explore 是否含 loggedIn:true);
  3. 当 `valid == 0`(无任何有效 Cookie)时,经 Bark 推送告警。

防轰炸:
  - 通过状态文件 `.cookie_alert_state.json` 记录上次状态与告警时间;
  - 状态未变(持续失效)时仅在冷却期(默认 12 小时)结束后才重复告警;
  - 由失效恢复到有效时,重置状态以便下次故障能立即告警。

支持 cron 静默执行:
  0 */4 * * * cd /opt/projects/xiaohongshu-downloader && \
      ./venv/bin/python scripts/check_cookies.py >> /tmp/xhs_cookie_check.log 2>&1
"""
import json
import os
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_STATUS_URL = "http://127.0.0.1:8088/api/cookies/status"
COOKIE_FILE = os.path.join(BASE_DIR, "web_frontend", "data", "cookies.json")
STATE_FILE = os.path.join(BASE_DIR, "scripts", ".cookie_alert_state.json")

BARK_API = "https://api.day.app/xFQHJJkwXgxsTkKvvQrs7a"
BARK_PAYLOAD = {
    "title": "小红书 Cookie 失效告警",
    "body": "小红书解析 Cookie 已全部失效,请及时扫码或更新 cookies.json!",
    "group": "小红书解析项目",
    "ttl": 600,
}

COOLDOWN_SECONDS = 12 * 3600  # 持续失效时,最多每 12 小时提醒一次
HTTP_TIMEOUT = 8


def _log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def check_via_local_api():
    """请求本地服务状态接口,成功返回 (total, valid, invalid),失败返回 None。"""
    try:
        r = requests.get(LOCAL_STATUS_URL, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        return int(data.get("total", 0)), int(data.get("valid", 0)), int(data.get("invalid", 0))
    except Exception:
        return None


def _verify_cookie_health(cookie_str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie_str,
        "Accept": "application/json, text/plain, */*",
    }
    try:
        r = requests.get("https://www.xiaohongshu.com/explore", headers=headers, timeout=HTTP_TIMEOUT)
        return '"loggedIn":true' in r.text
    except Exception:
        return False


def check_via_cookie_file():
    """本地服务不可用时,直接读取 cookies.json 并探活。"""
    if not os.path.exists(COOKIE_FILE):
        _log("未找到 Cookie 文件: %s" % COOKIE_FILE)
        return 0, 0, 0
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception as e:
        _log("解析 Cookie 文件失败: %s" % e)
        return 0, 0, 0
    if not isinstance(cookies, list):
        return 0, 0, 0
    valid = sum(1 for c in cookies if _verify_cookie_health(c.get("cookie", "")))
    total = len(cookies)
    return total, valid, total - valid


def send_bark():
    try:
        r = requests.post(BARK_API, json=BARK_PAYLOAD, timeout=HTTP_TIMEOUT)
        ok = r.status_code == 200 and (r.json().get("code") in (200, 0, None))
        _log("Bark 告警%s (HTTP %s)" % ("发送成功" if ok else "返回异常", r.status_code))
        return ok
    except Exception as e:
        _log("Bark 告警发送失败: %s" % e)
        return False


def main():
    total, valid, invalid = 0, 0, 0
    source = check_via_local_api()
    if source is None:
        _log("本地服务不可用,回退直接读取 cookies.json 探活")
        total, valid, invalid = check_via_cookie_file()
    else:
        total, valid, invalid = source
        _log("本地接口状态: total=%d valid=%d invalid=%d" % (total, valid, invalid))

    now = time.time()
    state = _load_state()
    last_state = state.get("last_state")
    last_alert = float(state.get("last_alert_at", 0))

    if valid == 0:
        # 状态由「正常」变为「全部失效」时立即告警
        became_invalid = last_state != "invalid"
        cooldown_passed = (now - last_alert) >= COOLDOWN_SECONDS
        if became_invalid or cooldown_passed:
            if send_bark():
                state["last_alert_at"] = now
        else:
            _log("处于冷却期(%.1f 小时),跳过重复告警" % ((COOLDOWN_SECONDS - (now - last_alert)) / 3600.0))
        state["last_state"] = "invalid"
    else:
        if last_state == "invalid":
            _log("Cookie 已恢复有效,重置告警状态")
        state["last_state"] = "valid"

    _save_state(state)
    # 返回码:0 正常;1 表示当前存在失效(便于 cron/monitor 采样)
    return 0 if valid > 0 else 1


if __name__ == "__main__":
    sys.exit(main())