from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

_token_cache: dict = {"token": "", "expires_at": 0.0}


async def _get_access_token(settings: Settings) -> str:
    if not settings.wecom_corp_id or not settings.wecom_app_secret:
        return ""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return str(_token_cache["token"])
    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {"corpid": settings.wecom_corp_id, "corpsecret": settings.wecom_app_secret}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"gettoken failed: {data}")
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + int(data.get("expires_in", 7200))
    return str(_token_cache["token"])


async def send_app_text(
    settings: Settings,
    userids: List[str],
    content: str,
    safe_mode: bool,
) -> dict:
    touser = "|".join([u for u in userids if u])
    if not touser:
        return {"skipped": True, "reason": "no_userids"}

    if safe_mode:
        logger.info("[SAFE_MODE] would send app message to %s: %s", touser, content[:300])
        return {"safe_mode": True, "touser": touser}

    token = await _get_access_token(settings)
    if not token or not settings.wecom_agent_id:
        logger.warning("WeCom app credentials incomplete, skip app message")
        return {"skipped": True, "reason": "missing_credentials"}

    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    payload = {
        "touser": touser,
        "msgtype": "markdown",
        "agentid": settings.wecom_agent_id,
        "markdown": {"content": content[:2048]},
        "safe": 0,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    if data.get("errcode", 0) != 0:
        logger.error("send app message failed: %s", data)
    return data


async def send_webhook_markdown(webhook: str, content: str, safe_mode: bool) -> Optional[dict]:
    """企业微信群机器人 Webhook。"""
    if not webhook:
        return None
    if safe_mode:
        logger.info("[SAFE_MODE] would post wecom webhook: %s", content[:300])
        return {"safe_mode": True}
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
            return resp.json() if resp.content else {"ok": True}
    except Exception:  # noqa: BLE001
        logger.exception("wecom notify webhook failed")
        return {"error": True}


async def send_feishu_text(webhook: str, content: str, safe_mode: bool) -> Optional[dict]:
    """飞书自定义机器人 Webhook（msg_type=text）。"""
    if not webhook:
        return None
    if safe_mode:
        logger.info("[SAFE_MODE] would post feishu webhook: %s", content[:300])
        return {"safe_mode": True}
    # 飞书 text 有长度限制，截断避免失败
    text = content[:4000]
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
            data = resp.json() if resp.content else {"ok": True}
            code = None
            if isinstance(data, dict):
                code = data.get("code", data.get("StatusCode", 0))
            if code not in (0, None, "0"):
                logger.error("feishu webhook failed: %s", data)
            return data
    except Exception:  # noqa: BLE001
        logger.exception("feishu notify webhook failed")
        return {"error": True}


def parse_suggestion_sections(suggestion: str) -> Dict[str, str]:
    """拆四段输出，供飞书卡片分区展示。"""
    text = (suggestion or "").strip()
    sections = {"type": "", "content": "", "analysis": "", "reply": ""}
    if not text:
        return sections
    patterns = [
        ("type", r"1\)\s*问题类型与紧急程度"),
        ("content", r"2\)\s*问题内容"),
        ("analysis", r"3\)\s*问题分析"),
        ("reply", r"4\)\s*回复建议"),
    ]
    matches = []
    for key, pat in patterns:
        m = re.search(pat, text)
        if m:
            matches.append((m.start(), key, m.end()))
    matches.sort()
    for i, (start, key, end) in enumerate(matches):
        stop = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        body = text[end:stop].strip()
        sections[key] = body
    if not sections["reply"] and text:
        sections["reply"] = text
    return sections


def _md_escape_lite(s: str) -> str:
    return (s or "").replace("\r\n", "\n").strip()


def build_feishu_alert_card(
    *,
    group_name: str,
    room_id: str,
    waiting_minutes: float,
    excerpt: str,
    suggestion: str,
    source: str,
    prefix: str = "",
) -> dict:
    """飞书 interactive 卡片：分区展示，突出可复制回复。"""
    secs = parse_suggestion_sections(suggestion)
    reply = _md_escape_lite(secs.get("reply") or suggestion)[:1200]
    analysis = _md_escape_lite(secs.get("analysis") or "")[:800]
    qtype = _md_escape_lite(secs.get("type") or "")[:200]
    excerpt_s = _md_escape_lite(excerpt)[:600]
    title = "客户群待跟进"
    if prefix:
        title = f"{prefix.strip()} {title}".strip()

    elements: List[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"**群**：{group_name}\n"
                f"**等待**：{waiting_minutes} 分钟未内部回复\n"
                f"**来源**：{source}\n"
                f"**room**：`{room_id}`"
            ),
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"**客户原话**\n{excerpt_s or '（无）'}",
        },
    ]
    if qtype:
        elements.append({"tag": "markdown", "content": f"**类型**\n{qtype}"})
    if analysis:
        elements.append({"tag": "markdown", "content": f"**内部分析**\n{analysis}"})
    elements.extend(
        [
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"**可复制回复**\n{reply or '（暂无）'}",
            },
        ]
    )
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:40]},
            "template": "orange",
        },
        "body": {"direction": "vertical", "elements": elements},
    }


