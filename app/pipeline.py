from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any, Dict, List, Optional

from .config import Settings
from .followup_judge import ai_needs_followup
from .groups import GroupRegistry
from .models import ScanResult, SenderKind
from .notify import (
    case_detail_url,
    format_alert_markdown,
    format_pending_list_markdown,
    format_resolved_markdown,
    primary_wecom_webhook,
    send_generic_webhook,
    send_webhook_markdown,
)
from .scanner import (
    annotate_messages,
    find_unanswered,
    in_work_hours,
    is_customer_issue_closed,
    is_customer_reaction_only,
)
from .sentlink_sync import run_sentlink_sync
from .store import MessageStore
from .workbuddy import suggest_reply

logger = logging.getLogger(__name__)


def _preview_from_excerpt(excerpt: str, limit: int = 100) -> str:
    text = (excerpt or "").strip().replace("\n", " ")
    return text[:limit]


def _ensure_detail_token(store: MessageStore, key: str, meta: Dict[str, Any]) -> str:
    token = str(meta.get("detail_token") or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(10)
    store.set_open_alert(key, {**meta, "detail_token": token})
    return token


def collect_pending_items(
    store: MessageStore,
    registry: GroupRegistry,
    settings: Optional[Settings] = None,
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
        # 客户最新一句已明确完结 → 不进未回复表
        last_cust_text = ""
        for m in reversed(recent):
            if m.sender_kind == SenderKind.customer:
                last_cust_text = (m.content or "").strip()
                break
        if last_cust_text and (
            is_customer_issue_closed(last_cust_text) or is_customer_reaction_only(last_cust_text)
        ):
            continue
        waiting = round(max(0.0, (now_ts - last_customer_at) / 60.0), 1) if last_customer_at else None
        preview = str(meta.get("preview") or "")
        if not preview and recent:
            for m in reversed(recent):
                if m.sender_kind == SenderKind.customer:
                    preview = (m.content or "").strip()[:100]
                    break
        token = _ensure_detail_token(store, key, meta)
        detail_url = case_detail_url(settings, token) if settings else ""
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
                "detail_token": token,
                "detail_url": detail_url,
                "has_suggestion": bool(str(meta.get("suggestion") or "").strip()),
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
    """同事已回复：推企微「已跟进」。"""
    webhook = primary_wecom_webhook(settings)
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
        last_cust_text = ""
        for m in reversed(recent):
            if m.sender_kind == SenderKind.customer:
                last_cust_text = (m.content or "").strip()
                break
        customer_closed = bool(
            last_cust_text
            and (
                is_customer_issue_closed(last_cust_text)
                or is_customer_reaction_only(last_cust_text)
            )
        )
        if not staff_replied and not customer_closed:
            continue
        group_name = str(meta.get("group_name") or room_id)
        md = format_resolved_markdown(group_name, room_id)
        resp = await send_webhook_markdown(webhook, md, safe_mode=settings.safe_mode)
        store.mark_alert_resolved_notified(key)
        result.details.append(
            {
                "room_id": room_id,
                "group_name": group_name,
                "status": "resolved_notified",
                "close_reason": "staff_replied" if staff_replied else "customer_closed",
                "wecom_resp": resp,
                "safe_mode": settings.safe_mode,
            }
        )
        logger.info(
            "resolved alert notified room=%s key=%s reason=%s",
            room_id,
            key,
            "staff_replied" if staff_replied else "customer_closed",
        )


async def push_pending_digest(
    store: MessageStore,
    registry: GroupRegistry,
    settings: Settings,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """推送未回复客户表（带详情超链接）到企微群消息推送。

    默认先 SentLink 同步再强制扫描，避免消息滞后导致误报/漏报。
    """
    sync_info: Dict[str, Any] = {"ok": True, "skipped": True}
    scan_info: Dict[str, Any] = {"skipped": True}
    if settings.digest_sync_before_send:
        sync_info = await asyncio.to_thread(run_sentlink_sync)
        try:
            scan_result = await run_scan(store, registry, settings, force=True)
            scan_info = {
                "skipped": False,
                "unanswered": scan_result.unanswered,
                "alerted": scan_result.alerted,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("pre-digest scan failed")
            scan_info = {"skipped": False, "error": str(exc)}

    items = collect_pending_items(store, registry, settings=settings)
    webhook = primary_wecom_webhook(settings)
    if not webhook:
        return {
            "ok": False,
            "error": "WECOM_NOTIFY_WEBHOOK 未配置（企微内部群→添加消息推送→自定义）",
            "items": items,
            "count": len(items),
            "sync": sync_info,
            "scan": scan_info,
        }
    if not items and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "empty_list",
            "count": 0,
            "items": [],
            "sync": sync_info,
            "scan": scan_info,
        }
    md = format_pending_list_markdown(items)
    resp = await send_webhook_markdown(webhook, md, safe_mode=settings.safe_mode)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "wecom_resp": resp,
        "safe_mode": settings.safe_mode,
        "public_base_url": case_detail_url(settings, "TOKEN").rsplit("/", 1)[0] + "/",
        "sync": sync_info,
        "scan": scan_info,
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
    wecom_hook = primary_wecom_webhook(settings)

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
            logger.info("skip alert room=%s reason=%s", case.room_id, judge_reason)
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

        # 主通道：企微内部群「自定义消息推送」
        wecom_resp = await send_webhook_markdown(
            wecom_hook,
            md,
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

        detail_token = secrets.token_urlsafe(10)
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
                "excerpt": case.customer_excerpt,
                "suggestion": suggest.suggestion,
                "source": suggest.source,
                "detail_token": detail_token,
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
                "wecom_resp": wecom_resp,
                "generic_resp": generic_resp,
                "suggestion_preview": suggest.suggestion[:200],
                "detail_url": case_detail_url(settings, detail_token),
            }
        )
        logger.info(
            "alerted room=%s waiting=%.1fm source=%s wecom=%s",
            case.room_id,
            case.waiting_minutes,
            suggest.source,
            bool(wecom_hook),
        )

    await _notify_resolved_alerts(store, registry, settings, result)
    # 未回复客户表改由 DIGEST_INTERVAL_MINUTES 定时推送，避免每次扫描刷屏
    if not wecom_hook and (changed or force):
        logger.warning("WECOM_NOTIFY_WEBHOOK empty: skip notify")

    return result
