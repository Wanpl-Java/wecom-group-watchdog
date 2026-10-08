from __future__ import annotations

import html
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

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

    if not scheduler.running:
        scheduler.start()

    if settings.scan_interval_minutes > 0:
        scheduler.add_job(
            _scheduled_scan,
            "interval",
            minutes=settings.scan_interval_minutes,
            id="scan_unanswered",
            replace_existing=True,
            max_instances=1,
        )
        logger.info("scheduler scan: every %s minutes", settings.scan_interval_minutes)

    if settings.digest_interval_minutes > 0:
        scheduler.add_job(
            _scheduled_digest,
            "interval",
            minutes=settings.digest_interval_minutes,
            id="pending_digest",
            replace_existing=True,
            max_instances=1,
        )
        logger.info(
            "scheduler digest: every %s minutes (public_base=%s)",
            settings.digest_interval_minutes,
            (settings.public_base_url or f"http://127.0.0.1:{settings.app_port}"),
        )

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


async def _scheduled_digest() -> None:
    settings = get_settings()
    try:
        from .pipeline import push_pending_digest

        result = await push_pending_digest(
            get_store(), get_registry(), settings, force=False
        )
        logger.info("scheduled digest: %s", {k: result.get(k) for k in ("ok", "count", "skipped", "error")})
    except Exception:  # noqa: BLE001
        logger.exception("scheduled digest failed")


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
        "notify_channel": "wecom_webhook",
        "wecom_webhook_configured": bool((settings.wecom_notify_webhook or "").strip()),
        "feishu_webhook_configured": bool((settings.feishu_notify_webhook or "").strip()),
        "digest_interval_minutes": settings.digest_interval_minutes,
        "digest_sync_before_send": settings.digest_sync_before_send,
        "confluence_configured": bool((settings.confluence_token or "").strip()),
        "public_base_url": (settings.public_base_url or "").strip()
        or f"http://127.0.0.1:{settings.app_port}",
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


@app.get("/admin/pending")
async def admin_pending(
    settings: Settings = Depends(get_settings),
    store: MessageStore = Depends(get_store),
    registry: GroupRegistry = Depends(get_registry),
) -> Dict[str, Any]:
    """当前待跟进清单（未关闭、同事尚未回复）。"""
    from .pipeline import collect_pending_items

    items = collect_pending_items(store, registry, settings=settings)
    return {"ok": True, "count": len(items), "items": items}


@app.post("/admin/pending-digest")
async def admin_pending_digest(
    force: bool = Query(True, description="空清单也推送一张「当前无待跟进」"),
    settings: Settings = Depends(get_settings),
    store: MessageStore = Depends(get_store),
    registry: GroupRegistry = Depends(get_registry),
) -> Dict[str, Any]:
    """手动把未回复客户表推到企微（带详情超链接）。"""
    from .pipeline import push_pending_digest

    return await push_pending_digest(store, registry, settings, force=force)


