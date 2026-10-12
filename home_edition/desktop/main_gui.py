"""Windows / macOS 桌面 GUI 主入口 (现代 Web 壳)。

彻底废弃原先简陋的灰底 tkinter 窗口,改为:
1. 优先使用 pywebview 加载 web_frontend 的精美卡片式 Web UI;
2. 未安装 pywebview 时,自动在后台启动本地 Web 服务并打开系统默认浏览器窗口;
3. 系统托盘常驻 (tray.py),支持打印、局域网共享、关闭拦截。

100% 复用 web_frontend/server.py 的玻璃拟态自适应卡片 UI(动态进度条、
生成结果弹窗、在线阅读器、扫码/Cookie 抽屉),桌面端与局域网端看到同一套
高颜值现代界面。

依赖 core_engine 与 web_frontend.server;托盘交互由 tray.py 提供。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

# 允许以脚本直接运行,同时兼容打包后的包路径
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from home_edition.auth.term_qr import TerminalQR

__all__ = ["XhsGuiApp", "main"]

LAN_PORT = 5088
LOCAL_HOST = "127.0.0.1"


def _lan_ip() -> str:
    """获取本机局域网 IP(跨网段绑定提示用)。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return LOCAL_HOST


class XhsGuiApp:
    """桌面 GUI 应用:托管 Web 服务并弹出 Web 壳窗口。"""

    def __init__(self) -> None:
        self.output_dir = os.path.expanduser("~/Downloads")
        self.server_thread: threading.Thread | None = None
        self._server_ready = threading.Event()
        self._lan_running = False
        self._lan_host = LOCAL_HOST
        self._tray_started = False

        # 托盘与关闭拦截:必须在弹出窗口前构造好,供 on_close 使用
        try:
            from home_edition.desktop.tray import TrayController

            self.tray = TrayController(
                on_minimize=self._minimize_to_tray,
                on_exit=self._quit,
                on_show=self._restore,
            )
            self._use_tray = True
        except Exception:
            self.tray = None
            self._use_tray = False

    # ------------------------------------------------------------------
    # 服务器托管
    # ------------------------------------------------------------------
    def _start_server(self, host: str = LOCAL_HOST, port: int = LAN_PORT) -> None:
        """在后台线程启动 web_frontend/server.py (lan 模式)。

        优先 in-process import(共享同一进程,打包后更可靠),失败则回退
        子进程启动。归档目录通过环境变量 XHS_OUTPUT_DIR 传递给服务。
        """
        os.environ["RUN_MODE"] = "lan"
        os.environ["XHS_OUTPUT_DIR"] = os.path.abspath(self.output_dir)

        def serve() -> None:
            started = self._start_server_inprocess(host, port)
            if not started:
                self._start_server_subprocess(host, port)
            self._server_ready.set()

        self.server_thread = threading.Thread(target=serve, daemon=True)
        self.server_thread.start()

        # 等待服务就绪(最长 10 秒)
        self._server_ready.wait(timeout=10)

    def _start_server_inprocess(self, host: str, port: int) -> bool:
        """in-process 启动 Flask:把 web_frontend 目录加入 sys.path 后 import。"""
        try:
            web_dir = os.path.join(_BASE, "web_frontend")
            if web_dir not in sys.path:
                sys.path.insert(0, web_dir)
            import server as srv  # noqa: E402  (web_frontend/server.py)

            srv.RUN_MODE = "lan"
            srv.DOWNLOAD_DIR = os.path.abspath(self.output_dir)
            os.makedirs(srv.DOWNLOAD_DIR, exist_ok=True)
            self._flask_srv = srv
            srv.app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
            return True
        except Exception as e:
            print("in-process server start failed:", e, file=sys.stderr)
            return False

    def _start_server_subprocess(self, host: str, port: int) -> None:
        """子进程启动 server.py(回退方案)。"""
        import subprocess

        web_dir = os.path.join(_BASE, "web_frontend")
        server_path = os.path.join(web_dir, "server.py")
        env = os.environ.copy()
        env["RUN_MODE"] = "lan"
        env["XHS_OUTPUT_DIR"] = os.path.abspath(self.output_dir)
        subprocess.Popen(
            [
                sys.executable, server_path,
                "--mode", "lan",
                "--host", host,
                "--port", str(port),
                "--output-dir", os.path.abspath(self.output_dir),
            ],
            cwd=web_dir,
            env=env,
        )

    def _wait_http(self, url: str, timeout: float = 8.0) -> bool:
        """轮询等待本地 HTTP 服务可访问。"""
        import urllib.request

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, timeout=1)
                return True
            except Exception:
                time.sleep(0.2)
        return False

    # ------------------------------------------------------------------
    # 窗口 (pywebview 或系统浏览器)
    # ------------------------------------------------------------------
    def _open_window(self, url: str) -> None:
        """优先 pywebview 原生窗口,否则打开系统默认浏览器。"""
        try:
            import webview

            window = webview.create_window(
                "小红书资料转 PDF — 家庭自用版",
                url,
                width=560,
                height=780,
                min_size=(420, 600),
            )
            # 拦截窗口关闭事件:交给托盘询问(最小化/退出)
            window.events.closing += self._on_window_closing
            self._webview_window = window
            self._webview = webview
            # 在 pywebview 主线程中启动托盘后进入事件循环
            webview.start(self._on_webview_start, self._webview_window)
            return
        except Exception:
            pass

        # 回退:打开系统默认浏览器,托盘在后台常驻
        webbrowser.open(url)
        self._restore()  # 触发托盘启动
        # 阻塞保持进程存活(托盘在后台线程运行)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            self._quit()

    def _on_webview_start(self, *args) -> None:
        """pywebview 启动后回调:启动系统托盘。"""
        if self._use_tray and not self._tray_started:
            self._tray_started = self.tray.start_tray()
        self._print_lan_info()

    def _on_window_closing(self) -> None:
        """pywebview 窗口关闭事件:交给托盘控制器询问。"""
        if self._use_tray:
            # 无 tk 根窗口,直接走记忆选择逻辑
            remembered = self.tray.load_remembered()
            if remembered is not None:
                self.tray._apply(remembered)
            else:
                # pywebview 下无法用 tk 弹窗,默认最小化到托盘并提示
                self._minimize_to_tray()
        else:
            self._quit()

    # ------------------------------------------------------------------
    # 托盘 / 退出
    # ------------------------------------------------------------------
    def _minimize_to_tray(self) -> None:
        """最小化到托盘:关闭/隐藏窗口,后台继续运行。"""
        try:
            wv = getattr(self, "_webview_window", None)
            if wv is not None:
                wv.hide()
        except Exception:
            pass
        if self._use_tray and not self._tray_started:
            self._tray_started = self.tray.start_tray()

    def _restore(self) -> None:
        """从托盘恢复窗口。"""
        try:
            wv = getattr(self, "_webview_window", None)
            if wv is not None:
                wv.show()
        except Exception:
            pass

    def _quit(self) -> None:
        """退出应用:停止服务并关闭窗口。"""
        self._stop_lan_server()
        try:
            wv = getattr(self, "_webview_window", None)
            if wv is not None:
                wv.destroy()
        except Exception:
            pass
        try:
            os._exit(0)
        except Exception:
            sys.exit(0)

    # ------------------------------------------------------------------
    # 局域网共享
    # ------------------------------------------------------------------
    def _print_lan_info(self) -> None:
        if not self._lan_running:
            return
        url = f"http://{self._lan_host}:{LAN_PORT}"
        print("\n[局域网共享已开启]")
        print("局域网访问地址:", url)
        print("(请确保手机与电脑处于同一局域网)")
        try:
            qr = TerminalQR().render(url)
            print("扫码访问:\n" + qr)
        except Exception:
            pass

    def _start_lan_server(self) -> None:
        """开启局域网共享:在 0.0.0.0:5088 上重新绑定服务,跨网段可访问。"""
        if self._lan_running:
            return
        self._lan_running = True
        self._lan_host = _lan_ip()

        # 重启服务以 0.0.0.0 绑定(默认 127.0.0.1 仅本机可访问)
        self._stop_server_thread()
        self._server_ready.clear()
        self._start_server(host="0.0.0.0", port=LAN_PORT)
        self._print_lan_info()

    def _stop_lan_server(self) -> None:
        self._lan_running = False

    def _stop_server_thread(self) -> None:
        """尽力停止已运行的 Flask 服务线程(不阻塞)。

        局域网开关由「本机 127.0.0.1」切换为「0.0.0.0」时,需要重启服务线程
        以重新绑定监听地址;in-process Flask 的 dev server 无法优雅关闭,
        这里通过替换线程引用 + 让旧线程自然退出(daemon)来实现。
        """
        self.server_thread = None

    # ------------------------------------------------------------------
    # 打印 / 共享 (保留原生能力)
    # ------------------------------------------------------------------
    def print_pdf(self, pdf_path: str) -> bool:
        """调用系统默认打印程序打印 PDF。"""
        if not pdf_path or not os.path.isfile(pdf_path):
            return False
        if sys.platform.startswith("win"):
            from home_edition.desktop.platform.win_print import print_pdf as _p
        elif sys.platform == "darwin":
            from home_edition.desktop.platform.mac_share import print_pdf as _p
        else:
            return False
        return _p(pdf_path)

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def run(self) -> None:
        # 1) 先启动本机 Web 服务 (127.0.0.1)
        self._start_server(host=LOCAL_HOST, port=LAN_PORT)
        # 2) 打开 Web 壳窗口
        self._open_window(f"http://{LOCAL_HOST}:{LAN_PORT}/")


def main() -> None:
    XhsGuiApp().run()


if __name__ == "__main__":
    main()
