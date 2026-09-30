from __future__ import annotations

import logging
import time
from typing import Optional

from .config import Settings
from .groups import GroupRegistry
from .models import ScanResult, SenderKind
from .notify import (
    build_feishu_alert_card,
    build_feishu_resolved_card,
    format_alert_markdown,
    send_app_text,
    send_feishu_card,
    send_generic_webhook,
    send_webhook_markdown,
)
from .scanner import annotate_messages, find_unanswered, in_work_hours
from .store import MessageStore
from .workbuddy import suggest_reply

logger = logging.getLogger(__name__)


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
            },
        )
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

    await _notify_resolved_alerts(store, registry, settings, result)
    return result
