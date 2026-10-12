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
