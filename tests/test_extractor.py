"""core_engine.extractor 单元测试。"""

from core_engine.extractor import (
    ALLOWED_SHARE_HOSTS,
    NOTE_ID_RE,
    Extractor,
    NoteInfo,
    extract_note_id,
)


def test_extract_note_id():
    url = "https://www.xiaohongshu.com/explore/6a678b1b0000000011007eee?xsec_token=abc"
    assert extract_note_id(url) == "6a678b1b0000000011007eee"


def test_extract_note_id_short_link_none():
    assert extract_note_id("https://xhslink.com/a/abc123") is None


def test_extract_note_id_discovery():
    url = "https://www.xiaohongshu.com/discovery/item/ffffffff0000000000000001"
    assert extract_note_id(url) == "ffffffff0000000000000001"


def test_note_id_regex_pattern():
    m = NOTE_ID_RE.search("https://x.com/explore/123456789012345678901234")
    assert m is not None
    assert m.group(1) == "123456789012345678901234"


def test_is_allowed_url():
    e = Extractor()
    assert e.is_allowed_url("https://www.xiaohongshu.com/x/1")
    assert e.is_allowed_url("https://xhslink.com/a/1")
    assert e.is_allowed_url("https://sub.xiaohongshu.com/x")
    assert not e.is_allowed_url("https://evil.com/x")
    assert not e.is_allowed_url("https://xiaohongshu.com.evil.com/x")


def test_allowed_hosts_constant():
    assert "xiaohongshu.com" in ALLOWED_SHARE_HOSTS
    assert "xhslink.com" in ALLOWED_SHARE_HOSTS
    assert "xhslink.cn" in ALLOWED_SHARE_HOSTS


def test_extract_image_token():
    assert Extractor._extract_image_token(None) is None
    url = "https://sns-img-bd.xhscdn.com/a/b/c/d/e/token123!nd_dft"
    assert Extractor._extract_image_token(url) == "c/d/e/token123"


def test_normalize_img_url():
    assert Extractor._normalize_img_url("//cdn.x.com/a.jpg") == "https://cdn.x.com/a.jpg"
    assert Extractor._normalize_img_url("https://a/b.jpg!suffix", keep_suffix=True) == "https://a/b.jpg!suffix"
    assert Extractor._normalize_img_url("https://a/b.jpg!suffix") == "https://a/b.jpg"


def test_sanitize_title():
    assert Extractor._sanitize_title('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i"


def test_note_missing_in_html():
    assert Extractor.note_missing_in_html("当前笔记暂时无法浏览")
    assert not Extractor.note_missing_in_html("正常内容")


def test_images_from_state():
    state = {
        "note": {
            "noteDetailMap": {
                "k": {
                    "note": {
                        "title": "测试卷",
                        "imageList": [
                            {"urlDefault": "https://sns-img-bd.xhscdn.com/a/b/c/d/e/tok1!x"}
                        ],
                    }
                }
            }
        }
    }
    imgs = Extractor()._images_from_state(state)
    assert imgs == ["https://sns-img-bd.xhscdn.com/c/d/e/tok1"]


def test_note_kind_from_state():
    e = Extractor()
    video_state = {"note": {"noteDetailMap": {"k": {"note": {"type": "video"}}}}}
    assert e.note_kind_from_state(video_state) == "video"
    img_state = {"note": {"noteDetailMap": {"k": {"note": {"imageList": [{}]}}}}}
    assert e.note_kind_from_state(img_state) == "image"
    assert e.note_kind_from_state({}) is None


def test_note_info_defaults():
    ni = NoteInfo()
    assert ni.title == "未命名笔记"
    assert ni.images == []
    assert ni.kind is None
    assert ni.missing is False
