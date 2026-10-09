from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from .groups import GroupRegistry
from .models import GroupConfig, IngestMessage, SenderKind, UnansweredCase
from .store import MessageStore

logger = logging.getLogger(__name__)


def is_js_room_name(name: str) -> bool:
    n = name or ""
    u = n.upper()
    return (
        ("【JS" in n)
        or ("[JS" in u)
        or ("JS】" in n)
        or ("JumpServer" in n)
        or ("JUMPSERVER" in u)
    )


# 客户纯确认/应答：不应再当成「待跟进」
_ACK_EXACT = {
    "收到",
    "收到了",
    "好的",
    "好的谢谢",
    "好的，谢谢",
    "好的谢谢！",
    "谢谢",
    "谢谢！",
    "谢谢老师",
    "谢谢老师！",
    "感谢",
    "ok",
    "okay",
    "ok谢谢",
    "嗯",
    "嗯嗯",
    "嗯嗯好的",
    "好",
    "好的哈",
    "明白",
    "了解",
    "知道了",
    "已收到",
    "已阅",
    "1",
    "好滴",
    "收到谢谢",
    "收到，谢谢",
    "稍等",
    "稍等下",
    "稍等一下",
    "等下",
    "等一下",
    "好的稍等",
    "可以",
    "可以的",
    "方便",
    "方便的",
}


def _strip_punct(s: str) -> str:
    return (
        (s or "")
        .replace(" ", "")
        .replace("　", "")
        .replace("!", "")
        .replace("！", "")
        .replace("。", "")
        .replace(".", "")
        .replace("~", "")
        .replace("～", "")
        .replace("，", "")
        .replace(",", "")
        .replace("、", "")
    )


def is_customer_ack(content: str) -> bool:
    """客户短确认语（如「收到」）不算待跟进。"""
    raw = (content or "").strip()
    if not raw:
        return True
    compact = _strip_punct(raw)
    low = compact.lower()
    if low in {x.lower() for x in _ACK_EXACT}:
        return True
    # 极短且仅确认语义
    if len(compact) <= 8 and any(
        compact.startswith(p) or compact == p
        for p in ("收到", "好的", "谢谢", "感谢", "明白", "了解", "嗯", "稍等", "等下")
    ):
        # 含问号/求助则不算确认
        if any(x in raw for x in ("?", "？", "怎么", "如何", "吗", "呢", "失败", "报错", "不行")):
            return False
        return True
    return False


# 客户明确表示问题已结束（比「收到/谢谢」更强；可稍长）
_RESOLVED_PHRASES = (
    "已解决",
    "已经解决",
    "解决了",
    "搞好了",
    "弄好了",
    "处理好了",
    "可以了",
    "已经可以了",
    "好了可以了",
    "已经好了",
    "没问题了",
    "没有问题了",
    "不用了",
    "不需要了",
    "先不用了",
    "暂时不用了",
    "先这样吧",
    "先这样",
    "没事了",
    "已经通了",
    "通了",
    "能登了",
    "能登录了",
    "登录成功了",
    "连上了",
    "连得上了",
    "恢复了",
    "正常了",
    "好用了",
    "搞定了",
    "ok了",
    "ok啦",
    "已恢复",
    "问题解决了",
    "问题已解决",
)


def is_customer_issue_closed(content: str) -> bool:
    """
    客户明确表示问题已结束/不需要再跟。
    注意：单独「谢谢/好的」不算完结（仍走 is_customer_ack），避免礼貌用语误关。
    """
    raw = (content or "").strip()
    if not raw:
        return False
    # 仍在求助/报错，不算完结
    if any(
        x in raw
        for x in (
            "?",
            "？",
            "怎么",
            "如何",
            "吗",
            "呢",
            "失败",
            "报错",
            "不行",
            "还是",
            "仍然",
            "无法",
            "不能",
            "连不上",
            "登不上",
            "帮忙",
            "求助",
        )
    ):
        # 「还是不行」明确未解决
        if any(x in raw for x in ("还是", "仍然", "依旧", "还是不行", "还没", "还未")):
            return False
        # 「可以了吗」是在问，不算完结
        if any(x in raw for x in ("吗", "？", "?")):
            return False

    compact = _strip_punct(raw).lower()
    if not compact:
        return False
    # 整句很短且就是完结语
    for p in _RESOLVED_PHRASES:
        pl = _strip_punct(p).lower()
        if compact == pl or compact.startswith(pl) or pl in compact:
            # 过长闲聊里偶然出现「通了」等，要求整体不太长
            if len(compact) <= 40 or compact.endswith(pl) or compact.startswith(pl):
                return True
    # 「好了 + 谢谢」类（中等长度）
    if len(compact) <= 24 and ("好了" in compact or "可以了" in compact or "解决" in compact):
        if any(x in compact for x in ("谢谢", "感谢", "麻烦了", "多谢")):
            return True
    return False


_REACTION_TOKENS = (
    "哦",
    "噢",
    "喔",
    "好吧",
    "行吧",
    "那行",
    "那好吧",
    "知道了",
    "了解",
    "明白",
    "无语",
    "失望",
    "唉",
    "哎",
    "嗯",
    "ok",
    "okay",
)


