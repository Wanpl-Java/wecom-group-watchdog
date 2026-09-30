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
}


def is_customer_ack(content: str) -> bool:
    """客户短确认语（如「收到」）不算待跟进。"""
    raw = (content or "").strip()
    if not raw:
        return True
    # 去掉常见标点再比
    compact = (
        raw.replace(" ", "")
        .replace("　", "")
        .replace("!", "")
        .replace("！", "")
        .replace("。", "")
        .replace(".", "")
        .replace("~", "")
        .replace("～", "")
    )
    low = compact.lower()
    if low in {x.lower() for x in _ACK_EXACT}:
        return True
    # 极短且仅确认语义
    if len(compact) <= 8 and any(
        compact.startswith(p) or compact == p
        for p in ("收到", "好的", "谢谢", "感谢", "明白", "了解", "嗯")
    ):
        # 含问号/求助则不算确认
        if any(x in raw for x in ("?", "？", "怎么", "如何", "吗", "呢", "失败", "报错", "不行")):
            return False
        return True
    return False


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


def match_watch_product(name: str, product: str, watch_product: str) -> bool:
    """watch_product: js|jumpserver|de|dataease|all|空."""
    wp = (watch_product or "all").strip().lower()
    if wp in ("", "all", "*"):
        return True
    pname = (product or "").strip().lower()
    if wp in ("js", "jumpserver"):
        if pname in ("jumpserver", "js"):
            return True
        return is_js_room_name(name) and not is_de_room_name(name)
    if wp in ("de", "dataease"):
        if pname in ("dataease", "de"):
            return True
        return is_de_room_name(name)
    return True


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
        # 「收到 / 好的 / ok」等确认语：尤其是同事刚发完方案后，不算待跟进
        if is_customer_ack(last_customer.content) and _staff_before_customer(
            recent, last_customer.sent_at
        ):
            continue
        # 纯确认且极短，即使前面没抓到 staff，也不告警（避免误报）
        if is_customer_ack(last_customer.content) and len((last_customer.content or "").strip()) <= 10:
            continue

        waiting = now_ts - float(last_customer.sent_at)
        if waiting < threshold:
            continue

        excerpt_lines = []
        for m in recent[-8:]:
            who = m.sender_name or m.sender_id or m.sender_kind.value
            role = m.sender_kind.value
            excerpt_lines.append(f"[{role}] {who}: {m.content[:200]}")

        support = registry.resolve_support_userids(group, room_name) or default_support
        product = group.product or (
            "jumpserver" if is_js_room_name(room_name) else ("dataease" if is_de_room_name(room_name) else "")
        )
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
