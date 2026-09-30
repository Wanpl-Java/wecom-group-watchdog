#!/usr/bin/env python3
"""本地冒烟：不依赖企微 / WorkBuddy，验证判超时 + 告警链路（SAFE_MODE）。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 强制使用临时数据目录与示例配置，避免污染真实 data/
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="wgw-smoke-")
os.environ["GROUPS_CONFIG"] = str(ROOT / "config" / "groups.example.yaml")
os.environ["SAFE_MODE"] = "true"
os.environ["MESSAGE_SOURCE"] = "demo"
os.environ["WORKBUDDY_MODE"] = "none"
os.environ["SCAN_INTERVAL_MINUTES"] = "0"  # 冒烟时不启调度
os.environ["WORK_HOURS_START"] = ""
os.environ["WORK_HOURS_END"] = ""


async def main() -> int:
    from app.config import get_settings
    from app.demo import build_demo_messages
    from app.groups import GroupRegistry
    from app.pipeline import run_scan
    from app.scanner import annotate_messages, find_unanswered
    from app.store import MessageStore

    get_settings.cache_clear()
    settings = get_settings()
    store = MessageStore(str(settings.data_path))
    registry = GroupRegistry(settings.groups_config)
    msgs = annotate_messages(build_demo_messages(), registry)
    store.upsert_messages(msgs)

    cases = find_unanswered(store, registry, settings.unanswered_minutes)
    assert len(cases) == 1, f"expected 1 unanswered, got {len(cases)}: {cases}"
    assert cases[0].room_id == "wr_demo_jumpserver_01"
    print(f"[ok] unanswered case: {cases[0].group_name} wait={cases[0].waiting_minutes}m")

    result = await run_scan(store, registry, settings, force=True)
    assert result.unanswered == 1
    assert result.alerted == 1
    assert result.details[0]["suggest_source"] == "fallback"
    print(f"[ok] scan alerted={result.alerted} source={result.details[0]['suggest_source']}")
    print("[ok] smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
