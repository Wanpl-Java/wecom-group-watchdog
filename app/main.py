from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import Settings, get_settings
from .crypto import WeComCryptoError, maybe_crypto
from .demo import build_demo_messages
from .groups import GroupRegistry
from .models import IngestRequest
from .pipeline import run_scan
from .scanner import annotate_messages
from .store import MessageStore

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_store: Optional[MessageStore] = None
_registry: Optional[GroupRegistry] = None


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, (level or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def get_store() -> MessageStore:
    assert _store is not None
    return _store


def get_registry() -> GroupRegistry:
    assert _registry is not None
    return _registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _registry
    settings = get_settings()
    _setup_logging(settings.log_level)
    _store = MessageStore(str(settings.data_path))
    _registry = GroupRegistry(settings.groups_config)

    if settings.message_source == "demo":
        msgs = annotate_messages(build_demo_messages(), _registry)
        n = _store.upsert_messages(msgs)
        logger.info("demo seed inserted/kept %s messages", n)

    if settings.scan_interval_minutes > 0:
        scheduler.add_job(
            _scheduled_scan,
            "interval",
            minutes=settings.scan_interval_minutes,
            id="scan_unanswered",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        logger.info("scheduler started: every %s minutes", settings.scan_interval_minutes)

    logger.info(
        "wecom-group-watchdog up safe_mode=%s workbuddy_mode=%s source=%s",
        settings.safe_mode,
        settings.workbuddy_mode,
        settings.message_source,
    )
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="WeCom Group Watchdog", version="0.1.0", lifespan=lifespan)


async def _scheduled_scan() -> None:
    settings = get_settings()
    try:
        result = await run_scan(get_store(), get_registry(), settings)
        logger.info("scheduled scan: %s", result.model_dump())
    except Exception:  # noqa: BLE001
        logger.exception("scheduled scan failed")


def _check_ingest_token(
    settings: Settings,
    authorization: Optional[str],
    x_ingest_token: Optional[str],
) -> None:
    expect = (settings.ingest_token or "").strip()
    if not expect:
        return
    token = ""
    if x_ingest_token:
        token = x_ingest_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token != expect:
        raise HTTPException(status_code=401, detail="invalid ingest token")


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    settings = get_settings()
    interval = _runtime_scan_minutes if _runtime_scan_minutes is not None else settings.scan_interval_minutes
    return {
        "ok": True,
        "safe_mode": settings.safe_mode,
        "workbuddy_mode": settings.workbuddy_mode,
        "message_source": settings.message_source,
        "scan_interval_minutes": interval,
        "unanswered_minutes": settings.unanswered_minutes,
    }


_runtime_scan_minutes: Optional[int] = None


def _reschedule_scan(minutes: int) -> None:
    """运行时调整定时扫描间隔（供 GUI）。"""
    global _runtime_scan_minutes
    if minutes <= 0:
        if scheduler.running and scheduler.get_job("scan_unanswered"):
            scheduler.remove_job("scan_unanswered")
        _runtime_scan_minutes = 0
        logger.info("scheduler scan job disabled")
        return
    _runtime_scan_minutes = minutes
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(
        _scheduled_scan,
        "interval",
        minutes=minutes,
        id="scan_unanswered",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("scheduler rescheduled: every %s minutes", minutes)


@app.api_route("/wecom/callback", methods=["GET", "POST"])
async def wecom_callback(
    request: Request,
    msg_signature: str = Query(default=""),
    timestamp: str = Query(default=""),
    nonce: str = Query(default=""),
    echostr: str = Query(default=""),
    settings: Settings = Depends(get_settings),
) -> Response:
    """
    企微「接收消息」URL 校验入口。
    用途：满足后台前置条件，从而能配置「企业可信 IP」。
    方案 A 业务不依赖这里收客户群消息。
    """
    crypto = maybe_crypto(settings)
    if crypto is None:
        raise HTTPException(
            status_code=503,
            detail="WECOM_CALLBACK_TOKEN / WECOM_ENCODING_AES_KEY / WECOM_CORP_ID not configured",
        )

    if request.method == "GET":
        try:
            plain = crypto.verify_url(msg_signature, timestamp, nonce, echostr)
        except WeComCryptoError as exc:
            logger.warning("wecom url verify failed: %s", exc)
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return PlainTextResponse(content=plain)

    # POST：仅确认可达，忽略业务事件
    logger.info("wecom callback POST received (ignored for send-only mode)")
    return PlainTextResponse(content="success")


@app.post("/ingest/messages")
async def ingest_messages(
    body: IngestRequest,
    authorization: Optional[str] = Header(default=None),
    x_ingest_token: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
    store: MessageStore = Depends(get_store),
    registry: GroupRegistry = Depends(get_registry),
) -> Dict[str, Any]:
    _check_ingest_token(settings, authorization, x_ingest_token)
    msgs = annotate_messages(body.messages, registry)
    inserted = store.upsert_messages(msgs)
    return {"ok": True, "received": len(msgs), "inserted": inserted}


@app.post("/admin/reload-groups")
async def reload_groups(registry: GroupRegistry = Depends(get_registry)) -> Dict[str, Any]:
    registry.reload()
    return {"ok": True, "groups": len(registry.all_rooms())}


@app.post("/admin/load-demo")
async def load_demo(
    store: MessageStore = Depends(get_store),
    registry: GroupRegistry = Depends(get_registry),
) -> Dict[str, Any]:
    msgs = annotate_messages(build_demo_messages(), registry)
    inserted = store.upsert_messages(msgs)
    return {"ok": True, "inserted": inserted, "total_seed": len(msgs)}


@app.post("/admin/scan")
async def admin_scan(
    force: bool = True,
    settings: Settings = Depends(get_settings),
    store: MessageStore = Depends(get_store),
    registry: GroupRegistry = Depends(get_registry),
) -> JSONResponse:
    result = await run_scan(store, registry, settings, force=force)
    return JSONResponse(result.model_dump())


@app.get("/admin/scan-interval")
async def get_scan_interval(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    minutes = _runtime_scan_minutes if _runtime_scan_minutes is not None else settings.scan_interval_minutes
    return {"ok": True, "scan_interval_minutes": minutes}


@app.post("/admin/scan-interval")
async def set_scan_interval(
    minutes: int = Query(..., ge=0, le=1440, description="0=关闭定时；建议 5/10"),
) -> Dict[str, Any]:
    _reschedule_scan(minutes)
    return {"ok": True, "scan_interval_minutes": minutes}


@app.post("/admin/suggest")
async def admin_suggest(
    body: Dict[str, Any],
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """
    GUI 模拟问答：不入库，直接走 WorkBuddy 建议链路。
    body.notify_feishu=true 时，把建议按正式告警格式推到飞书 webhook。
    body.force_real=true 时可在 safe_mode 下仍真实推送（仅供本地联调）。
    """
    import time as _time

    from .models import IngestMessage, SenderKind, UnansweredCase
    from .notify import build_feishu_alert_card, send_feishu_card
    from .workbuddy import suggest_reply

    excerpt = str(body.get("question") or body.get("customer_excerpt") or "").strip()
    if not excerpt:
        raise HTTPException(status_code=400, detail="question required")
    group_name = str(body.get("group_name") or "【JS】GUI模拟群").strip()
    room_id = str(body.get("room_id") or "gui-sim").strip()
    product = str(body.get("product") or "js").strip() or "js"
    waiting = float(body.get("waiting_minutes") or 6)
    notify_feishu = bool(body.get("notify_feishu") or body.get("push_feishu"))
    force_real = bool(body.get("force_real"))
    now = _time.time()
    msg = IngestMessage(
        msg_id=str(body.get("msg_id") or f"gui-{int(now)}"),
        room_id=room_id,
        room_name=group_name,
        sender_id=str(body.get("sender_id") or "customer-gui"),
        sender_name=str(body.get("sender_name") or "客户"),
        sender_kind=SenderKind.customer,
        content=excerpt,
        sent_at=now,
    )
    case = UnansweredCase(
        room_id=room_id,
        group_name=group_name,
        product=product,
        support_userids=list(body.get("support_userids") or []),
        last_customer_msg_id=msg.msg_id,
        last_customer_at=now,
        waiting_minutes=waiting,
        recent_messages=[msg],
        customer_excerpt=excerpt,
    )
    result = await suggest_reply(case, settings)
    out: Dict[str, Any] = {
        "ok": True,
        "source": result.source,
        "suggestion": result.suggestion,
        "question": excerpt,
        "group_name": group_name,
        "room_id": room_id,
        "notify_feishu": notify_feishu,
        "feishu_pushed": False,
        "feishu_resp": None,
        "safe_mode": settings.safe_mode and not force_real,
    }
    if notify_feishu:
        if not (settings.feishu_notify_webhook or "").strip():
            out["ok"] = False
            out["error"] = "FEISHU_NOTIFY_WEBHOOK 未配置"
            return out
        card = build_feishu_alert_card(
            group_name=group_name,
            room_id=room_id,
            waiting_minutes=waiting,
            excerpt=excerpt,
            suggestion=result.suggestion,
            source=f"{result.source}+gui",
            prefix="【GUI模拟】",
        )
        use_safe = settings.safe_mode and not force_real
        feishu_resp = await send_feishu_card(
            settings.feishu_notify_webhook,
            card,
            safe_mode=use_safe,
        )
        out["feishu_resp"] = feishu_resp
        out["feishu_pushed"] = bool(feishu_resp) and not (
            isinstance(feishu_resp, dict) and feishu_resp.get("error")
        )
        out["safe_mode"] = use_safe
        if use_safe:
            out["note"] = "safe_mode=true，未真实发飞书（仅日志）。可传 force_real=true 强制实发。"
        elif not out["feishu_pushed"]:
            out["ok"] = False
            out["error"] = "飞书推送失败，请检查 webhook"
    return out


@app.post("/admin/feishu-push")
async def admin_feishu_push(
    body: Dict[str, Any],
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """把已有建议文本推到飞书（GUI「推送上次结果」）。"""
    from .notify import build_feishu_alert_card, send_feishu_card

    suggestion = str(body.get("suggestion") or "").strip()
    excerpt = str(body.get("question") or body.get("excerpt") or "").strip()
    if not suggestion:
        raise HTTPException(status_code=400, detail="suggestion required")
    group_name = str(body.get("group_name") or "【JS】GUI模拟群").strip()
    room_id = str(body.get("room_id") or "gui-sim").strip()
    waiting = float(body.get("waiting_minutes") or 6)
    source = str(body.get("source") or "gui").strip() or "gui"
    force_real = bool(body.get("force_real"))
    if not (settings.feishu_notify_webhook or "").strip():
        return {"ok": False, "error": "FEISHU_NOTIFY_WEBHOOK 未配置"}
    card = build_feishu_alert_card(
        group_name=group_name,
        room_id=room_id,
        waiting_minutes=waiting,
        excerpt=excerpt or suggestion[:200],
        suggestion=suggestion,
        source=source,
        prefix="【GUI模拟】",
    )
    use_safe = settings.safe_mode and not force_real
    feishu_resp = await send_feishu_card(
        settings.feishu_notify_webhook,
        card,
        safe_mode=use_safe,
    )
    pushed = bool(feishu_resp) and not (
        isinstance(feishu_resp, dict) and feishu_resp.get("error")
    )
    return {
        "ok": pushed or use_safe,
        "feishu_pushed": pushed and not use_safe,
        "feishu_resp": feishu_resp,
        "safe_mode": use_safe,
        "note": (
            "safe_mode=true，未真实发飞书（仅日志）。可传 force_real=true 强制实发。"
            if use_safe
            else None
        ),
    }


@app.get("/admin/rooms")
async def list_rooms(
    store: MessageStore = Depends(get_store),
    registry: GroupRegistry = Depends(get_registry),
) -> Dict[str, Any]:
    rooms = []
    for room_id in sorted(set(store.list_room_ids()) | set(registry.all_rooms().keys())):
        g = registry.get(room_id)
        recent = store.recent_messages(room_id, limit=5)
        rooms.append(
            {
                "room_id": room_id,
                "configured": g is not None,
                "name": g.name if g else (recent[-1].room_name if recent else ""),
                "support_userids": g.support_userids if g else [],
                "message_count_sample": len(recent),
            }
        )
    return {"rooms": rooms}
