"""pytest 公共配置:注入项目根目录到 sys.path,统一可用依赖。"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def sample_image_dir(tmp_path):
    """构造一组用于去杂测试的图片:3 张白色 A4 图 + 1 张彩色正方形广告图。"""
    from PIL import Image

    paths = []
    for i in range(3):
        im = Image.new("RGB", (1200, 1600), (255, 255, 255))
        p = tmp_path / f"exam_{i}.jpg"
        im.save(str(p))
        paths.append(str(p))

    # 彩色正方形广告图
    im = Image.new("RGB", (800, 800), (200, 50, 50))
    ad = tmp_path / "ad.jpg"
    im.save(str(ad))
    paths.append(str(ad))

    return paths
