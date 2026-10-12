"""本地单账号小红书二维码登录模块。

面向家庭/个人自用版:通过官方二维码接口生成登录二维码 -> 轮询扫码状态
-> 提取 `web_session` -> 持久化到本地 `cookie.json` (扁平结构)。

与公网版中央 Cookie 池不同,这里只维护一个本机账号,扫码一次长期有效。

依赖:requests (可选 playwright 由 web_frontend/qr_service 复用)。
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

__all__ = ["XhsQRLogin", "QrStatus", "login_via_qr", "COOKIE_PATH"]

COOKIE_PATH = os.path.join(os.path.dirname(__file__), "cookie.json")

PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 小红书二维码登录相关接口 (域名可能随平台调整,失败时可回退 playwright 方案)
QR_CREATE_URL = "https://www.xiaohongshu.com/api/account/login/v2/qrcode/create"
QR_STATUS_URL = "https://www.xiaohongshu.com/api/account/login/v2/qrcode/status"

# 轮询状态码
_STATUS_SCANNED = "SCANNED"
_STATUS_CONFIRMED = "CONFIRMED"
_STATUS_EXPIRED = "EXPIRED"


class QrStatus:
    """扫码轮询状态常量。"""

    WAITING = "waiting"
    SCANNED = "scanned"
    SUCCESS = "success"
    EXPIRED = "expired"
    FAILED = "failed"


class XhsQRLogin:
    """小红书二维码登录器。"""

    def __init__(
        self,
        cookie_path: str = COOKIE_PATH,
        user_agent: str = PC_UA,
        poll_interval: float = 2.0,
        timeout: float = 90.0,
    ) -> None:
        self.cookie_path = cookie_path
        self.user_agent = user_agent
        self.poll_interval = poll_interval
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://www.xiaohongshu.com",
            "Referer": "https://www.xiaohongshu.com/",
        }

    def create_qr(self) -> dict:
        """请求生成二维码,返回 {qr_id, qr_url}。"""
        resp = requests.post(
            QR_CREATE_URL, headers=self._headers(), json={}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def check_status(self, qr_id: str, code: str) -> dict:
        """轮询扫码状态,返回原始 JSON。"""
        resp = requests.get(
            QR_STATUS_URL,
            headers=self._headers(),
            params={"qr_id": qr_id, "code": code},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def login(
        self,
        on_qr: Optional[Callable[[str, str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """执行完整扫码登录流程。

        :param on_qr:     回调 (qr_id, qr_url),用于把二维码展示给用户
        :param on_status: 回调 (status),用于进度提示
        :return: 成功返回 web_session,失败/超时返回 None
        """
        data = self.create_qr()
        qr_id = data.get("qr_id") or data.get("qrId")
        qr_url = data.get("url") or data.get("qr_url")
        code = data.get("code", "")

        if not qr_id or not qr_url:
            # 直接接口受限:回退到 playwright 方案
            return self._login_via_playwright(on_qr, on_status)

        if on_qr:
            on_qr(qr_id, qr_url)
        if on_status:
            on_status(QrStatus.WAITING)

        start = time.time()
        while time.time() - start < self.timeout:
            try:
                result = self.check_status(qr_id, code)
            except Exception:
                result = {}

            status = result.get("status") or result.get("code_status")
            if status in (_STATUS_CONFIRMED, "SUCCESS", 2):
                cookie = self._extract_cookie(result)
                if cookie:
                    self._save_cookie(cookie)
                    if on_status:
                        on_status(QrStatus.SUCCESS)
                    return cookie
            elif status in (_STATUS_SCANNED, "SCANNED", 1):
                if on_status:
                    on_status(QrStatus.SCANNED)
            elif status in (_STATUS_EXPIRED, "EXPIRED", 3):
                if on_status:
                    on_status(QrStatus.EXPIRED)
                return None

            time.sleep(self.poll_interval)

        if on_status:
            on_status(QrStatus.FAILED)
        return None

    # ------------------------------------------------------------------
    # 回退方案:复用 playwright 扫码 (与公网版一致)
    # ------------------------------------------------------------------
    def _login_via_playwright(self, on_qr, on_status) -> Optional[str]:
        try:
            from web_frontend.qr_service import start_qr_task, active_sessions
        except ImportError:
            return None

        import uuid

        session_id = str(uuid.uuid4())
        result_holder: dict = {}

        def _cb(cookie_str: str) -> None:
            result_holder["cookie"] = cookie_str
            self._save_cookie(cookie_str)

        start_qr_task(session_id, _cb)
        # 等待二维码就绪
        for _ in range(30):
            sess = active_sessions.get(session_id, {})
            if sess.get("status") == "ready" and sess.get("qr_data_url"):
                if on_qr:
                    on_qr(session_id, sess["qr_data_url"])
                break
            if sess.get("status") == "failed":
                return None
            time.sleep(0.5)

        start = time.time()
        while time.time() - start < self.timeout:
            sess = active_sessions.get(session_id, {})
            if sess.get("status") == "success":
                return result_holder.get("cookie")
            if sess.get("status") == "failed":
                return None
            time.sleep(self.poll_interval)
        return None

    # ------------------------------------------------------------------
    # Cookie 提取与持久化
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_cookie(result: dict) -> Optional[str]:
        """从扫码结果中提取完整 Cookie 串,优先 web_session。"""
        cookies = result.get("cookies") or result.get("cookie_list") or []
        if isinstance(cookies, dict):
            cookies = [{"name": k, "value": v} for k, v in cookies.items()]
        web_session = None
        parts = []
        for c in cookies:
            name = c.get("name")
            value = c.get("value")
            if not name or not value:
                continue
            if name == "web_session":
                web_session = value
            parts.append(f"{name}={value}")
        cookie_str = "; ".join(parts)
        return cookie_str or (("web_session=" + web_session) if web_session else None)

    def _save_cookie(self, cookie_str: str) -> None:
        os.makedirs(os.path.dirname(self.cookie_path), exist_ok=True)
        record = {
            "id": "local",
            "cookie": cookie_str,
            "status": "valid",
            "added_at": time.time(),
        }
        with open(self.cookie_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)


def login_via_qr(
    cookie_path: str = COOKIE_PATH,
    on_qr: Optional[Callable[[str, str], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """便捷封装:执行扫码登录并返回 web_session/cookie 串。"""
    return XhsQRLogin(cookie_path=cookie_path).login(on_qr=on_qr, on_status=on_status)
