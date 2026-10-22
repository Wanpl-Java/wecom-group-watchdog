"""Digest / 推送前同步 SentLink 群消息，避免清单滞后。"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_BRIDGE = _ROOT / "scripts" / "bridge_sentlink.py"


def run_sentlink_sync() -> Dict[str, Any]:
    """执行一次 bridge catchup 同步（TTL + 待跟进 + 会话更新），返回摘要。"""
    if not _BRIDGE.is_file():
        return {"ok": False, "error": f"bridge missing: {_BRIDGE}"}
    try:
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        spec = importlib.util.spec_from_file_location("bridge_sentlink", _BRIDGE)
        if spec is None or spec.loader is None:
            return {"ok": False, "error": "cannot load bridge_sentlink"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 发 digest 前用 catchup：后台常驻已保温，这里只补漏，避免全量 100+ 群
        result = mod.sync_once(mode="catchup")
        if isinstance(result, dict):
            logger.info(
                "pre-send SentLink sync mode=%s inserted=%s fetched=%s/%s skipped=%s",
                result.get("mode"),
                result.get("inserted"),
                result.get("fetched_ok"),
                result.get("fetched_rooms"),
                result.get("skipped_quiet"),
            )
            return {"ok": bool(result.get("ok", True)), **result}
        inserted = int(result or 0)
        logger.info("pre-send SentLink sync inserted=%s", inserted)
        return {"ok": True, "inserted": inserted, "mode": "catchup"}
    except SystemExit as exc:
        msg = str(exc) or "bridge SystemExit"
        logger.error("pre-send SentLink sync aborted: %s", msg)
        return {"ok": False, "error": msg}
    except Exception as exc:  # noqa: BLE001
        logger.exception("pre-send SentLink sync failed")
        return {"ok": False, "error": str(exc)}
