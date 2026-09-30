from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1] / "workbuddy"
_VENDOR = _ROOT / "vendor" / "anti-aistyle-zh"
_SUPPORT = _ROOT / "anti-aistyle-support" / "SKILL.md"
_HOME_SUPPORT = Path.home() / ".workbuddy" / "skills" / "anti-aistyle-zh"


def _read_text(path: Path, max_chars: int = 0) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.exception("read skill file failed: %s", path)
        return ""
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n…(截断)"
    return text


def load_anti_aistyle_block(max_total_chars: int = 6000) -> str:
    """
    组装 anti-aistyle-zh（客服平衡强度）注入块。
    优先本地整合层 + 上游标记摘要，控制 token。
    """
    parts: List[str] = []
    support = _read_text(_SUPPORT, max_chars=3500)
    if not support:
        # 回退到用户目录完整 skill（可能较长）
        support = _read_text(_HOME_SUPPORT / "SKILL.md", max_chars=2500)
    if support:
        parts.append("===== anti-aistyle-zh × 售后整合（强度=平衡/客服口径）=====\n" + support)

    markers = _read_text(_VENDOR / "references" / "chinese_ai_markers.md", max_chars=2200)
    if markers:
        parts.append("===== anti-aistyle-zh chinese_ai_markers（摘要）=====\n" + markers)

    hotlist = _read_text(_VENDOR / "references" / "residual_hotlist.md", max_chars=1800)
    if hotlist:
        parts.append("===== anti-aistyle-zh residual_hotlist（摘要）=====\n" + hotlist)

    if not parts:
        logger.warning("anti-aistyle-zh materials missing under %s", _ROOT)
        return ""

    blob = "\n\n".join(parts)
    if len(blob) > max_total_chars:
        blob = blob[:max_total_chars].rstrip() + "\n…(anti-aistyle 注入截断)"
    return blob


_CUSTOMER_FLUFF = (
    "希望以上信息对您有帮助",
    "希望以上对您有帮助",
    "如有其他问题随时联系",
    "如有疑问请随时联系",
    "感谢您的理解与支持",
    "我将竭诚为您服务",
    "祝您使用愉快",
)


_AI_MARKUP_RE = re.compile(r"[*_`~#>]{1,}|·{1,}")
_FANCY_QUOTES = str.maketrans(
    {
        "\u201c": "",  # “
        "\u201d": "",  # ”
        "\u2018": "",  # ‘
        "\u2019": "",  # ’
        "\u300c": "",  # 「
        "\u300d": "",  # 」
        "\u300e": "",  # 『
        "\u300f": "",  # 』
        '"': "",
        "'": "",
    }
)


def _strip_ai_markup(text: str) -> str:
    """去掉易显 AI 味的引号/加粗/反引号/间隔号等，不伤 URL。"""
    if not text:
        return text
    # 先保护 http(s) URL，避免误伤
    urls: List[str] = []

    def _park(m: re.Match[str]) -> str:
        urls.append(m.group(0))
        return f"@@URL{len(urls) - 1}@@"

    parked = re.sub(r"https?://\S+", _park, text)
    parked = parked.translate(_FANCY_QUOTES)
    parked = _AI_MARKUP_RE.sub("", parked)
    # 去掉残留成对 **xx** 拆开后的空格堆积
    parked = re.sub(r"[ \t]{2,}", " ", parked)
    for i, u in enumerate(urls):
        parked = parked.replace(f"@@URL{i}@@", u)
    return parked


def _split_section4(text: str) -> tuple[str, str]:
    """返回 (前缀含1-3段, 第4段正文)。找不到第4段时正文为空。"""
    m = re.search(r"(?m)^(4\)\s*回复建议)\s*$", text)
    if not m:
        # 兼容无换行标题
        m = re.search(r"(?m)^(4\)\s*回复建议)\s*", text)
    if not m:
        return text, ""
    head = text[: m.end()]
    body = text[m.end() :].lstrip("\n")
    return head, body


_FAULT_HINTS = (
    "报错",
    "失败",
    "连不上",
    "连不了",
    "无法",
    "超时",
    "异常",
    "卡住",
    "错误",
    "不行",
    "登不上",
    "白屏",
    "打不开",
    "不生效",
    "怎么排查",
    "怎么办",
)

# 纯咨询时要砍掉的「追问尾巴」
_SOLICIT_HINT = re.compile(
    r"(版本号|截图|复现|方便发|请补充|发下具体|没选对还是报错|帮您看下|继续协助|我们帮您看)"
)


def _customer_looks_like_fault(customer_excerpt: str) -> bool:
    t = customer_excerpt or ""
    return any(k in t for k in _FAULT_HINTS)


def _trim_solicitation_tail(sec4: str, customer_excerpt: str) -> str:
    """客户未报障时，去掉回复建议里追问版本/复现/是否报错的尾巴。"""
    if not sec4 or _customer_looks_like_fault(customer_excerpt):
        return sec4
    parts = re.split(r"(?<=[。！？])", sec4)
    kept: List[str] = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        if _SOLICIT_HINT.search(s) and re.search(r"(发|补充|方便|吗|呢|是否|还是)", s):
            continue
        kept.append(p if p.endswith(("。", "！", "？")) or not s else p)
    text = "".join(kept).strip()
    return text or sec4.strip()


def light_deai_customer_reply(suggestion: str, customer_excerpt: str = "") -> str:
    """确定性轻清理：去套话；第4段去 AI 符、压短，并按原问裁掉多余追问。"""
    if not suggestion:
        return suggestion
    lines = suggestion.splitlines()
    out: List[str] = []
    for line in lines:
        s = line.strip()
        if any(s == f or s.startswith(f) for f in _CUSTOMER_FLUFF):
            continue
        if s.startswith("首先，") or s.startswith("首先："):
            line = line.replace("首先，", "", 1).replace("首先：", "", 1)
        out.append(line)
    text = "\n".join(out)
    for phrase in ("综上所述，", "总而言之，", "值得注意的是，", "需要注意的是，"):
        text = text.replace(phrase, "")

    head, sec4 = _split_section4(text)
    if not sec4:
        return text
    sec4 = _strip_ai_markup(sec4).strip()
    sec4 = re.sub(r"\n{3,}", "\n\n", sec4)
    bullet_lines = [ln.strip() for ln in sec4.splitlines() if ln.strip()]
    if len(bullet_lines) > 1 and sum(
        1 for ln in bullet_lines if re.match(r"^(\d+[\.\)、]|[-•])\s*", ln)
    ) >= 2:
        cleaned = [re.sub(r"^(\d+[\.\)、]|[-•])\s*", "", ln) for ln in bullet_lines]
        parts = []
        for x in cleaned:
            x = x.strip()
            if not x:
                continue
            if not x.endswith(("。", "！", "？", ".", "!", "?")):
                x += "。"
            parts.append(x)
        sec4 = "".join(parts)
    else:
        sec4 = "\n".join(bullet_lines)
    sec4 = _trim_solicitation_tail(sec4, customer_excerpt)
    return f"{head.rstrip()}\n{sec4}\n"
