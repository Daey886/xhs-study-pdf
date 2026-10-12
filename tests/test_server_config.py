"""web_frontend.server 运行模式与归档目录配置的单元测试。

通过子进程以 --mode 参数启动 server 并验证其参数解析 (不实际请求小红书)。
"""

import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_server_argparse_help():
    """验证 --mode/--output-dir/--host/--port 参数被正确注册。"""
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "web_frontend", "server.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0
    assert "--mode" in r.stdout
    assert "--output-dir" in r.stdout
    assert "--host" in r.stdout
    assert "--port" in r.stdout


def test_server_rejects_bad_mode():
    r = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "web_frontend", "server.py"),
            "--mode",
            "bogus",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # argparse choices 会返回非 0 并提示错误
    assert r.returncode != 0
    assert "invalid choice" in r.stderr.lower() or "invalid choice" in r.stderr


def test_server_default_port_is_5088():
    """验证系统全局默认端口已收敛统一为 5088。"""
    import importlib.util
    import sys as _sys

    web_dir = os.path.join(ROOT, "web_frontend")
    if web_dir not in _sys.path:
        _sys.path.insert(0, web_dir)

    spec = importlib.util.spec_from_file_location(
        "xhs_server", os.path.join(web_dir, "server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    args = mod._parse_args([])
    assert args.port == 5088


def test_normalize_cookie_input():
    """验证 Cookie 手动输入归一化逻辑(完整串 / 仅 web_session 值 / 跨行)。"""
    import importlib.util
    import sys as _sys

    web_dir = os.path.join(ROOT, "web_frontend")
    if web_dir not in _sys.path:
        _sys.path.insert(0, web_dir)

    spec = importlib.util.spec_from_file_location(
        "xhs_server_norm", os.path.join(web_dir, "server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 完整 cookie 串
    full = mod._normalize_cookie_input("a1=b1; web_session=ws123")
    assert "web_session=ws123" in full

    # 仅 web_session 值(无 key=)
    only_val = mod._normalize_cookie_input("ws456")
    assert only_val == "web_session=ws456"

    # 带 key 的 web_session
    keyed = mod._normalize_cookie_input("web_session=abc")
    assert keyed == "web_session=abc"

    # 跨行 + 尾随分号
    multiline = mod._normalize_cookie_input("a1=b1;\nweb_session=zzz;")
    assert "web_session=zzz" in multiline

    # 空输入
    assert mod._normalize_cookie_input("") is None
    assert mod._normalize_cookie_input("   ") is None
