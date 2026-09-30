from __future__ import annotations

import logging
import time
from typing import List, Optional

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
