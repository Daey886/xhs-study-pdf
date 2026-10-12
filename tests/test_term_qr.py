"""home_edition.auth.term_qr 单元测试。"""

from home_edition.auth.term_qr import TerminalQR, print_qr_ansi, qr_to_ansi


def test_render_returns_multiline():
    text = TerminalQR().render("https://192.168.1.5:5088")
    lines = text.splitlines()
    assert len(lines) > 5
    assert all(len(line) == len(lines[0]) for line in lines)


def test_render_content_non_empty():
    text = qr_to_ansi("hello")
    assert text.strip() != ""


def test_fallback_matrix():
    matrix = TerminalQR._get_matrix_fallback("data")
    assert len(matrix) > 0
    assert all(len(row) == len(matrix[0]) for row in matrix)
    assert all(isinstance(cell, bool) for row in matrix for cell in row)


def test_invert_changes_render():
    normal = TerminalQR(invert=False).render("same-data")
    inverted = TerminalQR(invert=True).render("same-data")
    assert normal != inverted


def test_print_qr_ansi_runs(capsys):
    print_qr_ansi("https://example.com")
    out = capsys.readouterr().out
    assert out.strip() != ""