def _render_case_html(
    meta: Dict[str, Any],
    *,
    waiting: Optional[float],
    tip: str = "",
) -> str:
    """详情页视觉对齐企微 markdown：分区彩色标题 + 可复制回复高亮块。"""
    from .notify import parse_suggestion_sections

    group = html.escape(str(meta.get("group_name") or ""))
    room = html.escape(str(meta.get("room_id") or ""))
    source = html.escape(str(meta.get("source") or "-"))
    wait_s = html.escape(str(waiting if waiting is not None else "-"))
    excerpt = html.escape(str(meta.get("excerpt") or meta.get("preview") or "（无）"))
    suggestion_raw = str(meta.get("suggestion") or "").strip()
    secs = parse_suggestion_sections(suggestion_raw)
    reply = (secs.get("reply") or suggestion_raw or "（暂无回复建议）").strip()
    qtype = (secs.get("type") or "").strip()
    analysis = (secs.get("analysis") or "").strip()
    content = (secs.get("content") or "").strip()
    token = html.escape(str(meta.get("detail_token") or ""))
    tip_html = f'<div class="tip">{html.escape(tip)}</div>' if tip else ""

    def _sec(
        title_class: str,
        title: str,
        body: str,
        *,
        quote: bool = False,
        is_reply: bool = False,
    ) -> str:
        if not (body or "").strip():
            return ""
        body_esc = html.escape(body.strip())
        box_cls = "reply-box" if is_reply else ("quote" if quote else "body")
        id_attr = ' id="replyText"' if is_reply else ""
        return (
            f'<div class="sec">'
            f'<div class="htag {title_class}">【{html.escape(title)}】</div>'
            f'<div class="{box_cls}"{id_attr}>{body_esc}</div>'
            f"</div>"
        )

    type_block = ""
    if qtype or content:
        type_body = qtype
        if content and content not in type_body:
            type_body = (type_body + "\n" + content).strip() if type_body else content
        type_block = _sec("c-info", "问题类型", type_body)

    sections_html = "".join(
        [
            _sec("c-info", "客户原话", excerpt, quote=True),
            type_block,
            _sec("c-info", "内部分析", analysis),
            _sec("c-warn", "可复制回复", reply, is_reply=True),
        ]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>待跟进 · {group}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; padding: 24px 14px 40px;
      font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
      background: #f3f4f6; color: #1f2937; line-height: 1.65;
    }}
    .wrap {{ max-width: 680px; margin: 0 auto; }}
    .bubble {{
      background: #fff; border-radius: 12px; padding: 20px 22px 18px;
      box-shadow: 0 1px 3px rgba(0,0,0,.06); border: 1px solid #e5e7eb;
    }}
    .title {{
      margin: 0 0 14px; font-size: 20px; font-weight: 700; color: #d97706;
    }}
    .meta {{ font-size: 14px; color: #4b5563; margin-bottom: 18px; }}
    .meta .row {{ margin: 4px 0; }}
    .meta .warn {{ color: #d97706; font-weight: 600; }}
    .meta code {{
      background: #fee2e2; color: #b91c1c; padding: 1px 6px; border-radius: 4px;
      font-size: 13px;
    }}
    .sec {{ margin-top: 16px; }}
    .htag {{ font-size: 15px; font-weight: 700; margin-bottom: 8px; }}
    .htag.c-info {{ color: #059669; }}
    .htag.c-warn {{ color: #d97706; }}
    .quote {{
      border-left: 3px solid #d1d5db; padding: 2px 0 2px 12px;
      color: #374151; white-space: pre-wrap; word-break: break-word; font-size: 15px;
    }}
    .body {{
      color: #374151; white-space: pre-wrap; word-break: break-word; font-size: 14px;
    }}
    .reply-box {{
      background: #fee2e2; color: #b91c1c; border-radius: 8px;
      padding: 12px 14px; white-space: pre-wrap; word-break: break-word;
      font-size: 15px; font-weight: 500; line-height: 1.7;
    }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }}
    button {{
      appearance: none; border: 0; border-radius: 10px; padding: 10px 16px;
      font-size: 14px; font-weight: 600; cursor: pointer;
    }}
    .primary {{ background: #2563eb; color: #fff; }}
    .secondary {{ background: #fff; color: #1f2937; border: 1px solid #d1d5db; }}
    .tip {{
      margin-bottom: 12px; padding: 10px 12px; border-radius: 10px;
      background: #ecfdf5; color: #047857; font-size: 14px; border: 1px solid #a7f3d0;
    }}
    .ok {{ color: #059669; font-size: 13px; align-self: center; }}
  </style>
</head>
<body>
  <div class="wrap">
    {tip_html}
    <div class="bubble">
      <h1 class="title">客户群待跟进提醒</h1>
      <div class="meta">
        <div class="row">群：<span class="warn">{group}</span></div>
        <div class="row">room：<code>{room}</code></div>
        <div class="row">已等待：<code>{wait_s}</code> 分钟 · 来源 <code>{source}</code></div>
      </div>
      {sections_html}
      <div class="actions">
        <button class="secondary" type="button" id="copyBtn" onclick="copyReply()">复制回复</button>
        <span class="ok" id="copied"></span>
      </div>
    </div>
  </div>
  <script>
    function copyReply() {{
      const el = document.getElementById('replyText');
      const tip = document.getElementById('copied');
      if (!el) return;
      const text = el.innerText || '';
      function ok() {{
        tip.textContent = '已复制';
        tip.style.color = '#059669';
        setTimeout(() => {{ tip.textContent = ''; }}, 2000);
      }}
      function fail(msg) {{
        tip.textContent = msg || '复制失败，请手动选中粉色区域复制';
        tip.style.color = '#b91c1c';
      }}
      // http://局域网IP 下 clipboard API 常被禁用，优先用 textarea 回退
      try {{
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, ta.value.length);
        const done = document.execCommand('copy');
        document.body.removeChild(ta);
        if (done) {{ ok(); return; }}
      }} catch (e) {{}}
      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(text).then(ok).catch(() => fail());
      }} else {{
        fail();
      }}
    }}
  </script>
</body>
</html>"""


@app.get("/case/{token}", response_class=HTMLResponse)
async def case_detail(
    token: str,
    settings: Settings = Depends(get_settings),
    store: MessageStore = Depends(get_store),
    registry: GroupRegistry = Depends(get_registry),
) -> HTMLResponse:
    """企微未回复表超链接：打开查看完整建议话术。"""
    from .models import SenderKind, UnansweredCase
    from .workbuddy import suggest_reply

    meta = store.get_open_alert_by_token(token)
    if not meta:
        raise HTTPException(status_code=404, detail="案件不存在或已关闭（同事已回复）")
    last_customer_at = float(meta.get("last_customer_at") or 0)
    waiting = round(max(0.0, (time.time() - last_customer_at) / 60.0), 1) if last_customer_at else None
    excerpt = str(meta.get("excerpt") or meta.get("preview") or "")
    suggestion = str(meta.get("suggestion") or "").strip()
    source = str(meta.get("source") or "stored")

    # 旧告警未落库完整建议时，打开链接再生成一次并写回
    if not suggestion:
        room_id = str(meta.get("room_id") or "")
        recent = annotate_messages(store.recent_messages(room_id, limit=20), registry)
        if not excerpt:
            for m in reversed(recent):
                if m.sender_kind == SenderKind.customer:
                    excerpt = (m.content or "").strip()
                    break
        g = registry.get(room_id)
        room_name = str(meta.get("group_name") or (g.name if g else room_id))
        case = UnansweredCase(
            room_id=room_id,
            group_name=room_name,
            product=(g.product if g else "js"),
            support_userids=registry.resolve_support_userids(g, room_name) if g else [],
            last_customer_msg_id=str(meta.get("last_customer_msg_id") or ""),
            last_customer_at=last_customer_at or time.time(),
            waiting_minutes=float(waiting or 0),
            recent_messages=recent,
            customer_excerpt=excerpt,
        )
        result = await suggest_reply(case, settings)
        suggestion = result.suggestion
        source = result.source
        key = str(meta.get("key") or "")
        if key:
            store.set_open_alert(
                key,
                {
                    **meta,
                    "excerpt": excerpt,
                    "suggestion": suggestion,
                    "source": source,
                    "preview": (excerpt or "")[:100],
                },
            )
            meta = store.get_open_alert_by_token(token) or meta

    meta = {**meta, "excerpt": excerpt, "suggestion": suggestion, "source": source}
    return HTMLResponse(_render_case_html(meta, waiting=waiting))


@app.post("/case/{token}/push")
async def case_push_wecom(
    token: str,
    settings: Settings = Depends(get_settings),
    store: MessageStore = Depends(get_store),
) -> HTMLResponse:
    """详情页按钮：把该案完整建议再推到企微。"""
    from .notify import (
        format_alert_markdown,
        primary_wecom_webhook,
        send_webhook_markdown,
    )

    meta = store.get_open_alert_by_token(token)
    if not meta:
        raise HTTPException(status_code=404, detail="案件不存在或已关闭")
    hook = primary_wecom_webhook(settings)
    if not hook:
        raise HTTPException(status_code=400, detail="WECOM_NOTIFY_WEBHOOK 未配置")
    last_customer_at = float(meta.get("last_customer_at") or 0)
    waiting = round(max(0.0, (time.time() - last_customer_at) / 60.0), 1) if last_customer_at else 0.0
    md = format_alert_markdown(
        group_name=str(meta.get("group_name") or ""),
        room_id=str(meta.get("room_id") or ""),
        waiting_minutes=float(waiting),
        excerpt=str(meta.get("excerpt") or meta.get("preview") or ""),
        suggestion=str(meta.get("suggestion") or "（暂无完整建议）"),
        source=str(meta.get("source") or "stored"),
    )
    resp = await send_webhook_markdown(hook, md, safe_mode=settings.safe_mode)
    ok = bool(resp) and not (isinstance(resp, dict) and (resp.get("error") or resp.get("errcode", 0)))
    tip = "已推送到企微" if ok else f"推送失败：{resp}"
    return HTMLResponse(_render_case_html(meta, waiting=waiting, tip=tip))


@app.post("/admin/suggest")
async def admin_suggest(
    body: Dict[str, Any],
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """
    GUI 模拟问答：不入库，直接走 WorkBuddy 建议链路。
    body.notify_wecom / notify_feishu / push=true 时，推到企微群消息推送 Webhook。
    body.force_real=true 时可在 safe_mode 下仍真实推送（仅供本地联调）。
    """
    import time as _time

    from .models import IngestMessage, SenderKind, UnansweredCase
    from .notify import format_alert_markdown, primary_wecom_webhook, send_webhook_markdown
    from .workbuddy import suggest_reply

    excerpt = str(body.get("question") or body.get("customer_excerpt") or "").strip()
    if not excerpt:
        raise HTTPException(status_code=400, detail="question required")
    group_name = str(body.get("group_name") or "【JS】GUI模拟群").strip()
    room_id = str(body.get("room_id") or "gui-sim").strip()
    product = str(body.get("product") or "js").strip() or "js"
    waiting = float(body.get("waiting_minutes") or 6)
    notify = bool(
        body.get("notify_wecom")
        or body.get("notify_feishu")
        or body.get("push_feishu")
        or body.get("push")
    )
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
        "notify_wecom": notify,
        "wecom_pushed": False,
        "wecom_resp": None,
        "safe_mode": settings.safe_mode and not force_real,
    }
    if notify:
        hook = primary_wecom_webhook(settings)
        if not hook:
            out["ok"] = False
            out["error"] = "WECOM_NOTIFY_WEBHOOK 未配置（企微内部群→添加消息推送→自定义）"
            return out
        md = "【GUI 模拟推送】\n" + format_alert_markdown(
            group_name=group_name,
            room_id=room_id,
            waiting_minutes=waiting,
            excerpt=excerpt,
            suggestion=result.suggestion,
            source=f"{result.source}+gui",
        )
        use_safe = settings.safe_mode and not force_real
        wecom_resp = await send_webhook_markdown(hook, md, safe_mode=use_safe)
        out["wecom_resp"] = wecom_resp
        out["wecom_pushed"] = bool(wecom_resp) and not (
            isinstance(wecom_resp, dict) and (wecom_resp.get("error") or wecom_resp.get("errcode", 0))
        )
        out["safe_mode"] = use_safe
        # 兼容旧字段名
        out["feishu_pushed"] = out["wecom_pushed"]
        out["feishu_resp"] = wecom_resp
        if use_safe:
            out["note"] = "safe_mode=true，未真实发企微（仅日志）。可传 force_real=true 强制实发。"
        elif not out["wecom_pushed"]:
            out["ok"] = False
            out["error"] = "企微推送失败，请检查 WECOM_NOTIFY_WEBHOOK"
    return out


@app.post("/admin/wecom-push")
@app.post("/admin/feishu-push")  # 兼容旧 GUI / 旧路径名
async def admin_wecom_push(
    body: Dict[str, Any],
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """把已有建议推到企微群消息推送。"""
    from .notify import format_alert_markdown, primary_wecom_webhook, send_webhook_markdown

    suggestion = str(body.get("suggestion") or "").strip()
    excerpt = str(body.get("question") or body.get("excerpt") or "").strip()
    if not suggestion:
        raise HTTPException(status_code=400, detail="suggestion required")
    group_name = str(body.get("group_name") or "【JS】GUI模拟群").strip()
    room_id = str(body.get("room_id") or "gui-sim").strip()
    waiting = float(body.get("waiting_minutes") or 6)
    source = str(body.get("source") or "gui").strip() or "gui"
    force_real = bool(body.get("force_real"))
    hook = primary_wecom_webhook(settings)
    if not hook:
        return {"ok": False, "error": "WECOM_NOTIFY_WEBHOOK 未配置"}
    md = "【GUI 模拟推送】\n" + format_alert_markdown(
        group_name=group_name,
        room_id=room_id,
        waiting_minutes=waiting,
        excerpt=excerpt or suggestion[:200],
        suggestion=suggestion,
        source=source,
    )
    use_safe = settings.safe_mode and not force_real
    wecom_resp = await send_webhook_markdown(hook, md, safe_mode=use_safe)
    pushed = bool(wecom_resp) and not (
        isinstance(wecom_resp, dict) and (wecom_resp.get("error") or wecom_resp.get("errcode", 0))
    )
    return {
        "ok": pushed or use_safe,
        "wecom_pushed": pushed and not use_safe,
        "feishu_pushed": pushed and not use_safe,  # 兼容旧字段
        "wecom_resp": wecom_resp,
        "feishu_resp": wecom_resp,
        "safe_mode": use_safe,
        "note": (
            "safe_mode=true，未真实发企微（仅日志）。可传 force_real=true 强制实发。"
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
