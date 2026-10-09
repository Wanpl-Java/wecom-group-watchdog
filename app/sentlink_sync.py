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
    """执行一次 bridge_sentlink.sync_once，返回 inserted 等摘要。"""
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
        inserted = int(mod.sync_once(full_today=True) or 0)
        logger.info("pre-send SentLink sync inserted=%s", inserted)
        return {"ok": True, "inserted": inserted}
    except SystemExit as exc:
        msg = str(exc) or "bridge SystemExit"
        logger.error("pre-send SentLink sync aborted: %s", msg)
        return {"ok": False, "error": msg}
    except Exception as exc:  # noqa: BLE001
        logger.exception("pre-send SentLink sync failed")
        return {"ok": False, "error": str(exc)}
