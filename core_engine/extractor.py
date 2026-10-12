"""小红书笔记解析与图片并发下载模块。

负责：
1. 从分享链接提取 24 位笔记 ID；
2. 短链 (xhslink.com) 重定向追踪；
3. 请求笔记页面并容错解析 window.__INITIAL_STATE__；
4. 提取无水印原图链接（token 重组）；
5. 并发下载图片到本地。

与 cleaner / pdf_builder 解耦，仅依赖 requests 与 pyyaml。
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import uuid
from typing import Optional
from urllib.parse import urlparse

import requests

__all__ = [
    "NOTE_ID_RE",
    "Extractor",
    "NoteInfo",
    "extract_note_id",
    "download_images",
]

NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item)/([0-9a-fA-F]{24})")

ALLOWED_SHARE_HOSTS = ("xiaohongshu.com", "xhslink.com")

PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

IMG_HEADERS = {
    "User-Agent": PC_UA,
    "Referer": "https://www.xiaohongshu.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

# 页面命中这些文案即判定笔记不可访问（删除/私密/失效）
NOTE_MISSING_MARKERS = (
    "当前笔记暂时无法浏览",
    "你访问的页面不见了",
    "笔记不存在",
    "当前内容无法查看",
    "该内容已删除",
)

YAML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 现役图片 CDN 域名（sns-img-* 已废弃，ci.xiaohongshu.com 兜底）
IMG_HOST_RE = re.compile(
    r"https?://(?:sns-webpic|sns-img|sns-webpic-hd|ci)[a-zA-Z0-9\-]*\."
    r"(?:xhscdn\.com|xiaohongshu\.com)/[a-zA-Z0-9/_\-\.!]+"
)


class NoteInfo:
    """一篇笔记解析后的结构化结果。"""

    __slots__ = ("title", "images", "kind", "missing")

    def __init__(
        self,
        title: str = "未命名笔记",
        images: Optional[list[str]] = None,
        kind: Optional[str] = None,
        missing: bool = False,
    ) -> None:
        self.title = title
        self.images = images or []
        self.kind = kind  # 'image' / 'video' / None
        self.missing = missing


class Extractor:
    """小红书笔记解析器。"""

    def __init__(
        self,
        user_agent: str = PC_UA,
        allowed_hosts: tuple[str, ...] = ALLOWED_SHARE_HOSTS,
        timeout: float = 15.0,
        max_workers: int = 6,
    ) -> None:
        self.user_agent = user_agent
        self.allowed_hosts = allowed_hosts
        self.timeout = timeout
        self.max_workers = max_workers

    # ------------------------------------------------------------------
    # 链接识别
    # ------------------------------------------------------------------
    @staticmethod
    def extract_note_id(url: str) -> Optional[str]:
        m = NOTE_ID_RE.search(url or "")
        return m.group(1) if m else None

    def is_allowed_url(self, url: str) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            return False
        if not host:
            return False
        return any(
            host == allowed or host.endswith("." + allowed)
            for allowed in self.allowed_hosts
        )

    # ------------------------------------------------------------------
    # 页面解析
    # ------------------------------------------------------------------
    def _headers(self, cookie: Optional[str] = None) -> dict:
        h = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if cookie:
            h["Cookie"] = cookie
        return h

    def _extract_initial_state(self, html: str) -> Optional[dict]:
        m = re.search(
            r"<script>window\.__INITIAL_STATE__\s*=\s*(.*?)</script>", html, re.S
        )
        if not m:
            return None
        raw = m.group(1).strip().rstrip(";")
        cleaned = YAML_ILLEGAL_RE.sub("", raw)
        cleaned = cleaned.replace("new Map([])", "[]").replace("undefined", "null")
        try:
            import yaml

            return yaml.safe_load(cleaned)
        except Exception:
            return None

    @staticmethod
    def _extract_image_token(url: str) -> Optional[str]:
        if not url or not isinstance(url, str) or "://" not in url:
            return None
        return "/".join(url.split("/")[5:]).split("!")[0]

    def _images_from_state(self, state) -> list[str]:
        imgs: list[str] = []
        if not isinstance(state, dict):
            return imgs
        nd = (state.get("note") or {}).get("noteDetailMap") or {}
        if not isinstance(nd, dict):
            return imgs
        for _k, v in nd.items():
            note = (v or {}).get("note") or {}
            for im in note.get("imageList") or []:
                token = None
                for ukey in ("urlDefault", "urlPre", "url"):
                    token = self._extract_image_token(im.get(ukey))
                    if token:
                        break
                if not token:
                    for info in im.get("infoList") or []:
                        token = self._extract_image_token(info.get("url"))
                        if token:
                            break
                if token:
                    imgs.append("https://sns-img-bd.xhscdn.com/" + token)
        return imgs

    def _title_from_state(self, state) -> Optional[str]:
        if not isinstance(state, dict):
            return None
        nd = (state.get("note") or {}).get("noteDetailMap") or {}
        if not isinstance(nd, dict):
            return None
        for _k, v in nd.items():
            t = ((v or {}).get("note") or {}).get("title")
            if t:
                return t
        return None

    def note_kind_from_state(self, state) -> Optional[str]:
        if not isinstance(state, dict):
            return None
        nd = (state.get("note") or {}).get("noteDetailMap") or {}
        if not isinstance(nd, dict):
            return None
        for _k, v in nd.items():
            note = (v or {}).get("note") or {}
            if not isinstance(note, dict):
                continue
            imgs = note.get("imageList") or []
            note_type = (note.get("type") or "").lower()
            if note_type == "video" or note.get("video"):
                return "video"
            if imgs:
                return "image"
        return None

    @staticmethod
    def note_missing_in_html(html: str) -> bool:
        if not html:
            return False
        return any(marker in html for marker in NOTE_MISSING_MARKERS)

    @staticmethod
    def _normalize_img_url(u: str, keep_suffix: bool = False) -> Optional[str]:
        if not u:
            return None
        cu = u if keep_suffix else u.split("!")[0]
        if cu.startswith("//"):
            return "https:" + cu
        return cu

    @staticmethod
    def _sanitize_title(title: str) -> str:
        clean = re.sub(r'[\\/\\:*?"<>|]', "_", title).strip()
        return (clean or "未命名笔记")[:30]

    def parse_note(self, url: str, cookie: Optional[str] = None) -> NoteInfo:
        """解析单篇笔记，返回 NoteInfo。"""
        resp = requests.get(
            url,
            headers=self._headers(cookie),
            allow_redirects=True,
            timeout=self.timeout,
        )
        html = resp.text

        state = self._extract_initial_state(html)
        kind = self.note_kind_from_state(state)
        title = self._title_from_state(state)
        if not title:
            t_match = re.search(r"<title>([^<]+)</title>", html)
            if t_match:
                title = t_match.group(1).split("-")[0].strip()
        title = self._sanitize_title(title or "未命名笔记")

        clean_imgs: list[str] = []
        for u in self._images_from_state(state):
            cu = self._normalize_img_url(u, keep_suffix=True)
            if cu and cu not in clean_imgs:
                clean_imgs.append(cu)

        if kind != "video" and not clean_imgs:
            for u in IMG_HOST_RE.findall(html):
                cu = self._normalize_img_url(u, keep_suffix=True)
                if not cu:
                    continue
                if any(s in cu for s in ("formula-static", "/fe-platform/", ".js", ".ico", ".png")):
                    continue
                if cu not in clean_imgs:
                    clean_imgs.append(cu)

        if kind is None and not clean_imgs:
            if re.search(r'"type"\s*:\s*"video"', html) or (
                '"video"' in html and "videoUrl" in html
            ):
                kind = "video"
        elif kind is None and clean_imgs:
            kind = "image"

        missing = False
        if not clean_imgs and kind is None:
            missing = self.note_missing_in_html(html)

        return NoteInfo(title=title, images=clean_imgs, kind=kind, missing=missing)

    # ------------------------------------------------------------------
    # 图片并发下载
    # ------------------------------------------------------------------
    def _download_one(self, img_url: str, out_dir: str, idx: int) -> Optional[str]:
        try:
            r = requests.get(img_url, headers=IMG_HEADERS, timeout=self.timeout)
            if r.status_code == 200:
                tp = os.path.join(out_dir, f"temp_{uuid.uuid4().hex[:6]}_{idx}.jpg")
                with open(tp, "wb") as f:
                    f.write(r.content)
                return tp
        except Exception:
            pass
        return None

    def download_images(self, image_urls: list[str], out_dir: str) -> list[str]:
        """并发下载图片到 out_dir，返回成功写入的本地路径列表。"""
        os.makedirs(out_dir, exist_ok=True)
        results: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(image_urls) or 1)
        ) as pool:
            futures = {
                pool.submit(self._download_one, u, out_dir, i): i
                for i, u in enumerate(image_urls)
            }
            for fut in concurrent.futures.as_completed(futures):
                path = fut.result()
                if path:
                    results.append(path)
        return results


# ----------------------------------------------------------------------
# 便捷函数
# ----------------------------------------------------------------------
def extract_note_id(url: str) -> Optional[str]:
    return Extractor().extract_note_id(url)


def download_images(image_urls: list[str], out_dir: str) -> list[str]:
    return Extractor().download_images(image_urls, out_dir)
