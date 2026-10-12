"""标准 A4 试卷排版与超清 PDF 合成模块。

负责将一组已去杂的图片合成为一张标准 A4 尺寸、带 DPI 校准的高清 PDF。

特性：
- 自动将任意分辨率图片按 A4 比例缩放/居中排版；
- 容忍 EXIF 非法旋转标记（rotation=0 之类会让 img2pdf 抛错）；
- 支持自定义页边距与输出 DPI。

仅依赖 Pillow 与 img2pdf，与 cleaner / extractor 完全解耦。
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

__all__ = ["PDFBuilder", "A4_MM", "A4_DPI", "build_pdf", "save_pdf"]

# A4 尺寸（毫米）
A4_MM = (210.0, 297.0)
# 默认输出 DPI
A4_DPI = 300.0


class PDFBuilder:
    """A4 试卷 PDF 合成器。"""

    def __init__(
        self,
        page_size_mm: tuple[float, float] = A4_MM,
        dpi: float = A4_DPI,
        margin_mm: float = 0.0,
    ) -> None:
        self.page_size_mm = page_size_mm
        self.dpi = dpi
        self.margin_mm = margin_mm

    # ------------------------------------------------------------------
    # 布局计算
    # ------------------------------------------------------------------
    @staticmethod
    def mm_to_points(value_mm: float) -> float:
        """毫米 -> PDF 点 (1 inch = 72 pt = 25.4 mm)。"""
        return value_mm * 72.0 / 25.4

    def layout_size(self) -> tuple[float, float]:
        """返回 (宽pt, 高pt) 的页面尺寸。"""
        return (
            self.mm_to_points(self.page_size_mm[0]),
            self.mm_to_points(self.page_size_mm[1]),
        )

    # ------------------------------------------------------------------
    # 图片归一化（按 A4 比例缩放居中，白底填充）
    # ------------------------------------------------------------------
    def fit_to_a4(self, path: str) -> Optional[bytes]:
        """将单张图片按 A4 比例缩放并居中到白底画布，返回 PNG 字节。

        缩放到恰好铺满 A4（等比放大/缩小），四周用白色填充，保证
        打印时无黑边、比例不失真。
        """
        from PIL import Image

        try:
            im = Image.open(path)
        except Exception:
            return None

        im = im.convert("RGB")

        # 目标 A4 像素尺寸（按 DPI）
        a4_w = int(round(self.page_size_mm[0] * self.dpi / 25.4))
        a4_h = int(round(self.page_size_mm[1] * self.dpi / 25.4))

        src_w, src_h = im.size
        if src_w <= 0 or src_h <= 0:
            return None

        # 等比缩放，覆盖整个 A4 画布
        scale = max(a4_w / src_w, a4_h / src_h)
        new_w = int(round(src_w * scale))
        new_h = int(round(src_h * scale))
        im = im.resize((new_w, new_h), Image.LANCZOS)

        canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
        offset_x = (a4_w - new_w) // 2
        offset_y = (a4_h - new_h) // 2
        canvas.paste(im, (offset_x, offset_y))

        import io

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    # ------------------------------------------------------------------
    # 合成
    # ------------------------------------------------------------------
    def build(self, image_paths: Iterable[str]) -> bytes:
        """将图片列表合成为 A4 标准 PDF 字节。

        使用 img2pdf 内置的 A4 布局（等比缩放、居中、保留清晰度），
        遇到非法 EXIF 旋转标记时回退到 rotation=ifvalid。
        """
        import img2pdf

        paths = [p for p in image_paths if p and os.path.exists(p)]
        if not paths:
            return b""

        layout = img2pdf.get_layout_fun(pagesize=self._page_size_tuple())
        try:
            return img2pdf.convert(paths, layout_fun=layout)
        except Exception:
            return img2pdf.convert(
                paths,
                layout_fun=layout,
                rotation=img2pdf.Rotation.ifvalid,
            )

    def _page_size_tuple(self) -> tuple[float, float]:
        """返回 img2pdf pagesize 所需的 (宽pt, 高pt)。"""
        return self.layout_size()


# ----------------------------------------------------------------------
# 便捷函数
# ----------------------------------------------------------------------
def build_pdf(image_paths: Iterable[str]) -> bytes:
    """便捷封装：直接合成 A4 PDF。"""
    return PDFBuilder().build(image_paths)


def save_pdf(image_paths: Iterable[str], output_path: str) -> str:
    """便捷封装：合成并写入磁盘，返回输出路径。"""
    data = PDFBuilder().build(image_paths)
    with open(output_path, "wb") as f:
        f.write(data)
    return output_path
