from __future__ import annotations

import re

import httpx

_MAX_CONTENT_CHARS = 4000
_REQUEST_TIMEOUT = 15.0


def fetch_url(url: str) -> str:
    """Fetch a web page and return a compact plain-text version."""
    if not url.startswith(("http://", "https://")):
        return f"無效的 URL: {url}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }

    try:
        with httpx.Client(
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException:
        return f"讀取逾時：{url}"
    except httpx.HTTPError as exc:
        return f"HTTP 錯誤：{exc}"

    if response.status_code >= 400:
        return f"HTTP {response.status_code}: {url}"

    content_type = response.headers.get("content-type", "")
    if "text" not in content_type and "html" not in content_type:
        return f"不支援的 Content-Type: {content_type}"

    return _extract_text(response.text)


def _extract_text(html: str) -> str:
    """Extract readable text from HTML."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]

        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(
            [
                "script",
                "style",
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "noscript",
                "iframe",
                "img",
                "svg",
                "button",
                "input",
            ]
        ):
            tag.decompose()

        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id=re.compile(r"content|main|article", re.I))
            or soup.find(class_=re.compile(r"content|main|article|post", re.I))
            or soup.body
        )
        text = (main or soup).get_text(separator="\n", strip=True)
    except ModuleNotFoundError:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    full_text = "\n".join(lines)

    if len(full_text) > _MAX_CONTENT_CHARS:
        full_text = full_text[:_MAX_CONTENT_CHARS] + "\n...[內容已截斷]"

    return full_text or "(頁面沒有可擷取的文字內容)"
