"""core_engine.cleaner 单元测试。"""

import os

from core_engine.cleaner import CleanResult, ImageCleaner, ImageMeta, clean_exam_images


def test_measure_valid_image(tmp_path):
    from PIL import Image

    im = Image.new("RGB", (100, 200), (255, 255, 255))
    p = tmp_path / "a.jpg"
    im.save(str(p))

    meta = ImageCleaner().measure(str(p))
    assert meta.width == 100
    assert meta.height == 200
    assert abs(meta.ratio - 2.0) < 1e-6
    assert meta.brightness > 220  # 白底


def test_measure_missing_file(tmp_path):
    meta = ImageCleaner().measure(str(tmp_path / "nope.jpg"))
    assert meta.width == 0
    assert meta.ratio == 0.0


def test_dominant_size_empty():
    assert ImageCleaner.dominant_size([]) is None


def test_dominant_size_picks_most_common():
    metas = [
        ImageMeta("a", 100, 100),
        ImageMeta("b", 100, 100),
        ImageMeta("c", 50, 50),
    ]
    assert ImageCleaner.dominant_size(metas) == (100, 100)


def test_is_square_or_banner():
    c = ImageCleaner()
    assert c.is_square_or_banner(ImageMeta("x", 100, 100)) is True  # 正方形
    assert c.is_square_or_banner(ImageMeta("x", 100, 160)) is False  # 正常竖版


def test_is_colorful_or_dark():
    c = ImageCleaner()
    assert c.is_colorful_or_dark(ImageMeta("x", 100, 160, saturation=10.0, brightness=250))
    assert not c.is_colorful_or_dark(ImageMeta("x", 100, 160, saturation=0.5, brightness=250))


def test_clean_removes_square_ad(sample_image_dir):
    result = ImageCleaner().clean(sample_image_dir, remove_files=False)
    kept_basenames = [os.path.basename(p) for p in result.kept]
    removed_basenames = [os.path.basename(p) for p in result.removed]
    assert "ad.jpg" in removed_basenames
    assert "ad.jpg" not in kept_basenames
    assert len(result.kept) == 3


def test_clean_single_image_no_change(sample_image_dir):
    one = sample_image_dir[:1]
    result = ImageCleaner().clean(one)
    assert result.kept == one
    assert result.removed == []


def test_clean_fallback_when_all_removed(tmp_path):
    from PIL import Image

    # 全部是正方形,理论上全被剔除,应回退全部保留
    paths = []
    for i in range(3):
        im = Image.new("RGB", (300, 300), (200, 50, 50))
        p = tmp_path / f"sq_{i}.jpg"
        im.save(str(p))
        paths.append(str(p))
    result = ImageCleaner().clean(paths)
    assert result.kept == paths


def test_clean_exam_images_convenience(sample_image_dir):
    kept = clean_exam_images(list(sample_image_dir))
    assert len(kept) == 3
    assert all(os.path.basename(p) != "ad.jpg" for p in kept)
