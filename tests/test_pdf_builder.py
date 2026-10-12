"""core_engine.pdf_builder 单元测试。"""

import os

from core_engine.pdf_builder import A4_DPI, A4_MM, PDFBuilder, build_pdf, save_pdf


def test_layout_size_is_a4_points():
    w, h = PDFBuilder().layout_size()
    assert abs(w - 595.2755905511812) < 0.5
    assert abs(h - 841.8897637795276) < 0.5


def test_mm_to_points():
    assert abs(PDFBuilder.mm_to_points(25.4) - 72.0) < 1e-6


def test_build_empty_returns_empty():
    assert PDFBuilder().build([]) == b""


def test_build_valid_images(sample_image_dir):
    kept = [p for p in sample_image_dir if "ad" not in p]
    data = PDFBuilder().build(kept)
    assert data[:5] == b"%PDF-"
    assert len(data) > 100


def test_build_ignores_missing_files(sample_image_dir):
    kept = [p for p in sample_image_dir if "ad" not in p] + ["/nonexistent/x.jpg"]
    data = PDFBuilder().build(kept)
    assert data[:5] == b"%PDF-"


def test_build_pdf_convenience(sample_image_dir):
    kept = [p for p in sample_image_dir if "ad" not in p]
    assert build_pdf(kept)[:5] == b"%PDF-"


def test_save_pdf_writes_file(sample_image_dir, tmp_path):
    kept = [p for p in sample_image_dir if "ad" not in p]
    out = tmp_path / "out.pdf"
    save_pdf(kept, str(out))
    assert out.exists()
    assert os.path.getsize(out) > 100


def test_fit_to_a4(sample_image_dir):
    builder = PDFBuilder()
    png = builder.fit_to_a4(sample_image_dir[0])
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_defaults():
    assert A4_MM == (210.0, 297.0)
    assert A4_DPI == 300.0
