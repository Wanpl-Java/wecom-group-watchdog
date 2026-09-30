#!/usr/bin/env python3
"""统计 WanPeiLin 在 SentLink CSM 可见的 JS / DE 客户群。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import parse, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def api_get(base: str, path: str, api_key: str, params: dict) -> dict:
    qs = parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    url = base.rstrip("/") + path + (("?" + qs) if qs else "")
    req = request.Request(
        url,
        headers={"Accept": "application/json", "X-API-Key": api_key},
        method="GET",
    )
    with request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def classify(name: str) -> str:
    n = name or ""
    u = n.upper()
    is_js = ("【JS" in n) or ("[JS" in u) or ("JS】" in n) or ("JumpServer" in n) or ("JUMPSERVER" in u)
    is_de = ("【DE" in n) or ("[DE" in u) or ("DE】" in n) or ("DataEase" in n) or ("DATAEASE" in u)
    if is_js and is_de:
        return "BOTH"
    if is_js:
        return "JS"
    if is_de:
        return "DE"
    return "OTHER"


def main() -> int:
    base = env("SENTLINK_BASE_URL", "https://csm.fit2cloud.cn")
    api_key = env("SENTLINK_API_KEY")
    staff = env("SENTLINK_EXT_STAFF_ID", "WanPeiLin")
    if not api_key:
        raise SystemExit("SENTLINK_API_KEY missing")

    all_items: list = []
    page = 1
    page_size = 100
    while page <= 50:
        data = api_get(
            base,
            "/api/v1/console/group-chat/chat-sessions",
            api_key,
            {
                "ext_staff_id": staff,
                "session_type": "room",
                "page": page,
                "page_size": page_size,
            },
        )
        body = data.get("data") or {}
        items = body.get("items") or []
        pager = body.get("pager") or {}
        all_items.extend(items)
        total = int(pager.get("total") or pager.get("total_rows") or 0)
        print(f"page={page} got={len(items)} accum={len(all_items)} total_hint={total}")
        if not items:
            break
        if total and len(all_items) >= total:
            break
        if len(items) < page_size:
            break
        page += 1

    by_room: dict = {}
    for it in all_items:
        rid = it.get("roomid") or ""
        if not rid:
            continue
        name = (it.get("group_chat_name") or "").strip()
        prev = by_room.get(rid)
        if not prev or (it.get("msgtime") or 0) > (prev.get("msgtime") or 0):
            by_room[rid] = {
                "roomid": rid,
                "name": name,
                "msgtime": it.get("msgtime"),
                "session_id": it.get("session_id"),
            }

    rooms = list(by_room.values())
    buckets = {"JS": [], "DE": [], "BOTH": [], "OTHER": []}
    for r in rooms:
        cat = classify(r["name"])
        r["cat"] = cat
        buckets[cat].append(r)
    for k in buckets:
        buckets[k] = sorted(buckets[k], key=lambda x: x.get("name") or "")

    out = {
        "staff": staff,
        "corp": "ww918354e3468dc0cc",
        "source": base,
        "total_rooms": len(rooms),
        "js_count": len(buckets["JS"]),
        "de_count": len(buckets["DE"]),
        "both_count": len(buckets["BOTH"]),
        "other_count": len(buckets["OTHER"]),
        "js": [{"name": r["name"], "roomid": r["roomid"]} for r in buckets["JS"]],
        "de": [{"name": r["name"], "roomid": r["roomid"]} for r in buckets["DE"]],
        "both": [{"name": r["name"], "roomid": r["roomid"]} for r in buckets["BOTH"]],
        "other": [{"name": r["name"], "roomid": r["roomid"]} for r in buckets["OTHER"]],
    }
    path = ROOT / "data" / "wanpeilin_js_de_rooms.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("==== SUMMARY ====")
    print(f"staff={staff} rooms={len(rooms)}")
    print(
        f"JS={len(buckets['JS'])} DE={len(buckets['DE'])} "
        f"BOTH={len(buckets['BOTH'])} OTHER={len(buckets['OTHER'])}"
    )
    print("---- JS ----")
    for i, r in enumerate(buckets["JS"], 1):
        print(f"{i:02d}. {r['name']} | {r['roomid']}")
    print("---- DE ----")
    for i, r in enumerate(buckets["DE"], 1):
        print(f"{i:02d}. {r['name']} | {r['roomid']}")
    if buckets["BOTH"]:
        print("---- BOTH ----")
        for i, r in enumerate(buckets["BOTH"], 1):
            print(f"{i:02d}. {r['name']} | {r['roomid']}")
    print("---- OTHER (up to 40) ----")
    for i, r in enumerate(buckets["OTHER"][:40], 1):
        print(f"{i:02d}. {r['name']} | {r['roomid']}")
    if len(buckets["OTHER"]) > 40:
        print(f"... +{len(buckets['OTHER']) - 40} more")
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
