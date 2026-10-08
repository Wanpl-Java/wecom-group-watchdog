"""内部 Confluence Wiki（wiki.fit2cloud.cn）检索，作为回复建议参考知识源。"""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

_MIN_TERM_LEN = 2
_WEAK = {
    "jumpserver",
    "jump",
    "server",
    "js",
    "老师",
    "你好",
    "问题",
    "帮忙",
    "请问",
    "怎么",
    "如何",
    "什么",
    "一下",
    "这个",
    "那个",
    "我们",
    "你们",
}


def _strip_html(html: str, limit: int = 220) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", html or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _extract_terms(text: str, limit: int = 4) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    strong: List[str] = []
    weak_fallback: List[str] = []
    seen = set()

    def add(t: str, *, allow_weak: bool = False) -> None:
        t = t.strip().strip("，。；：、！？,.!?;:\"'()[]{}")
        if len(t) < _MIN_TERM_LEN:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        if key in _WEAK:
            if allow_weak:
                weak_fallback.append(t)
            return
        strong.append(t)

    for m in re.finditer(r"[\u4e00-\u9fff]{2,12}", raw):
        add(m.group(0))
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_\-./]{2,}", raw):
        add(m.group(0), allow_weak=True)
    strong.sort(key=lambda x: (-len(x), x))
    if strong:
        return strong[:limit]
    # 仅剩产品名等弱词时也要能搜（如 JumpServer）
    return weak_fallback[:limit]


def _clean_query_phrase(text: str, limit: int = 40) -> str:
    """去掉角色前缀后取短句，供 siteSearch / text 检索。"""
    raw = (text or "").strip()
    raw = re.sub(r"\[(customer|staff)\]\s*", "", raw, flags=re.I)
    raw = re.sub(r"[\w\-]+?:\s*", "", raw)  # userid:
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:limit]


def _enabled(settings: Settings) -> bool:
    return bool((settings.confluence_token or "").strip()) and bool(
        (settings.confluence_base or "").strip()
    )


def _headers(settings: Settings) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.confluence_token.strip()}",
        "Accept": "application/json",
    }


def _space_clause(settings: Settings) -> str:
    keys = [
        k.strip()
        for k in (settings.confluence_space_keys or "").split(",")
        if k.strip()
    ]
    if not keys:
        return ""
    if len(keys) == 1:
        return f'space = "{keys[0]}" AND '
    joined = ", ".join(f'"{k}"' for k in keys)
    return f"space in ({joined}) AND "


def _page_url(settings: Settings, result: Dict[str, Any]) -> str:
    base = settings.confluence_base.rstrip("/")
    links = result.get("_links") or {}
    webui = str(links.get("webui") or "").strip()
    if webui:
        if webui.startswith("http"):
            return webui
        return base + (webui if webui.startswith("/") else "/" + webui)
    cid = result.get("id")
    return f"{base}/pages/viewpage.action?pageId={cid}" if cid else base


def _cql_candidates(text: str, settings: Settings) -> List[str]:
    space = _space_clause(settings)
    terms = _extract_terms(text)
    phrase = _clean_query_phrase(text)
    cqls: List[str] = []
    if terms:
        q_terms = terms[:2]
        or_parts = []
        for t in q_terms:
            safe = t.replace('"', " ").replace("\\", " ")
            or_parts.append(f'title ~ "{safe}"')
            or_parts.append(f'text ~ "{safe}"')
        cqls.append(space + "type = page AND (" + " OR ".join(or_parts) + ")")
        # 单强词 title 优先，噪音更少
        safe0 = q_terms[0].replace('"', " ").replace("\\", " ")
        cqls.append(space + f'type = page AND title ~ "{safe0}"')
    if phrase and len(phrase) >= 2:
        safe_p = phrase.replace('"', " ").replace("\\", " ")
        cqls.append(space + f'type = page AND text ~ "{safe_p}"')
        cqls.append(space + f'siteSearch ~ "{safe_p}" AND type = page')
    # 去重保序
    seen = set()
    out: List[str] = []
    for c in cqls:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _parse_hits(
    data: Dict[str, Any],
    settings: Settings,
    *,
    limit: int,
) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    for r in data.get("results") or []:
        title = str(r.get("title") or "").strip()
        if not title:
            continue
        body = ((r.get("body") or {}).get("view") or {}).get("value") or ""
        snippet = _strip_html(body)
        out.append((title, _page_url(settings, r), snippet))
        if len(out) >= limit:
            break
    return out


def search_confluence(
    text: str,
    settings: Settings,
    *,
    limit: int = 2,
) -> List[Tuple[str, str, str]]:
    """返回 (title, url, snippet)。未配置 Token 时返回空。"""
    if not _enabled(settings):
        return []
    cqls = _cql_candidates(text, settings)
    if not cqls:
        return []
    base = settings.confluence_base.rstrip("/")
    endpoint = f"{base}/rest/api/content/search"
    lim = max(1, min(limit, 5))
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            for cql in cqls:
                resp = client.get(
                    endpoint,
                    params={
                        "cql": cql,
                        "limit": lim,
                        "expand": "space,body.view",
                    },
                    headers=_headers(settings),
                )
                if resp.status_code == 401:
                    logger.warning("Confluence auth failed (401); check CONFLUENCE_TOKEN")
                    return []
                if resp.status_code >= 400:
                    logger.info("Confluence cql rejected status=%s cql=%s", resp.status_code, cql[:160])
                    continue
                hits = _parse_hits(resp.json(), settings, limit=limit)
                if hits:
                    logger.info("Confluence hits=%s cql=%s", len(hits), cql[:160])
                    return hits
    except Exception:  # noqa: BLE001
        logger.exception("Confluence search failed")
        return []
    return []


def format_confluence_hint(text: str, settings: Optional[Settings]) -> str:
    if settings is None or not _enabled(settings):
        return ""
    hits = search_confluence(text, settings, limit=2)
    if not hits:
        return (
            "内部 Wiki（Confluence）检索：未命中高相关页；"
            "回复建议中不要编造 wiki 链接。"
        )
    lines = [
        "内部 Wiki（Confluence）相关页（可作内部分析参考；"
        "对外回复仅在高度相关时嵌一条可访问说明，勿堆链接）：",
    ]
    for i, (title, url, snippet) in enumerate(hits, 1):
        lines.append(f"{i}. 《{title}》 {url}")
        if snippet:
            lines.append(f"   摘录: {snippet}")
    return "\n".join(lines)
