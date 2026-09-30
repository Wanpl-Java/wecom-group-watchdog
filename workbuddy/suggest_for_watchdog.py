#!/usr/bin/env python3
"""
WorkBuddy local_cmd 适配器：接收 watchdog JSON，返回 suggestion。

优先走公司 AI 网关生成话术（由 watchdog 注入 AI_GATEWAY_* 环境变量）；
失败则回退到本地模板。后续可在此接入教学库 / kb / wiki 检索。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict


def _fallback(body: dict) -> str:
    group = body.get("group_name") or body.get("room_id") or "客户群"
    excerpt = (body.get("customer_excerpt") or "")[:400]
    wait = body.get("waiting_minutes", "?")
    return (
        f"【WorkBuddy】群「{group}」已等待约 {wait} 分钟未回复。\n"
        "1) 问题摘要：请根据下方对话确认客户最新诉求。\n"
        "2) 建议回复话术：先致谢并说明正在跟进，请客户补充版本/报错/复现步骤；"
        "不确定处写清「需进一步确认」，勿承诺排期与商务条款。\n"
        "3) 还需确认：产品版本、环境、报错全文、是否紧急。\n"
        f"---\n上下文:\n{excerpt}"
    )


def _via_ai_gateway(body: dict) -> str:
    base = (os.environ.get("AI_GATEWAY_BASE_URL") or "").rstrip("/")
    key = (os.environ.get("AI_GATEWAY_API_KEY") or "").strip()
    model = (os.environ.get("AI_GATEWAY_MODEL") or "f2c-auto").strip()
    if not base or not key:
        return ""

    hint = os.environ.get("SUGGEST_SYSTEM_HINT") or (
        "你是售后一线助手。根据客户群最新未回复内容给出："
        "1)问题摘要 2)建议回复话术 3)仍需向客户确认的信息。"
        "不要编造结论；不确定时写「建议转人工确认」。"
    )
    prompt = body.get("prompt") or (
        f"{hint}\n\n客户群: {body.get('group_name')}\n"
        f"产品线: {body.get('product') or '未知'}\n"
        f"已等待: {body.get('waiting_minutes')} 分钟\n"
        f"最近对话:\n{body.get('customer_excerpt') or ''}\n"
    )
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是飞致云售后一线助手（经 WorkBuddy 调度）。"
                    "只输出给内部同事看的建议，不要假装已回复客户。"
                    "结构固定为：1)问题摘要 2)建议回复话术 3)还需向客户确认的信息。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=55) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
        return ""
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def suggest_from_body(body: Dict[str, Any]) -> str:
    """供 watchdog 进程内调用（Windows 无 asyncio 子进程时）。"""
    return _via_ai_gateway(body) or _fallback(body)


def main() -> int:
    raw = sys.argv[-1] if len(sys.argv) > 1 else "{}"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {"customer_excerpt": raw}

    suggestion = suggest_from_body(body)
    print(json.dumps({"suggestion": suggestion, "source": "workbuddy_skill"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