def build_feishu_resolved_card(
    *,
    group_name: str,
    room_id: str,
    note: str = "同事已在群内回复，本条可关闭。",
) -> dict:
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "客户群已跟进"},
            "template": "green",
        },
        "body": {
            "direction": "vertical",
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**群**：{group_name}\n"
                        f"**room**：`{room_id}`\n"
                        f"**状态**：已回复\n"
                        f"{note}"
                    ),
                }
            ],
        },
    }


def build_feishu_pending_list_card(
    items: List[Dict[str, Any]],
    *,
    title: str = "待跟进清单",
) -> dict:
    """汇总当前所有未关闭的待跟进（一条卡片看全貌）。"""
    if not items:
        body_md = "当前没有待跟进项。"
        template = "green"
    else:
        lines = [f"共 **{len(items)}** 条待跟进：\n"]
        for i, it in enumerate(items[:30], 1):
            g = _md_escape_lite(str(it.get("group_name") or it.get("room_id") or ""))
            wait = it.get("waiting_minutes")
            wait_s = f"{wait} 分钟" if wait is not None else "-"
            preview = _md_escape_lite(str(it.get("preview") or ""))[:120]
            lines.append(
                f"{i}. **{g}** · 已等 {wait_s}\n"
                f"   - 要点：{preview or '（无摘录）'}\n"
                f"   - room：`{it.get('room_id')}`"
            )
        if len(items) > 30:
            lines.append(f"\n…另有 {len(items) - 30} 条未列出")
        body_md = "\n".join(lines)
        template = "orange"
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:40]},
            "template": template,
        },
        "body": {
            "direction": "vertical",
            "elements": [{"tag": "markdown", "content": body_md[:3500]}],
        },
    }


async def send_feishu_card(webhook: str, card: dict, safe_mode: bool) -> Optional[dict]:
    """飞书自定义机器人：消息卡片（比纯文本更规范）。"""
    if not webhook:
        return None
    if safe_mode:
        logger.info("[SAFE_MODE] would post feishu card: %s", str(card)[:400])
        return {"safe_mode": True, "card": True}
    payload = {"msg_type": "interactive", "card": card}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
            data = resp.json() if resp.content else {"ok": True}
            code = None
            if isinstance(data, dict):
                code = data.get("code", data.get("StatusCode", 0))
            if code not in (0, None, "0"):
                logger.error("feishu card webhook failed: %s", data)
                # 卡片失败时降级纯文本，避免完全丢通知
                fallback = _card_to_plain(card)
                return await send_feishu_text(webhook, fallback, safe_mode=False)
            return data
    except Exception:  # noqa: BLE001
        logger.exception("feishu card webhook failed")
        try:
            return await send_feishu_text(webhook, _card_to_plain(card), safe_mode=False)
        except Exception:  # noqa: BLE001
            return {"error": True}


def _card_to_plain(card: dict) -> str:
    title = ""
    try:
        title = card.get("header", {}).get("title", {}).get("content", "") or ""
    except Exception:  # noqa: BLE001
        title = ""
    parts = [title] if title else []
    for el in (card.get("body") or {}).get("elements") or []:
        if el.get("tag") == "markdown" and el.get("content"):
            parts.append(str(el["content"]))
    return "\n\n".join(parts)[:3900]


async def send_generic_webhook(
    webhook: str,
    *,
    title: str,
    text: str,
    extra: Optional[dict] = None,
    safe_mode: bool = True,
) -> Optional[dict]:
    """通用 Webhook：邮件/短信网关等只要能收 JSON POST 即可对接。"""
    if not webhook:
        return None
    payload = {"title": title, "text": text}
    if extra:
        payload.update(extra)
    if safe_mode:
        logger.info("[SAFE_MODE] would post generic webhook: %s", str(payload)[:300])
        return {"safe_mode": True}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
            return resp.json() if resp.content else {"ok": True}
    except Exception:  # noqa: BLE001
        logger.exception("generic notify webhook failed")
        return {"error": True}


def format_alert_markdown(
    group_name: str,
    room_id: str,
    waiting_minutes: float,
    excerpt: str,
    suggestion: str,
    source: str,
) -> str:
    return (
        f"### 客户群待跟进提醒\n"
        f"> 群：**{group_name}**\n"
        f"> room_id: `{room_id}`\n"
        f"> 已等待：**{waiting_minutes}** 分钟无内部回复\n\n"
        f"**最近对话**\n"
        f"```\n{excerpt[:800]}\n```\n\n"
        f"**内部建议**（来源: {source}）\n"
        f"{suggestion[:2200]}"
    )
