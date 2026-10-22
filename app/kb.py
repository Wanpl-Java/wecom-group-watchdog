from __future__ import annotations

import logging
import re
import time
from typing import List, Tuple

import httpx

logger = logging.getLogger(__name__)

KB_BASE = "https://kb.fit2cloud.com"
KB_CATEGORY = f"{KB_BASE}/categories/jumpserver"
_JS_CATEGORY_NAME = "2"
_CACHE_TTL_SEC = 3600
_MIN_SCORE = 14
_MIN_STRONG_HITS = 1

# 过宽词：单独出现不得撑起「高度匹配」（典型：仅 Windows → 误中 AD 域文档）
_WEAK_TERMS = {
    "jumpserver",
    "jump",
    "server",
    "js",
    "v3",
    "v4",
    "远程应用",
    "remoteapp",
    "配置",
    "部署",
    "问题",
    "常见",
    "相关",
    "说明",
    "指南",
    "汇总",
    "使用",
    "连接",
    "资产",
    "用户",
    "登陆",
    "登录",
    "windows",
    "window",
    "win",
    "win10",
    "win11",
    "linux",
    "macos",
    "mac",
    "microsoft",
    "centos",
    "ubuntu",
    "debian",
    "redhat",
    "对接",
    "机器",
    "主机",
    "系统",
    "数据",
    "老师",
    "帮忙",
    "看下",
    "飞致云",
}

# 短缩写但语义强：可单独构成高度匹配（须同时过主题闸门）
_STRONG_ACRONYMS = {
    "ad",
    "mfa",
    "ssl",
    "sso",
    "cas",
    "otp",
    "rdp",
    "vnc",
    "ssh",
    "ldap",
    "oidc",
    "saml",
}


_cache_arts: List[Tuple[str, str]] = []
_cache_at: float = 0.0


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _is_strong_term(term: str) -> bool:
    nt = _normalize(term)
    if nt in _WEAK_TERMS:
        return False
    if nt in _STRONG_ACRONYMS:
        return True
    if len(nt) < 3:
        return False
    # 「远程应」「程应用」等弱词切窗不算强
    for w in _WEAK_TERMS:
        wn = _normalize(w)
        if len(wn) >= 3 and nt != wn and nt in wn:
            return False
    return True


def _extract_query_terms(text: str) -> List[str]:
    """领域短语 + 英文 + 短中文整块；不做碎窗切分。"""
    raw = (text or "").strip()
    if not raw:
        return []
    terms: List[str] = []
    seen = set()

    def add(t: str) -> None:
        t = t.strip()
        if len(t) < 2:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(t)

    domain = (
        "远程应用发布机",
        "应用发布机",
        "发布机",
        "远程应用",
        "调用账号",
        "账号逻辑",
        "选账号",
        "代填",
        "私有账号",
        "公共账号",
        "ConnectToken",
        "applet-option",
        "RemoteApp",
        "Tinker",
        "DOMAINS",
        "组件注册",
        "双因子",
        "改密",
        "升级",
        "回退",
        "SSL",
        "HTTPS",
        "Razor",
        "乱码",
        "LDAP",
        "MFA",
        "AD域",
        "Windows AD",
        "域控",
        "域账号",
        "域用户",
        "加入域",
        "PyCharm",
        "Oracle",
        "WinRM",
        "OpenSSH",
        "部署",
        "配置",
        "报错",
        "离线",
        "无法登录",
        "账号",
        "授权",
    )
    for d in domain:
        if d.lower() in raw.lower() or d in raw:
            add(d)

    for m in re.finditer(r"[A-Za-z][A-Za-z0-9._-]{1,}", raw):
        add(m.group(0))

    for part in re.split(r"[\s\[\]\(\)\"'：:，,。？?！!/\\、；;]+", raw):
        part = part.strip()
        if not part:
            continue
        for m in re.finditer(r"[\u4e00-\u9fff]{2,10}", part):
            add(m.group(0))
    return terms[:24]


def _score_title(title: str, terms: List[str]) -> Tuple[int, int, List[str]]:
    nt = _normalize(title)
    if not nt:
        return 0, 0, []
    score = 0
    strong_hits = 0
    matched_strong: List[str] = []
    weak_bonus = 0
    for term in terms:
        nt_term = _normalize(term)
        if len(nt_term) < 2 or nt_term not in nt:
            continue
        if _is_strong_term(term):
            if nt_term in _STRONG_ACRONYMS:
                score += 16
            elif re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,}", nt_term):
                score += 16 if len(nt_term) >= 4 else 8
            else:
                score += max(5, min(12, len(term) + 2))
            strong_hits += 1
            matched_strong.append(term)
        else:
            weak_bonus += 1
    score += min(2, weak_bonus)
    return score, strong_hits, matched_strong


