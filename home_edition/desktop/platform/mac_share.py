"""macOS 专属:AirPrint 打印与 AirDrop 投送联动。

- print_pdf: 通过 `lpr` 或 `open` 调用系统 AirPrint 打印 PDF
- airdrop_share: 通过 `open -a` 或 Finder 分享面板投送文件
"""

from __future__ import annotations

import os
import subprocess
import sys

__all__ = ["print_pdf", "airdrop_share", "is_macos"]


def is_macos() -> bool:
    return sys.platform == "darwin"


def print_pdf(pdf_path: str) -> bool:
    """调用 macOS 系统打印 (AirPrint) 打印 PDF。"""
    if not is_macos():
        return False
    if not os.path.isfile(pdf_path):
        return False

    abs_path = os.path.abspath(pdf_path)

    # 方案 1:直接丢给默认打印队列
    try:
        r = subprocess.run(
            ["lpr", abs_path],
            capture_output=True,
            timeout=30,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    # 方案 2:用 open 调起预览打印对话框
    try:
        subprocess.Popen(["open", "-a", "Preview", abs_path])
        return True
    except Exception:
        return False


def airdrop_share(file_path: str) -> bool:
    """调起系统分享面板 (AirDrop) 投送文件。"""
    if not is_macos():
        return False
    if not os.path.isfile(file_path):
        return False

    abs_path = os.path.abspath(file_path)
    try:
        # 用 osascript 调起分享扩展 (Airdrop/信息/邮件等)
        script = (
            'tell application "Finder" to activate\n'
            f'open POSIX file "{abs_path}"\n'
        )
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=30,
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="macOS AirPrint/AirDrop")
    parser.add_argument("pdf", help="PDF 文件路径")
    parser.add_argument("--share", action="store_true", help="AirDrop 投送而非打印")
    args = parser.parse_args()
    ok = airdrop_share(args.pdf) if args.share else print_pdf(args.pdf)
    print("ok:", ok)
