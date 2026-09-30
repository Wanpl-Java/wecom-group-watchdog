#!/usr/bin/env python3
"""
WorkBuddy 侧最小同步 Webhook 样例。
正式环境由 WorkBuddy 技能/工作流接管：检索知识库后返回 suggestion。

启动:
  uvicorn scripts.mock_workbuddy_webhook:app --port 8093

本 watchdog 配置:
  WORKBUDDY_MODE=sync_webhook
  WORKBUDDY_WEBHOOK_URL=http://127.0.0.1:8093/workbuddy/suggest
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI

app = FastAPI(title="Mock WorkBuddy Suggest")


@app.post("/workbuddy/suggest")
async def suggest(body: Dict[str, Any]) -> Dict[str, str]:
    group = body.get("group_name") or body.get("room_id") or "客户群"
    excerpt = (body.get("customer_excerpt") or "")[:200]
    return {
        "suggestion": (
            f"【WorkBuddy 建议】针对「{group}」：\n"
            f"1) 先致谢并复述问题要点；\n"
            f"2) 请客户补充版本号/报错截图/复现步骤；\n"
            f"3) 参考知识库检索后组织正式回复。\n"
            f"---\n客户原文摘要:\n{excerpt}"
        )
    }


@app.get("/healthz")
async def healthz() -> Dict[str, bool]:
    return {"ok": True}
