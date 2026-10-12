"""home_edition.desktop.main_gui 单元测试(不启动窗口/服务,仅测常量与工具函数)。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from home_edition.desktop.main_gui import LAN_PORT, LOCAL_HOST, _lan_ip


def test_lan_port_is_5088():
    assert LAN_PORT == 5088


def test_local_host_is_loopback():
    assert LOCAL_HOST == "127.0.0.1"


def test_lan_ip_returns_string():
    ip = _lan_ip()
    assert isinstance(ip, str)
    assert ip
    # 至少是合法 IPv4 或回退到 127.0.0.1
    parts = ip.split(".")
    assert len(parts) == 4
    assert all(p.isdigit() for p in parts)
