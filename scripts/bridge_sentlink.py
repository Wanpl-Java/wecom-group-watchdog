#!/usr/bin/env python3
"""
从 SentLink CSM 拉取会话存档消息，推送到 wecom-group-watchdog /ingest/messages。

用法（在 wecom-group-watchdog 目录）:
  .venv\\Scripts\\python.exe scripts\\bridge_sentlink.py
  .venv\\Scripts\\python.exe scripts\\bridge_sentlink.py --once
  .venv\\Scripts\\python.exe scripts\\bridge_sentlink.py --loop --interval 60

环境变量（也可写在 .env）:
  SENTLINK_BASE_URL=https://csm.fit2cloud.cn
  SENTLINK_API_KEY=sk_...
  SENTLINK_EXT_STAFF_ID=WanPeiLin          # 存档视角员工，一般填自己的 userid
  SENTLINK_SESSION_TYPE=room               # room | external | internal
  SENTLINK_PAGE_SIZE=50
  SENTLINK_MSG_PAGE_SIZE=50
  SENTLINK_TENANT_ID=                      # 可选，全局管理员可指定区域
  WATCHDOG_INGEST_URL=http://127.0.0.1:8092/ingest/messages
  WATCHDOG_INGEST_TOKEN=                   # 若 watchdog 配了 INGEST_TOKEN
  BRIDGE_STATE_FILE=./data/sentlink_bridge_state.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"seen_msg_ids": [], "last_msgtime": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen_msg_ids": [], "last_msgtime": 0}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 只保留最近 N 条去重指纹，避免无限膨胀
    seen = state.get("seen_msg_ids") or []
    if len(seen) > 5000:
        state["seen_msg_ids"] = seen[-5000:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def api_get(base: str, path: str, api_key: str, params: Dict[str, Any], tenant_id: str = "") -> Dict[str, Any]:
    qs = parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""}, doseq=True)
    url = base.rstrip("/") + path + (("?" + qs) if qs else "")
    headers = {
        "Accept": "application/json",
        "X-API-Key": api_key,
    }
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected response: {data!r}")
    if data.get("code") not in (0, "0", None):
        # some middleware returns {error:...}
        if "error" in data and data.get("code") is None:
            raise RuntimeError(f"auth/middleware error: {data}")
        raise RuntimeError(f"business error code={data.get('code')} message={data.get('message')}")
    return data


def post_ingest(url: str, token: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-Ingest-Token"] = token
    req = request.Request(url, data=payload, headers=headers, method="POST")
    with request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def map_sender_kind(item: Dict[str, Any]) -> str:
    st = (item.get("sender_type") or "").lower()
    if st in ("staff", "employee", "internal"):
        return "staff"
    if st in ("customer", "external", "client"):
        return "customer"
    fr = item.get("from") or ""
    if fr.startswith(("wm", "wo", "wb")):
        return "customer"
    # 默认：非外部前缀当员工（内部群/同事）
    return "staff" if fr and not fr.startswith("wm") else "customer"


def to_ingest_message(item: Dict[str, Any], room_name: str = "") -> Optional[Dict[str, Any]]:
    if (item.get("msgtype") or "") != "text":
        return None
    if (item.get("action") or "send") != "send":
        return None
    content = (item.get("content_text") or "").strip()
    if not content:
        return None
    msgid = item.get("msgid") or ""
    roomid = item.get("roomid") or item.get("ext_chat_id") or ""
    if not msgid or not roomid:
        return None
    msgtime = int(item.get("msgtime") or 0)
    # SentLink 存档时间为毫秒
    sent_at = msgtime / 1000.0 if msgtime > 10_000_000_000 else float(msgtime)
    return {
        "msg_id": msgid,
        "room_id": roomid,
        "room_name": room_name or item.get("group_chat_name") or "",
        "sender_id": item.get("from") or "",
        "sender_name": "",
        "sender_kind": map_sender_kind(item),
        "content": content,
        "sent_at": sent_at,
        "msg_type": "text",
    }


def is_js_room(name: str) -> bool:
    n = name or ""
    u = n.upper()
    return (
        ("【JS" in n)
        or ("[JS" in u)
        or ("JS】" in n)
        or ("JumpServer" in n)
        or ("JUMPSERVER" in u)
    )


def is_de_room(name: str) -> bool:
    n = name or ""
    u = n.upper()
    return (
        ("【DE" in n)
        or ("[DE" in u)
        or ("DE】" in n)
        or ("DataEase" in n)
        or ("DATAEASE" in u)
    )


def match_watch_product(name: str, watch_product: str) -> bool:
    wp = (watch_product or "all").strip().lower()
    if wp in ("", "all", "*"):
        return True
    if wp in ("js", "jumpserver"):
        return is_js_room(name) and not is_de_room(name)
    if wp in ("de", "dataease"):
        return is_de_room(name)
    return True


def sync_once() -> int:
    base = env("SENTLINK_BASE_URL", "https://csm.fit2cloud.cn")
    api_key = env("SENTLINK_API_KEY")
    staff = env("SENTLINK_EXT_STAFF_ID", "WanPeiLin")
    session_type = env("SENTLINK_SESSION_TYPE", "room")
    watch_product = env("WATCH_PRODUCT", "js")
    page_size = int(env("SENTLINK_PAGE_SIZE", "30") or "30")
    msg_page = int(env("SENTLINK_MSG_PAGE_SIZE", "40") or "40")
    tenant = env("SENTLINK_TENANT_ID")
    ingest_url = env("WATCHDOG_INGEST_URL", "http://127.0.0.1:8092/ingest/messages")
    ingest_token = env("WATCHDOG_INGEST_TOKEN")
    state_path = Path(env("BRIDGE_STATE_FILE", str(ROOT / "data" / "sentlink_bridge_state.json")))

    if not api_key:
        raise SystemExit("SENTLINK_API_KEY missing")

    state = load_state(state_path)
    seen = set(state.get("seen_msg_ids") or [])
    last_msgtime = int(state.get("last_msgtime") or 0)

    # 分页拉全量会话，再按 JS/DE 过滤
    items: List[Dict[str, Any]] = []
    page = 1
    while page <= 50:
        sessions = api_get(
            base,
            "/api/v1/console/group-chat/chat-sessions",
            api_key,
            {
                "ext_staff_id": staff,
                "session_type": session_type,
                "page": page,
                "page_size": page_size,
            },
            tenant_id=tenant,
        )
        chunk = ((sessions.get("data") or {}).get("items")) or []
        pager = (sessions.get("data") or {}).get("pager") or {}
        items.extend(chunk)
        total = int(pager.get("total") or pager.get("total_rows") or 0)
        if not chunk:
            break
        if total and len(items) >= total:
            break
        if len(chunk) < page_size:
            break
        page += 1

    # 按 room 去重，保留最新一条会话记录上的群名
    by_room: Dict[str, Dict[str, Any]] = {}
    for sess in items:
        room_id = sess.get("roomid") or ""
        if not room_id:
            continue
        prev = by_room.get(room_id)
        if not prev or int(sess.get("msgtime") or 0) > int(prev.get("msgtime") or 0):
            by_room[room_id] = sess
    room_list = list(by_room.values())
    room_list = [s for s in room_list if match_watch_product(s.get("group_chat_name") or "", watch_product)]
    print(
        f"[bridge] sessions={len(items)} rooms={len(by_room)} "
        f"watched={len(room_list)} product={watch_product} staff={staff} type={session_type}"
    )

    batch: List[Dict[str, Any]] = []
    max_msgtime = last_msgtime

    for sess in room_list:
        room_id = sess.get("roomid") or ""
        room_name = sess.get("group_chat_name") or ""
        if not room_id:
            continue
        msgs_resp = api_get(
            base,
            "/api/v1/console/group-chat/session-msgs",
            api_key,
            {
                "ext_chat_id": room_id,
                "ext_staff_id": staff,
                "page": 1,
                "page_size": msg_page,
                "sort_field": "msg_time",
                "sort_type": "desc",
            },
            tenant_id=tenant,
        )
        msgs = ((msgs_resp.get("data") or {}).get("items")) or []
        for m in msgs:
            mapped = to_ingest_message(m, room_name=room_name)
            if not mapped:
                continue
            mt = int(m.get("msgtime") or 0)
            if mapped["msg_id"] in seen:
                continue
            # 首次运行：只取最近 bootstrap 小时，避免灌入历史全量
            if last_msgtime == 0:
                bootstrap_h = float(env("BRIDGE_BOOTSTRAP_HOURS", "6") or "6")
                cutoff_ms = int((time.time() - bootstrap_h * 3600) * 1000)
                if mt and mt < cutoff_ms:
                    continue
            elif mt and mt <= last_msgtime:
                continue
            batch.append(mapped)
            seen.add(mapped["msg_id"])
            if mt > max_msgtime:
                max_msgtime = mt

    if not batch:
        print("[bridge] no new messages")
        state["seen_msg_ids"] = list(seen)
        state["last_msgtime"] = max_msgtime or last_msgtime
        save_state(state_path, state)
        return 0

    # ingest 按时间正序更利于判定
    batch.sort(key=lambda x: x["sent_at"])
    print(f"[bridge] posting {len(batch)} messages -> {ingest_url}")
    result = post_ingest(ingest_url, ingest_token, batch)
    print(f"[bridge] ingest result: {result}")

    state["seen_msg_ids"] = list(seen)
    state["last_msgtime"] = max_msgtime or last_msgtime
    save_state(state_path, state)
    return len(batch)


def main() -> int:
    parser = argparse.ArgumentParser(description="SentLink chat_msg -> watchdog ingest bridge")
    parser.add_argument("--once", action="store_true", default=True, help="run one sync (default)")
    parser.add_argument("--loop", action="store_true", help="loop forever")
    parser.add_argument("--interval", type=int, default=60, help="loop interval seconds")
    args = parser.parse_args()

    if args.loop:
        while True:
            try:
                sync_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[bridge] error: {exc}")
            time.sleep(max(10, args.interval))
    else:
        sync_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