def _strip_wecom_quote(content: str) -> str:
    """去掉企微引用块，返回客户自己跟的那截。"""
    raw = (content or "").strip()
    if not raw:
        return ""
    # 「被引用内容」 ----- 客户补充
    if "「" in raw and "」" in raw:
        after = raw.rsplit("」", 1)[-1]
        after = after.replace("-", "").replace("—", "").replace(" ", "").replace("　", "").strip()
        return after
    if "------" in raw:
        after = raw.split("------")[-1].strip()
        return after
    return raw


def is_customer_reaction_only(content: str) -> bool:
    """
    引用同事方案后只发表情/短反应（如「[失望]」「好吧」），没有新问题。
    不当作待跟进。
    """
    raw = (content or "").strip()
    if not raw:
        return False
    is_quote = ("「" in raw and "」" in raw) or ("------" in raw) or ("这是一条引用" in raw)
    rest = _strip_wecom_quote(raw) if is_quote else raw
    rest = rest.strip()
    if not rest:
        return True if is_quote else False
    # 仍在提问/报错则不算反应
    if any(
        x in rest
        for x in ("?", "？", "怎么", "如何", "吗", "呢", "失败", "报错", "不行", "还是", "无法", "不能", "帮忙")
    ):
        return False
    compact = _strip_punct(rest)
    compact = (
        compact.replace("[", "")
        .replace("]", "")
        .replace("【", "")
        .replace("】", "")
        .lower()
    )
    if not compact:
        return True if is_quote else False
    # 纯表情/很短反应
    if is_quote and len(compact) <= 12:
        return True
    if compact in {x.lower() for x in _REACTION_TOKENS}:
        return True
    if len(compact) <= 8 and any(t in compact for t in _REACTION_TOKENS):
        return True
    return False


_QUESTION_HINTS = (
    "?",
    "？",
    "吗",
    "呢",
    "么",
    "怎么",
    "如何",
    "为什么",
    "为啥",
    "咋",
    "能不能",
    "可不可以",
    "是否",
    "有没有",
    "啥",
    "帮忙",
    "麻烦",
    "失败",
    "报错",
    "不行",
    "无法",
    "不能",
    "异常",
    "登不上",
    "连不上",
    "没用",
    "没有用",
    "不管用",
    "没解决",
    "没好",
)


def customer_own_text(content: str) -> str:
    """去掉引用块和 @，只留客户自己写的字。"""
    text = _strip_wecom_quote(content or "")
    out = []
    skip = False
    for ch in text:
        if ch == "@":
            skip = True
            continue
        if skip:
            if ch in " \t\n\u2005\u200b\u00a0，。！？,.!?":
                skip = False
            else:
                continue
        out.append(ch)
    return "".join(out).strip()


def has_question_tone(content: str) -> bool:
    """客户这句有没有问句或求助语气。没有就不算新问题。"""
    text = customer_own_text(content)
    if not text:
        return False
    return any(h in text for h in _QUESTION_HINTS)


def no_new_question_after_staff(recent: list) -> bool:
    """同事已经回复过，且之后的客户消息都没有问句语气。"""
    last_staff_at = 0.0
    had_staff = False
    for m in recent:
        if m.sender_kind == SenderKind.staff:
            had_staff = True
            last_staff_at = max(last_staff_at, float(m.sent_at))
    if not had_staff:
        return False
    after = [
        m
        for m in recent
        if m.sender_kind == SenderKind.customer and float(m.sent_at) > last_staff_at
    ]
    if not after:
        return True
    return not any(has_question_tone(m.content or "") for m in after)


def customer_needs_no_followup(content: str) -> bool:
    """确认语、明确完结、或引用同事后的短反应 → 不需要再当待跟进。"""
    return (
        is_customer_ack(content)
        or is_customer_issue_closed(content)
        or is_customer_reaction_only(content)
    )


def _staff_before_customer(recent: list, customer_at: float) -> bool:
    """这条客户消息之前是否刚有同事发言（典型：发完方案客户回「收到」）。"""
    return any(
        m.sender_kind == SenderKind.staff and float(m.sent_at) <= float(customer_at)
        for m in recent
    )


def is_de_room_name(name: str) -> bool:
    n = name or ""
    u = n.upper()
    return (
        ("【DE" in n)
        or ("[DE" in u)
        or ("DE】" in n)
        or ("DataEase" in n)
        or ("DATAEASE" in u)
    )


def is_mk_room_name(name: str) -> bool:
    n = name or ""
    u = n.upper()
    return (
        ("【MK" in n)
        or ("[MK" in u)
        or ("MK】" in n)
        or ("MaxKB" in n)
        or ("MAXKB" in u)
    )


def _parse_watch_products(watch_product: str) -> List[str]:
    wp = (watch_product or "all").strip().lower()
    if wp in ("", "all", "*"):
        return ["all"]
    parts = [
        p.strip()
        for p in wp.replace("+", ",").replace("|", ",").replace(" ", ",").split(",")
        if p.strip()
    ]
    return parts or ["all"]


