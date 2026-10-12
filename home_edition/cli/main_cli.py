"""Linux Server 命令行入口。

支持:
- `-u/--url`       单链接直接下载并生成 PDF 到本地;
- `--batch`        批量处理文本文件 (每行一条链接);
- `--serve`        在服务器上常驻启动 Web 界面 (复用 web_frontend.server);
- `--output-dir`   自定义归档目录;
- `--login`        扫码登录 (终端打印二维码)。

依赖 core_engine,与公网版完全共享算法。
"""

from __future__ import annotations

import argparse
import os
import sys

# 允许直接脚本运行 + 包导入
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from core_engine.extractor import Extractor
from core_engine.cleaner import ImageCleaner
from core_engine.pdf_builder import PDFBuilder

__all__ = ["main", "convert_single", "convert_batch"]


def convert_single(url: str, output_dir: str) -> str:
    """转换单条链接,返回生成的 PDF 路径;失败抛异常。"""
    os.makedirs(output_dir, exist_ok=True)
    ext = Extractor()
    note = ext.parse_note(url)
    if note.kind == "video":
        raise RuntimeError("该笔记为纯视频笔记,暂不支持")
    if not note.images:
        raise RuntimeError("未解析到无水印图片")
    local = ext.download_images(note.images, output_dir)
    if not local:
        raise RuntimeError("图片下载失败")
    kept = ImageCleaner().clean(local, remove_files=True).kept
    pdf_path = os.path.join(output_dir, f"{note.title}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(PDFBuilder().build(kept))
    return pdf_path


def convert_batch(batch_file: str, output_dir: str) -> list[str]:
    """批量转换,返回成功生成的 PDF 路径列表。"""
    with open(batch_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    results: list[str] = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url[:60]}")
        try:
            path = convert_single(url, output_dir)
            results.append(path)
            print(f"  -> {os.path.basename(path)}")
        except Exception as e:
            print(f"  -> 失败: {e}")
    return results


def do_login() -> None:
    """终端扫码登录,打印 ANSI 二维码。"""
    from home_edition.auth.qr_login import XhsQRLogin
    from home_edition.auth.term_qr import TerminalQR

    login = XhsQRLogin()

    def on_qr(qr_id: str, qr_url: str) -> None:
        print("\n请使用小红书 App 扫描下方二维码登录:\n")
        TerminalQR().print(qr_url)
        print("\n(若终端二维码无法识别,可打开链接:", qr_url, ")\n")

    def on_status(status: str) -> None:
        from home_edition.auth.qr_login import QrStatus

        msg = {
            QrStatus.WAITING: "等待扫码...",
            QrStatus.SCANNED: "已扫码,请在手机确认...",
            QrStatus.SUCCESS: "登录成功!",
            QrStatus.EXPIRED: "二维码已失效",
            QrStatus.FAILED: "登录超时/失败",
        }.get(status, status)
        print(msg)

    cookie = login.login(on_qr=on_qr, on_status=on_status)
    if cookie:
        print("Cookie 已保存到本地 cookie.json")
    else:
        print("登录失败或已取消", file=sys.stderr)
        sys.exit(1)


def do_serve(output_dir: str, port: int) -> None:
    """启动 Web 服务。"""
    web_dir = os.path.join(_BASE, "web_frontend")
    server_path = os.path.join(web_dir, "server.py")
    os.environ["RUN_MODE"] = "lan"
    os.environ["XHS_OUTPUT_DIR"] = output_dir
    import subprocess

    subprocess.run(
        [sys.executable, server_path, "--mode", "lan", "--port", str(port)],
        cwd=web_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhs-study-pdf",
        description="小红书资料转 PDF 家庭自用版命令行工具",
    )
    parser.add_argument("-u", "--url", help="单条小红书笔记链接")
    parser.add_argument("--batch", help="批量处理:包含链接的文本文件 (每行一条)")
    parser.add_argument("--serve", action="store_true", help="启动本地 Web 服务")
    parser.add_argument("--login", action="store_true", help="扫码登录")
    parser.add_argument(
        "--output-dir",
        default=os.path.join(os.path.expanduser("~"), "xhs_pdf"),
        help="归档目录 (默认 ~/xhs_pdf)",
    )
    parser.add_argument("--port", type=int, default=8088, help="Web 服务端口 (默认 8088)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.login:
        do_login()
        return 0

    if args.serve:
        do_serve(args.output_dir, args.port)
        return 0

    if args.batch:
        results = convert_batch(args.batch, args.output_dir)
        print(f"\n共成功 {len(results)} 个,输出目录: {args.output_dir}")
        return 0

    if args.url:
        try:
            path = convert_single(args.url, args.output_dir)
            print(f"生成成功: {path}")
            return 0
        except Exception as e:
            print(f"生成失败: {e}", file=sys.stderr)
            return 1

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