def _title_has_ad_topic(title: str) -> bool:
    t = title or ""
    nt = _normalize(t)
    # 「免域」是 RemoteApp 无域场景，不算 AD/域对接主题
    if "免域" in t:
        return False
    if "ldap" in nt or "activedirectory" in nt:
        return True
    if re.search(r"(?i)\bAD\b", t) or "windowsad" in nt or "对接windowsad" in nt:
        return True
    if "域" in t and any(x in t for x in ("AD", "ad", "域控", "域认", "域账")):
        return True
    # 「…AD 域配置…」：有 AD + 域
    if "域" in t and ("ad" in nt):
        return True
    return False


def _query_has_ad_topic(raw_text: str = "") -> bool:
    raw = raw_text or ""
    q = _normalize(raw)
    if any(
        x in q
        for x in (
            "ldap",
            "activedirectory",
            "ad域",
            "域控",
            "域账号",
            "域用户",
            "域认证",
            "加入域",
            "windowsad",
        )
    ):
        return True
    if re.search(r"(?i)\bAD\b", raw):
        return True
    if "域" in raw and any(k in raw for k in ("域控", "域账", "域用", "加入域", "LDAP", "ldap")):
        return True
    return False


def _fetch_jumpserver_articles(timeout: float = 20.0) -> List[Tuple[str, str]]:
    arts: List[Tuple[str, str]] = []
    page = 1
    total = None
    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
        while page <= 30:
            resp = client.get(
                f"{KB_BASE}/apis/api.content.halo.run/v1alpha1/posts",
                params={
                    "page": page,
                    "size": 50,
                    "fieldSelector": f"spec.categories={_JS_CATEGORY_NAME}",
                },
            )
            resp.raise_for_status()
            data = resp.json() or {}
            items = data.get("items") or []
            if total is None:
                total = int(data.get("total") or 0)
            for it in items:
                if not isinstance(it, dict):
                    continue
                title = str((it.get("spec") or {}).get("title") or "").strip()
                permalink = str((it.get("status") or {}).get("permalink") or "").strip()
                if not title or not permalink:
                    continue
                if permalink.startswith("http"):
                    url = permalink
                else:
                    url = KB_BASE + (permalink if permalink.startswith("/") else "/" + permalink)
                arts.append((title, url))
            if not items:
                break
            if total is not None and page * 50 >= total:
                break
            page += 1
    return arts


def _get_cached_articles(force: bool = False) -> List[Tuple[str, str]]:
    global _cache_arts, _cache_at
    now = time.time()
    if not force and _cache_arts and (now - _cache_at) < _CACHE_TTL_SEC:
        return _cache_arts
    try:
        arts = _fetch_jumpserver_articles()
        if arts:
            _cache_arts = arts
            _cache_at = now
            logger.info("KB online index refreshed: %s JumpServer articles", len(arts))
        elif _cache_arts:
            logger.warning("KB fetch empty, keep stale cache size=%s", len(_cache_arts))
    except Exception:  # noqa: BLE001
        logger.exception("KB online fetch failed")
        if not _cache_arts:
            return []
    return _cache_arts


def search_kb_online(text: str, limit: int = 2) -> List[Tuple[str, str, int]]:
    terms = _extract_query_terms(text)
    if not terms:
        return []
    arts = _get_cached_articles()
    if not arts:
        return []
    ranked: List[Tuple[int, str, str]] = []
    query_ad = _query_has_ad_topic(text)
    for title, url in arts:
        sc, strong, _matched = _score_title(title, terms)
        title_ad = _title_has_ad_topic(title)
        # AD/域类标题：查询必须也有 AD/域/LDAP 语境，禁止仅 Windows 命中
        if title_ad and not query_ad:
            continue
        # 双方都是 AD/域语境时给主题加分（如「域控」对「AD 域配置」）
        if title_ad and query_ad:
            sc = max(sc + 10, _MIN_SCORE)
            strong = max(strong, 1)
        if sc < _MIN_SCORE or strong < _MIN_STRONG_HITS:
            continue
        ranked.append((sc, title, url))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    out: List[Tuple[str, str, int]] = []
    seen = set()
    for sc, title, url in ranked:
        if url in seen:
            continue
        seen.add(url)
        out.append((title, url, sc))
        if len(out) >= limit:
            break
    return out


def match_kb_articles(text: str, limit: int = 2) -> List[Tuple[str, str, str]]:
    hits = search_kb_online(text, limit=limit)
    return [(t, u, f"score={s}") for t, u, s in hits]


def format_kb_hint(text: str) -> str:
    hits = search_kb_online(text, limit=2)
    if not hits:
        return (
            "知识库在线检索：未达到「高度匹配」门槛，回复建议中不要贴知识库链接。"
            "请优先依据下方本地源码/文档摘录回答；摘录不足就写待人工确认，"
            "不要硬套发布机部署/代填配置教程。"
        )
    lines = [
        "知识库高度匹配（仅此情况下可引用直链；不要额外塞分类页或其他弱相关文）：",
    ]
    for i, (title, url, sc) in enumerate(hits, 1):
        lines.append(f"{i}. 《{title}》 {url} （相关分={sc}）")
    return "\n".join(lines)
