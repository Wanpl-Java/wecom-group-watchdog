from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from app.groups import GroupsRegistry
from app.store import Message, Store, dump_json


def classify_sender(sender_id: str, registry: GroupsRegistry, sender_type: Optional[str] = None) -> str:
    if sender_type in ("customer", "staff", "bot", "unknown"):
        return sender_type
    if not sender_id:
        return "unknown"
    if sender_id.startswith(("wm", "wo", "external_")):
        return "customer"
    if registry.is_staff(sender_id):
        return "staff"
    # 默认保守：未知当 customer，避免误判「已回复」
    return "customer"


def normalize_ingest_item(item: Dict[str, Any], registry: GroupsRegistry) -> Optional[Message]:
    msg_id = str(item.get("msg_id") or item.get("msgid") or "").strip()
    room_id = str(item.get("room_id") or item.get("chat_id") or item.get("roomid") or "").strip()
    sender_id = str(item.get("sender_id") or item.get("from") or item.get("sender") or "").strip()
    content = str(item.get("content") or item.get("text") or "").strip()
    if not msg_id or not room_id or not sender_id:
        return None
    send_time = item.get("send_time") or item.get("msgtime") or item.get("timestamp")
    if send_time is None:
        send_time = int(time.time())
    send_time = int(send_time)
    # 企微存档经常是毫秒
    if send_time > 10_000_000_000:
        send_time //= 1000
    sender_type = classify_sender(sender_id, registry, item.get("sender_type"))
    msg_type = str(item.get("msg_type") or item.get("msgtype") or "text")
    if msg_type != "text" and not content:
        content = f"[{msg_type}]"
    return Message(
        msg_id=msg_id,
        room_id=room_id,
        sender_id=sender_id,
        sender_type=sender_type,
        content=content,
        msg_type=msg_type,
        send_time=send_time,
        raw_json=dump_json(item),
    )


def ingest_messages(store: Store, registry: GroupsRegistry, items: List[Dict[str, Any]]) -> int:
    messages: List[Message] = []
    for item in items:
        msg = normalize_ingest_item(item, registry)
        if msg:
            messages.append(msg)
    return store.upsert_messages(messages)


def seed_demo_messages(store: Store) -> int:
    """写入两群样例：一群超时未回复，一群已回复。"""
    now = int(time.time())
    items = [
        Message(
            msg_id="demo_js_1",
            room_id="wr_demo_jumpserver_01",
            sender_id="external_alice",
            sender_type="customer",
            content="LDAP 登录 JumpServer 失败，提示 invalid credentials，帮忙看下？",
            msg_type="text",
            send_time=now - 25 * 60,
        ),
        Message(
            msg_id="demo_js_2",
            room_id="wr_demo_jumpserver_01",
            sender_id="external_alice",
            sender_type="customer",
            content="版本是 v4.10，目录服务用的是 Windows AD。",
            msg_type="text",
            send_time=now - 22 * 60,
        ),
        Message(
            msg_id="demo_de_1",
            room_id="wr_demo_dataease_01",
            sender_id="external_bob",
            sender_type="customer",
            content="DataEase 导出 Excel 超时了",
            msg_type="text",
            send_time=now - 40 * 60,
        ),
        Message(
            msg_id="demo_de_2",
            room_id="wr_demo_dataease_01",
            sender_id="lisi",
            sender_type="staff",
            content="收到，请提供仪表板名称和大概数据行数，我们帮你看超时配置。",
            msg_type="text",
            send_time=now - 35 * 60,
        ),
    ]
    return store.upsert_messages(items)
