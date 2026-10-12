"""启发式去杂引擎 (Heuristic Image Cleaner)。

独立于 Web/桌面/CLI 的纯算法模块，负责在小红书图文笔记的一批图片中，
剔除与「试卷」无关的杂质图：

1. 尺寸比例聚类过滤 —— 统计整组图片的主导分辨率，剔除孤立尺寸；
2. 剔除 1:1 正方形引流广告图与横版超长 Banner；
3. HSV 白底识别 —— 试卷通常为高亮度、低饱和度的白底，据此剔除彩色
   插画 / 灰色褶皱纸纹质感的博主广告卡片。

零外部模型依赖、零网络请求，仅依赖 Pillow。
"""

from __future__ import annotations

from typing import Iterable, Optional

__all__ = [
    "ImageMeta",
    "CleanResult",
    "ImageCleaner",
    "clean_exam_images",
    "clean_exam_images_to_pdf",
]


class ImageMeta:
    """单张图片的度量信息（供聚类与白底判定使用）。"""

    __slots__ = ("path", "width", "height", "ratio", "saturation", "brightness")

    def __init__(
        self,
        path: str,
        width: int = 0,
        height: int = 0,
        saturation: float = 0.0,
        brightness: float = 0.0,
    ) -> None:
        self.path = path
        self.width = width
        self.height = height
        # 高/宽比例；宽为 0 时记为 0
        self.ratio = (height / float(width)) if width > 0 else 0.0
        self.saturation = saturation
        self.brightness = brightness

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)


class CleanResult:
    """一次去杂的完整结果。"""

    __slots__ = ("kept", "removed", "dominant_size")

    def __init__(
        self,
        kept: list[str],
        removed: list[str],
        dominant_size: Optional[tuple[int, int]],
    ) -> None:
        self.kept = kept
        self.removed = removed
        self.dominant_size = dominant_size


class ImageCleaner:
    """无模型启发式试卷去杂器。

    阈值均可通过构造参数覆盖，便于单元测试与调参。
    """

    # 长宽比（高/宽）阈值
    MIN_PORTRAIT_RATIO = 1.15   # 低于此值视为正方形/接近正方形卡片
    MAX_PORTRAIT_RATIO = 2.2    # 高于此值视为横版超长 Banner
    # HSV 色彩/亮度阈值
    SATURATION_THRESHOLD = 3.0   # 饱和度均值高于此值视为彩色
    BRIGHTNESS_THRESHOLD = 220.0  # 亮度均值低于此值视为非白底

    def __init__(
        self,
        min_portrait_ratio: float = MIN_PORTRAIT_RATIO,
        max_portrait_ratio: float = MAX_PORTRAIT_RATIO,
        saturation_threshold: float = SATURATION_THRESHOLD,
        brightness_threshold: float = BRIGHTNESS_THRESHOLD,
    ) -> None:
        self.min_portrait_ratio = min_portrait_ratio
        self.max_portrait_ratio = max_portrait_ratio
        self.saturation_threshold = saturation_threshold
        self.brightness_threshold = brightness_threshold

    # ------------------------------------------------------------------
    # 度量采集
    # ------------------------------------------------------------------
    def measure(self, path: str) -> ImageMeta:
        """读取单张图片并计算 HSV 度量；读取失败时返回全 0 元数据。"""
        from PIL import Image, ImageStat

        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                width, height = im.size
                hsv = im.convert("HSV")
                stat = ImageStat.Stat(hsv)
                saturation = stat.mean[1]
                brightness = stat.mean[2]
                return ImageMeta(path, width, height, saturation, brightness)
        except Exception:
            return ImageMeta(path)

    def measure_all(self, paths: Iterable[str]) -> list[ImageMeta]:
        return [self.measure(p) for p in paths]

    # ------------------------------------------------------------------
    # 尺寸聚类
    # ------------------------------------------------------------------
    @staticmethod
    def dominant_size(metas: Iterable[ImageMeta]) -> Optional[tuple[int, int]]:
        """返回出现次数最多的 (width, height)，无有效尺寸时返回 None。"""
        counter: dict[tuple[int, int], int] = {}
        for m in metas:
            if m.width > 0 and m.height > 0:
                counter[m.size] = counter.get(m.size, 0) + 1
        if not counter:
            return None
        return max(counter.items(), key=lambda kv: kv[1])[0]

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def is_square_or_banner(self, meta: ImageMeta) -> bool:
        return (
            meta.ratio < self.min_portrait_ratio
            or meta.ratio > self.max_portrait_ratio
        )

    def is_colorful_or_dark(self, meta: ImageMeta) -> bool:
        return (
            meta.saturation > self.saturation_threshold
            or meta.brightness < self.brightness_threshold
        )

    def should_drop(
        self,
        meta: ImageMeta,
        dominant: Optional[tuple[int, int]],
        dominant_count: int,
    ) -> bool:
        """综合判定一张图是否为杂质广告图。"""
        if self.is_square_or_banner(meta):
            return True
        is_isolated = (
            dominant_count >= 2 and meta.size != dominant and dominant is not None
        )
        if is_isolated and self.is_colorful_or_dark(meta):
            return True
        return False

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def clean(self, paths: list[str], remove_files: bool = False) -> CleanResult:
        """对一组图片路径执行去杂，返回保留与剔除列表。

        :param paths: 图片文件路径列表
        :param remove_files: 为 True 时直接从磁盘删除被剔除的图片
        """
        if len(paths) <= 1:
            return CleanResult(list(paths), [], self.dominant_size([]))

        metas = self.measure_all(paths)
        dominant = self.dominant_size(metas)
        dom_count = sum(
            1 for m in metas if m.width > 0 and dominant is not None and m.size == dominant
        )

        kept: list[str] = []
        removed: list[str] = []
        for meta in metas:
            if self.should_drop(meta, dominant, dom_count):
                removed.append(meta.path)
                if remove_files:
                    try:
                        import os

                        os.remove(meta.path)
                    except Exception:
                        pass
            else:
                kept.append(meta.path)

        # 兜底：全部被剔除时回退全部保留，防止生成空 PDF
        if not kept:
            return CleanResult(list(paths), [], dominant)
        return CleanResult(kept, removed, dominant)


# ----------------------------------------------------------------------
# 便捷函数（保持与原 server.py 内联实现一致的语义）
# ----------------------------------------------------------------------
def clean_exam_images(temp_imgs: list[str]) -> list[str]:
    """便捷封装：返回保留图片路径列表（并删除被剔除的临时文件）。"""
    return ImageCleaner().clean(temp_imgs, remove_files=True).kept


def clean_exam_images_to_pdf(temp_imgs: list[str]) -> bytes:
    """去杂并合成标准 A4 PDF（依赖 pdf_builder）。"""
    try:
        from core_engine.pdf_builder import PDFBuilder
    except ImportError:  # 直接以脚本运行时回退到同级模块
        from pdf_builder import PDFBuilder

    kept = ImageCleaner().clean(temp_imgs, remove_files=True).kept
    return PDFBuilder().build(kept)