def _match_one_product(name: str, product: str, wp: str) -> bool:
    pname = (product or "").strip().lower()
    if wp in ("js", "jumpserver"):
        if pname in ("jumpserver", "js"):
            return True
        return is_js_room_name(name) and not is_de_room_name(name) and not is_mk_room_name(name)
    if wp in ("de", "dataease"):
        if pname in ("dataease", "de"):
            return True
        return is_de_room_name(name)
    if wp in ("mk", "maxkb"):
        if pname in ("maxkb", "mk"):
            return True
        return is_mk_room_name(name)
    return False


def match_watch_product(name: str, product: str, watch_product: str) -> bool:
    """watch_product: js|de|mk|all，或多个逗号分隔如 js,de,mk。"""
    parts = _parse_watch_products(watch_product)
    if "all" in parts:
        return True
    return any(_match_one_product(name, product, p) for p in parts)


def infer_product_from_room_name(name: str) -> str:
    if is_js_room_name(name):
        return "jumpserver"
    if is_de_room_name(name):
        return "dataease"
    if is_mk_room_name(name):
        return "maxkb"
    return ""


def resolve_sender_kind(
    msg: IngestMessage,
    registry: GroupRegistry,
) -> SenderKind:
    if msg.sender_kind and msg.sender_kind != SenderKind.unknown:
        return msg.sender_kind
    if registry.is_staff(msg.sender_id):
        return SenderKind.staff
    # 企微外部联系人常见前缀；其余未知按客户处理（外部群场景）
    sid = msg.sender_id or ""
    if sid.startswith("wm") or sid.startswith("wo") or sid.startswith("wb"):
        return SenderKind.customer
    if sid and not registry.is_staff(sid):
        return SenderKind.customer
    return SenderKind.unknown


def annotate_messages(
    messages: List[IngestMessage],
    registry: GroupRegistry,
) -> List[IngestMessage]:
    out: List[IngestMessage] = []
    for m in messages:
        kind = resolve_sender_kind(m, registry)
        out.append(m.model_copy(update={"sender_kind": kind}))
    return out


def find_unanswered(
    store: MessageStore,
    registry: GroupRegistry,
    unanswered_minutes: int,
    now: Optional[float] = None,
    watch_product: str = "all",
) -> List[UnansweredCase]:
    import time

    now_ts = now if now is not None else time.time()
    threshold = unanswered_minutes * 60
    cases: List[UnansweredCase] = []

    default_support = list(registry.file.staff_userids or [])
    room_ids = set(store.list_room_ids()) | set(registry.all_rooms().keys())
    for room_id in sorted(room_ids):
        group = registry.get(room_id)
        if group is None:
            if registry.file.ignore_unknown_rooms:
                continue
            # 未登记群：仍扫描；推送对象按群名区域规则解析
            group = GroupConfig(
                room_id=room_id,
                name="",
                product="",
                support_userids=[],
                enabled=True,
            )
        if not group.enabled:
            continue

        recent = annotate_messages(store.recent_messages(room_id, limit=40), registry)
        if not recent:
            continue

        room_name = group.name or (recent[-1].room_name if recent else "") or room_id
        if not match_watch_product(room_name, group.product, watch_product):
            continue

        last_customer: Optional[IngestMessage] = None
        last_staff_after_customer = False
        for msg in reversed(recent):
            if msg.sender_kind == SenderKind.customer:
                last_customer = msg
                # 看这条客户消息之后是否有同事回复
                last_staff_after_customer = any(
                    m.sender_kind == SenderKind.staff and m.sent_at > msg.sent_at
                    for m in recent
                )
                break

        if last_customer is None:
            continue
        if last_staff_after_customer:
            continue
        # 是否还要跟进交给 AI 判断，这里只保留「客户说完还没有同事再回」

        waiting = now_ts - float(last_customer.sent_at)
        if waiting < threshold:
            continue

        excerpt_lines = []
        for m in recent[-8:]:
            who = m.sender_name or m.sender_id or m.sender_kind.value
            role = m.sender_kind.value
            excerpt_lines.append(f"[{role}] {who}: {m.content[:200]}")

        support = registry.resolve_support_userids(group, room_name) or default_support
        product = group.product or infer_product_from_room_name(room_name)
        cases.append(
            UnansweredCase(
                room_id=room_id,
                group_name=group.name or last_customer.room_name or room_id,
                product=product,
                support_userids=support,
                last_customer_msg_id=last_customer.msg_id,
                last_customer_at=float(last_customer.sent_at),
                waiting_minutes=round(waiting / 60.0, 1),
                recent_messages=recent[-12:],
                customer_excerpt="\n".join(excerpt_lines),
            )
        )
    return cases


def in_work_hours(start: str, end: str, tz_name: str, now: Optional[datetime] = None) -> bool:
    if not start or not end:
        return True
    tz = ZoneInfo(tz_name)
    current = now.astimezone(tz) if now else datetime.now(tz)
    sh, sm = [int(x) for x in start.split(":")[:2]]
    eh, em = [int(x) for x in end.split(":")[:2]]
    minutes = current.hour * 60 + current.minute
    return (sh * 60 + sm) <= minutes <= (eh * 60 + em)
