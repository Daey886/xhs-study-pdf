"""Windows 专属:直调系统默认打印机打印 PDF。

通过 ShellExecuteW 的 "print" 动词调用系统默认 PDF 阅读器/打印程序,
用户可在弹出的打印对话框中确认纸张与份数。
"""

from __future__ import annotations

import os
import subprocess
import sys

__all__ = ["print_pdf", "is_windows"]

_WIN32_FOUND = False
try:
    if sys.platform.startswith("win"):
        import ctypes  # noqa: F401

        _WIN32_FOUND = True
except Exception:
    _WIN32_FOUND = False


def is_windows() -> bool:
    return sys.platform.startswith("win")


def print_pdf(pdf_path: str) -> bool:
    """调用系统默认打印程序打印 PDF。

    优先使用 ShellExecuteW 的 "print" 动词;回退到 `start` 命令。
    返回是否成功发起打印。
    """
    if not is_windows():
        return False

    if not os.path.isfile(pdf_path):
        return False

    abs_path = os.path.abspath(pdf_path)

    # 方案 1:ShellExecuteW("print", ...) 直接触发打印动词
    try:
        import ctypes

        result = ctypes.windll.shell32.ShellExecuteW(
            None, "print", abs_path, None, None, 1  # SW_SHOWNORMAL
        )
        # ShellExecuteW 返回值 > 32 表示成功
        if result > 32:
            return True
    except Exception:
        pass

    # 方案 2:回退到 `start "" /p` 命令
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", "/p", abs_path],
            shell=False,
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Windows 一键打印 PDF")
    parser.add_argument("pdf", help="PDF 文件路径")
    args = parser.parse_args()
    ok = print_pdf(args.pdf)
    print("print dispatched:", ok)
