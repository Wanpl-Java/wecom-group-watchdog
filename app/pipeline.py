from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .config import Settings
from .groups import GroupRegistry
from .models import ScanResult, SenderKind
from .notify import (
    build_feishu_alert_card,
    build_feishu_pending_list_card,
    build_feishu_resolved_card,
    format_alert_markdown,
    send_app_text,
    send_feishu_card,
    send_generic_webhook,
    send_webhook_markdown,
)
from .followup_judge import ai_needs_followup
from .scanner import annotate_messages, find_unanswered, in_work_hours
from .store import MessageStore
from .workbuddy import suggest_reply

logger = logging.getLogger(__name__)


def _preview_from_excerpt(excerpt: str, limit: int = 100) -> str:
    text = (excerpt or "").strip().replace("\n", " ")
    return text[:limit]


def collect_pending_items(
    store: MessageStore,
    registry: GroupRegistry,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """当前未关闭的待跟进清单（以 open_alerts 为准，并校验仍无同事回复）。"""
    now_ts = now if now is not None else time.time()
    items: List[Dict[str, Any]] = []
    for key, meta in list(store.list_open_alerts().items()):
        room_id = str(meta.get("room_id") or "")
        if not room_id:
            continue
        last_customer_at = float(meta.get("last_customer_at") or 0)
        recent = annotate_messages(store.recent_messages(room_id, limit=40), registry)
        staff_replied = any(
            m.sender_kind == SenderKind.staff and float(m.sent_at) > last_customer_at
            for m in recent
        )
        if staff_replied:
            continue
        waiting = round(max(0.0, (now_ts - last_customer_at) / 60.0), 1) if last_customer_at else None
        preview = str(meta.get("preview") or "")
        if not preview and recent:
            # 取最后一条客户话
            for m in reversed(recent):
                if m.sender_kind == SenderKind.customer:
                    preview = (m.content or "").strip()[:100]
                    break
        items.append(
            {
                "key": key,
                "room_id": room_id,
                "group_name": str(meta.get("group_name") or room_id),
                "waiting_minutes": waiting,
                "last_customer_at": last_customer_at,
                "last_customer_msg_id": meta.get("last_customer_msg_id"),
                "preview": preview,
                "alerted_at": meta.get("alerted_at"),
            }
        )
    items.sort(key=lambda x: float(x.get("waiting_minutes") or 0), reverse=True)
    return items


async def _notify_resolved_alerts(
    store: MessageStore,
    registry: GroupRegistry,
    settings: Settings,
    result: ScanResult,
) -> None:
    """已推送过、且同事已回复的告警：再发一条绿色「已跟进」卡片。"""
    webhook = (settings.feishu_notify_webhook or "").strip()
    if not webhook:
        return
    for key, meta in list(store.list_open_alerts().items()):
        if meta.get("resolved_notified"):
            continue
        room_id = str(meta.get("room_id") or "")
        if not room_id:
            continue
        last_customer_at = float(meta.get("last_customer_at") or 0)
        recent = annotate_messages(store.recent_messages(room_id, limit=40), registry)
        staff_replied = any(
            m.sender_kind == SenderKind.staff and float(m.sent_at) > last_customer_at
            for m in recent
        )
        if not staff_replied:
            continue
        group_name = str(meta.get("group_name") or room_id)
        card = build_feishu_resolved_card(group_name=group_name, room_id=room_id)
        resp = await send_feishu_card(webhook, card, safe_mode=settings.safe_mode)
        store.mark_alert_resolved_notified(key)
        result.details.append(
            {
                "room_id": room_id,
                "group_name": group_name,
                "status": "resolved_notified",
                "feishu_resp": resp,
                "safe_mode": settings.safe_mode,
            }
        )
        logger.info("resolved alert notified room=%s key=%s", room_id, key)


async def push_pending_digest(
    store: MessageStore,
    registry: GroupRegistry,
    settings: Settings,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """推送当前待跟进清单到飞书。"""
    items = collect_pending_items(store, registry)
    webhook = (settings.feishu_notify_webhook or "").strip()
    if not webhook:
        return {"ok": False, "error": "FEISHU_NOTIFY_WEBHOOK 未配置", "items": items, "count": len(items)}
    if not items and not force:
        return {"ok": True, "skipped": True, "reason": "empty_list", "count": 0, "items": []}
    card = build_feishu_pending_list_card(items)
    resp = await send_feishu_card(webhook, card, safe_mode=settings.safe_mode)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "feishu_resp": resp,
        "safe_mode": settings.safe_mode,
    }


async def run_scan(
    store: MessageStore,
    registry: GroupRegistry,
    settings: Settings,
    force: bool = False,
) -> ScanResult:
    result = ScanResult()
    if not force and settings.work_hours_enabled:
        if not in_work_hours(settings.work_hours_start, settings.work_hours_end, settings.timezone):
            result.skipped_offhours = 1
            logger.info("skip scan: outside work hours")
            return result

    cases = find_unanswered(
        store,
        registry,
        settings.unanswered_minutes,
        watch_product=settings.watch_product,
    )
    result.scanned_rooms = len(set(store.list_room_ids()) | set(registry.all_rooms().keys()))
    result.unanswered = len(cases)
    cooldown = settings.alert_cooldown_minutes * 60
    now = time.time()
    changed = False

    for case in cases:
        alert_key = f"{case.room_id}:{case.last_customer_msg_id}"
        last = store.get_alert_at(alert_key)
        if last is not None and (now - last) < cooldown and not force:
            result.skipped_cooldown += 1
            result.details.append(
                {
                    "room_id": case.room_id,
                    "status": "cooldown",
                    "group_name": case.group_name,
                }
            )
            continue

        need, judge_reason = await ai_needs_followup(case, settings)
        if not need:
            result.skipped_no_followup += 1
            result.details.append(
                {
                    "room_id": case.room_id,
                    "group_name": case.group_name,
                    "status": "skipped_no_followup",
                    "judge_reason": judge_reason,
                }
            )
            logger.info(
                "skip alert room=%s reason=%s",
                case.room_id,
                judge_reason,
            )
            continue

        suggest = await suggest_reply(case, settings)
        md = format_alert_markdown(
            group_name=case.group_name,
            room_id=case.room_id,
            waiting_minutes=case.waiting_minutes,
            excerpt=case.customer_excerpt,
            suggestion=suggest.suggestion,
            source=suggest.source,
        )
        card = build_feishu_alert_card(
            group_name=case.group_name,
            room_id=case.room_id,
            waiting_minutes=case.waiting_minutes,
            excerpt=case.customer_excerpt,
            suggestion=suggest.suggestion,
            source=suggest.source,
        )

        app_resp = await send_app_text(
            settings,
            case.support_userids,
            md,
            safe_mode=settings.safe_mode,
        )
        hook_resp = await send_webhook_markdown(
            settings.wecom_notify_webhook,
            md,
            safe_mode=settings.safe_mode,
        )
        feishu_resp = None
        if (settings.feishu_notify_webhook or "").strip():
            feishu_resp = await send_feishu_card(
                settings.feishu_notify_webhook,
                card,
                safe_mode=settings.safe_mode,
            )
        generic_resp = await send_generic_webhook(
            settings.generic_notify_webhook,
            title=f"客户群待跟进: {case.group_name}",
            text=md,
            extra={
                "group_name": case.group_name,
                "room_id": case.room_id,
                "waiting_minutes": case.waiting_minutes,
                "suggestion": suggest.suggestion,
                "support_userids": case.support_userids,
            },
            safe_mode=settings.safe_mode,
        )

        store.set_alert_at(alert_key, now)
        store.set_open_alert(
            alert_key,
            {
                "room_id": case.room_id,
                "group_name": case.group_name,
                "last_customer_at": case.last_customer_at,
                "last_customer_msg_id": case.last_customer_msg_id,
                "alerted_at": now,
                "preview": _preview_from_excerpt(case.customer_excerpt),
            },
        )
        changed = True
        result.alerted += 1
        result.details.append(
            {
                "room_id": case.room_id,
                "group_name": case.group_name,
                "status": "alerted",
                "waiting_minutes": case.waiting_minutes,
                "suggest_source": suggest.source,
                "safe_mode": settings.safe_mode,
                "app_resp": app_resp,
                "webhook_resp": hook_resp,
                "feishu_resp": feishu_resp,
                "generic_resp": generic_resp,
                "suggestion_preview": suggest.suggestion[:200],
            }
        )
        logger.info(
            "alerted room=%s waiting=%.1fm source=%s safe=%s",
            case.room_id,
            case.waiting_minutes,
            suggest.source,
            settings.safe_mode,
        )

    before_open = len(store.list_open_alerts())
    await _notify_resolved_alerts(store, registry, settings, result)
    after_open = len(store.list_open_alerts())
    if after_open != before_open:
        changed = True

    # 有新增/关闭时，推一张汇总清单；force 扫描也推
    if (changed or force) and (settings.feishu_notify_webhook or "").strip():
        digest = await push_pending_digest(store, registry, settings, force=True)
        result.details.append(
            {
                "status": "pending_digest",
                "count": digest.get("count"),
                "feishu_resp": digest.get("feishu_resp"),
            }
        )

    return result
