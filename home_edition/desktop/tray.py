"""系统托盘与窗口关闭交互。

核心职责:
1. 拦截主窗口关闭 (WM_DELETE_WINDOW) 事件;
2. 弹出询问对话框,让用户选择「最小化到系统托盘」或「直接退出」;
3. 支持「记住我的选择」持久化;
4. (Windows/macOS) 提供系统托盘图标与右键菜单。

仅在 pystray 可用时启用真实托盘;否则优雅降级为仅拦截关闭事件的纯逻辑层。
"""

from __future__ import annotations

import json
import os
from typing import Callable, Optional

__all__ = ["TrayController", "CloseChoice", "REMEMBER_FILE"]

# 记忆文件存放于用户主目录,避免污染仓库
REMEMBER_FILE = os.path.join(os.path.expanduser("~"), ".xhs_study_pdf_close_choice.json")

CHOICE_MINIMIZE = "minimize"
CHOICE_EXIT = "exit"


class CloseChoice:
    MINIMIZE = CHOICE_MINIMIZE
    EXIT = CHOICE_EXIT


class TrayController:
    """托盘与关闭交互控制器。

    :param on_minimize: 最小化到托盘回调
    :param on_exit:     直接退出回调
    :param on_show:     从托盘恢复主窗口回调
    """

    def __init__(
        self,
        on_minimize: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        on_show: Optional[Callable[[], None]] = None,
    ) -> None:
        self.on_minimize = on_minimize or (lambda: None)
        self.on_exit = on_exit or (lambda: None)
        self.on_show = on_show or (lambda: None)
        self._remembered: Optional[str] = None

    # ------------------------------------------------------------------
    # 记忆选择
    # ------------------------------------------------------------------
    @staticmethod
    def load_remembered() -> Optional[str]:
        try:
            with open(REMEMBER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            choice = data.get("choice")
            if choice in (CHOICE_MINIMIZE, CHOICE_EXIT):
                return choice
        except Exception:
            pass
        return None

    @staticmethod
    def save_remembered(choice: str) -> None:
        try:
            with open(REMEMBER_FILE, "w", encoding="utf-8") as f:
                json.dump({"choice": choice}, f, ensure_ascii=False)
        except Exception:
            pass

    @staticmethod
    def clear_remembered() -> None:
        try:
            if os.path.exists(REMEMBER_FILE):
                os.remove(REMEMBER_FILE)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 关闭询问
    # ------------------------------------------------------------------
    def on_close(self, root) -> None:
        """处理主窗口关闭事件:优先按记忆执行,否则弹窗询问。"""
        remembered = self.load_remembered()
        if remembered is not None:
            self._apply(remembered)
            return
        self._ask(root)

    def _ask(self, root) -> None:
        """弹出单选对话框询问关闭行为。"""
        import tkinter as tk
        import tkinter.messagebox as mb

        win = tk.Toplevel(root)
        win.title("退出确认")
        win.geometry("360x210")
        win.resizable(False, False)
        win.transient(root)
        win.grab_set()

        choice_var = tk.StringVar(value=CHOICE_MINIMIZE)

        tk.Label(
            win,
            text="关闭窗口后你想要:",
            font=("Microsoft YaHei", 11, "bold"),
        ).pack(pady=(18, 8), anchor="w", padx=20)

        tk.Radiobutton(
            win,
            text="最小化到系统托盘 (后台继续运行)",
            variable=choice_var,
            value=CHOICE_MINIMIZE,
            anchor="w",
        ).pack(anchor="w", padx=24, pady=2)
        tk.Radiobutton(
            win,
            text="直接退出程序",
            variable=choice_var,
            value=CHOICE_EXIT,
            anchor="w",
        ).pack(anchor="w", padx=24, pady=2)

        remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            win, text="记住我的选择 (下次不再询问)", variable=remember_var
        ).pack(anchor="w", padx=24, pady=(10, 12))

        def confirm() -> None:
            choice = choice_var.get()
            if remember_var.get():
                self.save_remembered(choice)
            win.destroy()
            self._apply(choice)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=6)
        tk.Button(btn_frame, text="确定", width=10, command=confirm).pack(
            side="left", padx=6
        )
        tk.Button(
            btn_frame,
            text="取消",
            width=10,
            command=win.destroy,
        ).pack(side="left", padx=6)

        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _apply(self, choice: str) -> None:
        if choice == CHOICE_MINIMIZE:
            self.on_minimize()
        else:
            self.on_exit()

    # ------------------------------------------------------------------
    # 真实系统托盘 (可选)
    # ------------------------------------------------------------------
    def start_tray(self, tooltip: str = "小红书资料转PDF") -> bool:
        """尝试启动系统托盘图标,失败返回 False。"""
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            return False

        image = Image.new("RGB", (64, 64), (255, 90, 110))
        draw = ImageDraw.Draw(image)
        draw.rectangle([12, 20, 52, 44], fill=(255, 255, 255))
        draw.rectangle([24, 28, 40, 36], fill=(255, 90, 110))

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", lambda: self.on_show(), default=True),
            pystray.MenuItem("退出", lambda: self.on_exit()),
        )
        icon = pystray.Icon("xhs_study_pdf", image, tooltip, menu)
        import threading

        threading.Thread(target=icon.run, daemon=True).start()
        return True
