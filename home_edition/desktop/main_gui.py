"""Windows / macOS 桌面 GUI 主界面。

基于 tkinter 的轻量现代界面,包含:
- 链接输入框 + 粘贴按钮;
- 保存位置选择 (浏览按钮选择系统文件夹);
- 生成按钮与进度状态反馈;
- 完成后「打开文件」「打开目录」「一键打印」;
- 「开启局域网共享」复选框:后台线程启动 Web 服务 (0.0.0.0:5088),
  并展示本机局域网 IP 与访问二维码。

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
try:
    from core_engine.extractor import Extractor
    from core_engine.cleaner import ImageCleaner
    from core_engine.pdf_builder import PDFBuilder
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from core_engine.extractor import Extractor
    from core_engine.cleaner import ImageCleaner
    from core_engine.pdf_builder import PDFBuilder

try:
    from home_edition.desktop.tray import TrayController
    from home_edition.auth.term_qr import TerminalQR
except ImportError:
    from tray import TrayController
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "auth"))
    from term_qr import TerminalQR

__all__ = ["XhsGuiApp", "main"]

LAN_PORT = 5088


class XhsGuiApp:
    """桌面 GUI 应用。"""

    def __init__(self) -> None:
        import tkinter as tk
        import tkinter.ttk as ttk

        self.tk = tk
        self.ttk = ttk

        self.root = tk.Tk()
        self.root.title("小红书资料转 PDF — 家庭自用版")
        self.root.geometry("560x460")
        self.root.resizable(False, False)

        self.extractor = Extractor()
        self.output_dir = os.path.expanduser("~/Downloads")
        self.server_thread: threading.Thread | None = None
        self._lan_running = False

        self._build_ui()

        self.tray = TrayController(
            on_minimize=self._minimize_to_tray,
            on_exit=self._quit,
            on_show=self._restore,
        )
        self.root.protocol("WM_DELETE_WINDOW", lambda: self.tray.on_close(self.root))

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        tk = self.tk
        ttk = self.ttk

        pad = {"padx": 16, "pady": 6}

        # 标题
        tk.Label(
            self.root,
            text="小红书试卷 / 课件转 PDF",
            font=("Microsoft YaHei", 15, "bold"),
        ).pack(pady=(20, 4))

        # 链接输入区
        frame = tk.Frame(self.root)
        frame.pack(fill="x", **pad)
        tk.Label(frame, text="小红书笔记链接:").pack(anchor="w")
        self.link_var = tk.StringVar()
        entry = tk.Entry(frame, textvariable=self.link_var, font=("Consolas", 10))
        entry.pack(side="left", fill="x", expand=True, pady=(4, 0))
        tk.Button(frame, text="粘贴", width=8, command=self._paste).pack(
            side="left", padx=(6, 0), pady=(4, 0)
        )

        # 保存位置
        dir_frame = tk.Frame(self.root)
        dir_frame.pack(fill="x", **pad)
        tk.Label(dir_frame, text="保存位置:").pack(anchor="w")
        self.dir_var = tk.StringVar(value=self.output_dir)
        tk.Entry(dir_frame, textvariable=self.dir_var).pack(
            side="left", fill="x", expand=True, pady=(4, 0)
        )
        tk.Button(dir_frame, text="浏览", width=8, command=self._browse_dir).pack(
            side="left", padx=(6, 0), pady=(4, 0)
        )

        # 局域网共享开关
        self.lan_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self.root,
            text="开启局域网共享 (手机/平板可访问本机网页)",
            variable=self.lan_var,
            command=self._toggle_lan,
        ).pack(anchor="w", **pad)

        # 进度条与状态
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", **pad)
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(self.root, textvariable=self.status_var, fg="#64748b").pack()

        # 生成按钮
        self.gen_btn = tk.Button(
            self.root,
            text="一键生成 PDF",
            bg="#ff5a6e",
            fg="white",
            activebackground="#ff7a4d",
            font=("Microsoft YaHei", 12, "bold"),
            relief="flat",
            padx=20,
            pady=8,
            command=self._generate,
        )
        self.gen_btn.pack(pady=(12, 6))

        # 结果操作按钮
        action_frame = tk.Frame(self.root)
        action_frame.pack(pady=4)
        self.open_file_btn = tk.Button(
            action_frame, text="打开文件", width=12, state="disabled", command=self._open_file
        )
        self.open_file_btn.pack(side="left", padx=4)
        self.open_dir_btn = tk.Button(
            action_frame, text="打开目录", width=12, state="disabled", command=self._open_dir
        )
        self.open_dir_btn.pack(side="left", padx=4)
        self.print_btn = tk.Button(
            action_frame, text="一键打印", width=12, state="disabled", command=self._print
        )
        self.print_btn.pack(side="left", padx=4)

        # 局域网地址显示
        self.lan_info = tk.Label(self.root, text="", fg="#2563eb", font=("Consolas", 9))
        self.lan_info.pack(pady=(4, 8))

        self.last_pdf: str | None = None

    # ------------------------------------------------------------------
    # 交互回调
    # ------------------------------------------------------------------
    def _paste(self) -> None:
        try:
            text = self.root.clipboard_get()
        except Exception:
            return
        self.link_var.set(text.strip())

    def _browse_dir(self) -> None:
        from tkinter import filedialog

        d = filedialog.askdirectory(initialdir=self.output_dir)
        if d:
            self.output_dir = d
            self.dir_var.set(d)

    def _set_status(self, msg: str) -> None:
        self.status_var.set(msg)

    def _toggle_lan(self) -> None:
        if self.lan_var.get():
            self._start_lan_server()
        else:
            self._stop_lan_server()

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    def _generate(self) -> None:
        url = self.link_var.get().strip()
        if not url:
            self._set_status("请先粘贴小红书链接")
            return

        output_dir = self.dir_var.get().strip() or self.output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.gen_btn.config(state="disabled")
        self.progress.start(12)
        self._set_status("正在解析笔记并下载图片...")

        def worker() -> None:
            try:
                note = self.extractor.parse_note(url)
                if note.kind == "video":
                    self._done_error("该笔记为纯视频笔记,暂不支持")
                    return
                if not note.images:
                    self._done_error(
                        "笔记不存在、已删除或未解析到图片" if note.missing else "未解析到无水印图片"
                    )
                    return
                self._set_status("正在下载图片...")
                local = self.extractor.download_images(note.images, output_dir)
                if not local:
                    self._done_error("图片下载失败")
                    return
                self._set_status("正在去杂并合成 A4 PDF...")
                kept = ImageCleaner().clean(local, remove_files=True).kept
                pdf_name = f"{note.title}.pdf"
                pdf_path = os.path.join(output_dir, pdf_name)
                PDFBuilder().build(kept)
                import core_engine.pdf_builder as pb

                data = pb.PDFBuilder().build(kept)
                with open(pdf_path, "wb") as f:
                    f.write(data)
                self.last_pdf = pdf_path
                self._done_success(pdf_path)
            except Exception as e:
                self._done_error(f"生成失败:{e}")

        threading.Thread(target=worker, daemon=True).start()

    def _done_success(self, pdf_path: str) -> None:
        self.root.after(0, self._finish_ui, pdf_path, None)

    def _done_error(self, msg: str) -> None:
        self.root.after(0, self._finish_ui, None, msg)

    def _finish_ui(self, pdf_path, error) -> None:
        self.progress.stop()
        self.gen_btn.config(state="normal")
        if error:
            self._set_status(error)
            return
        self._set_status(f"完成:{os.path.basename(pdf_path)}")
        self.open_file_btn.config(state="normal")
        self.open_dir_btn.config(state="normal")
        self.print_btn.config(state="normal")

    def _open_file(self) -> None:
        if self.last_pdf and os.path.exists(self.last_pdf):
            webbrowser.open(self.last_pdf)

    def _open_dir(self) -> None:
        d = self.dir_var.get().strip() or self.output_dir
        if os.path.isdir(d):
            if sys.platform.startswith("win"):
                os.startfile(d)
            elif sys.platform == "darwin":
                os.system(f'open "{d}"')
            else:
                webbrowser.open("file://" + d)

    def _print(self) -> None:
        if not self.last_pdf:
            return
        if sys.platform.startswith("win"):
            from home_edition.desktop.platform.win_print import print_pdf
        elif sys.platform == "darwin":
            from home_edition.desktop.platform.mac_share import print_pdf
        else:
            self._set_status("当前平台不支持系统打印")
            return
        if print_pdf(self.last_pdf):
            self._set_status("已发送到系统打印机")
        else:
            self._set_status("打印启动失败")

    # ------------------------------------------------------------------
    # 局域网共享
    # ------------------------------------------------------------------
    def _start_lan_server(self) -> None:
        if self._lan_running:
            return
        self._lan_running = True

        def serve() -> None:
            os.environ["RUN_MODE"] = "lan"
            os.environ["XHS_OUTPUT_DIR"] = self.dir_var.get() or self.output_dir
            import subprocess

            server_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "web_frontend",
                "server.py",
            )
            subprocess.run(
                [sys.executable, server_path, "--mode", "lan", "--port", str(LAN_PORT)],
                cwd=os.path.dirname(server_path),
            )

        self.server_thread = threading.Thread(target=serve, daemon=True)
        self.server_thread.start()
        time.sleep(0.5)

        ip = self._lan_ip()
        url = f"http://{ip}:{LAN_PORT}"
        self.lan_info.config(text=f"局域网访问地址:{url}\n请用手机浏览器访问")
        print("LAN URL:", url)
        # 打印终端二维码 (也可在此处展示图片二维码,终端降级为字符)
        try:
            qr = TerminalQR().render(url)
            print("扫码访问:\n" + qr)
        except Exception:
            pass

    def _stop_lan_server(self) -> None:
        self._lan_running = False
        self.lan_info.config(text="")

    @staticmethod
    def _lan_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    # ------------------------------------------------------------------
    # 托盘 / 退出
    # ------------------------------------------------------------------
    def _minimize_to_tray(self) -> None:
        self.root.withdraw()
        self.tray.start_tray()

    def _restore(self) -> None:
        self.root.deiconify()

    def _quit(self) -> None:
        self._stop_lan_server()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    XhsGuiApp().run()


if __name__ == "__main__":
    main()
