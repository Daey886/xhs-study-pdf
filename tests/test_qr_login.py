"""home_edition.auth.qr_login 单元测试 (不触网,仅测逻辑与持久化)。"""

import json
import os

from home_edition.auth.qr_login import (
    COOKIE_PATH,
    QrStatus,
    XhsQRLogin,
)


def test_qr_status_constants():
    assert QrStatus.WAITING == "waiting"
    assert QrStatus.SUCCESS == "success"


def test_save_cookie_writes_flat_json(tmp_path):
    login = XhsQRLogin(cookie_path=str(tmp_path / "cookie.json"))
    login._save_cookie("web_session=abc123; a=b")
    assert os.path.exists(str(tmp_path / "cookie.json"))
    with open(str(tmp_path / "cookie.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["cookie"] == "web_session=abc123; a=b"
    assert data["status"] == "valid"


def test_extract_cookie_from_list():
    result = {
        "cookies": [
            {"name": "web_session", "value": "ws123"},
            {"name": "a1", "value": "b1"},
        ]
    }
    cookie = XhsQRLogin._extract_cookie(result)
    assert "web_session=ws123" in cookie
    assert "a1=b1" in cookie


def test_extract_cookie_from_dict():
    result = {"cookies": {"web_session": "ws456", "x": "y"}}
    cookie = XhsQRLogin._extract_cookie(result)
    assert "web_session=ws456" in cookie


def test_extract_cookie_empty():
    assert XhsQRLogin._extract_cookie({}) is None


def test_cookie_path_default():
    assert COOKIE_PATH.endswith("cookie.json")
