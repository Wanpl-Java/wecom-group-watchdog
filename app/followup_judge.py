from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from .config import Settings
from .models import UnansweredCase

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM = (
    "你是 JumpServer/DataEase/MaxKB 企微售后值班助手，只判断：当前对话是否需要支持同事跟进。"
    "只输出一行 JSON，不要其它文字："
    '{"need_followup":true/false,"reason":"一句话"}。'
    "判定规则（按序，命中即止）："
    "1) 客户明确表示已解决/搞好了/可以了/不用了/没事了/能登了/连上了/正常了 → false；"
    "2) 客户只是确认收到/谢谢/好的/ok/表情，或引用同事答复后只发表情/失望/好吧等短反应、没有新问题 → false；"
    "2b) 同事已给完方案（含产品不支持/能力边界），客户未提出新问法 → false；"
    "3) 客户在提问题、报错、要方案、催进度、说还没好/还是不行 → true；"
    "4) 注意：单独「谢谢」「好的」不等于问题已解决；但「好了谢谢」「已经可以了」算完结 → false；"
    "5) 不确定时偏向 true（宁可提醒，不要漏未回复的真问题）。"
)


def _last_customer_text(case: UnansweredCase) -> str:
    for m in reversed(case.recent_messages or []):
        if getattr(m, "sender_kind", None) and m.sender_kind.value == "customer":
            return (m.content or "").strip()
    # excerpt 兜底：取最后一行 customer
    for line in reversed((case.customer_excerpt or "").splitlines()):
        if "[customer]" in line.lower() or line.strip().startswith("[customer]"):
            return line.split(":", 1)[-1].strip() if ":" in line else line.strip()
    return (case.customer_excerpt or "").strip()[:300]


def _parse_judge(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*need_followup[^{}]*\}", raw, re.I | re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


async def ai_needs_followup(case: UnansweredCase, settings: Settings) -> Tuple[bool, str]:
    """
    告警前二次判定。返回 (需要跟进, 原因)。
    AI 不可用或解析失败时默认 True（保持原超时告警行为）。
    """
    if not getattr(settings, "followup_ai_judge", True):
        return True, "judge_disabled"

    mode = (settings.workbuddy_mode or "").lower().strip()
    base = (settings.ai_gateway_base_url or "").rstrip("/")
    key = (settings.ai_gateway_api_key or "").strip()
    if mode not in ("ai_gateway", "openai") or not base or not key:
        return True, "ai_unavailable_default_alert"

    last = _last_customer_text(case)
    # 本地规则先挡一层，少打 AI、少误报
    from .scanner import is_customer_ack, is_customer_issue_closed, is_customer_reaction_only

    if is_customer_issue_closed(last):
        return False, "local_resolved"
    if is_customer_reaction_only(last):
        return False, "local_reaction"
    if is_customer_ack(last):
        return False, "local_ack"

    excerpt = (case.customer_excerpt or "")[-1200:]
    user = (
        f"群：{case.group_name}\n"
        f"已等待约 {case.waiting_minutes} 分钟无同事回复\n"
        f"客户最后一句：{last}\n"
        f"最近对话：\n{excerpt}\n"
    )
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": (settings.ai_gateway_model or "f2c-auto").strip(),
        "stream": False,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=min(45.0, float(settings.workbuddy_timeout_seconds or 60))) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        content = ""
        choices = data.get("choices") or []
        if choices:
            content = ((choices[0].get("message") or {}).get("content")) or ""
        parsed = _parse_judge(content)
        if not parsed:
            logger.warning("followup judge parse failed: %s", content[:200])
            return True, "parse_failed_default_alert"
        need = bool(parsed.get("need_followup"))
        reason = str(parsed.get("reason") or ("need" if need else "skip"))
        return need, reason
    except Exception:  # noqa: BLE001
        logger.exception("followup AI judge failed, default alert")
        return True, "judge_error_default_alert"
