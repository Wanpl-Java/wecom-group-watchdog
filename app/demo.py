from __future__ import annotations

import time
from typing import List

from .models import IngestMessage, SenderKind


def build_demo_messages(now: float | None = None) -> List[IngestMessage]:
    """构造两群样例：一群超时未回复，一群已有同事回复。"""
    ts = now if now is not None else time.time()
    return [
        # 群1：客户 20 分钟前说话，无同事回复 → 应告警
        IngestMessage(
            msg_id="demo-js-1",
            room_id="wr_demo_jumpserver_01",
            room_name="【JS】示例客户支持群",
            sender_id="wm_customer_a",
            sender_name="客户A",
            sender_kind=SenderKind.customer,
            content="你好，JumpServer 改密自动化跑完后账号还是登录失败，怎么排查？",
            sent_at=ts - 20 * 60,
        ),
        IngestMessage(
            msg_id="demo-js-2",
            room_id="wr_demo_jumpserver_01",
            room_name="【JS】示例客户支持群",
            sender_id="wm_customer_a",
            sender_name="客户A",
            sender_kind=SenderKind.customer,
            content="另外想确认下 SQL Server 资产探测端口是不是有问题",
            sent_at=ts - 18 * 60,
        ),
        # 群2：客户说话后同事已回 → 不应告警
        IngestMessage(
            msg_id="demo-de-1",
            room_id="wr_demo_dataease_01",
            room_name="【DE】示例客户支持群",
            sender_id="wm_customer_b",
            sender_name="客户B",
            sender_kind=SenderKind.customer,
            content="DataEase 仪表板导出 PDF 报错",
            sent_at=ts - 25 * 60,
        ),
        IngestMessage(
            msg_id="demo-de-2",
            room_id="wr_demo_dataease_01",
            room_name="【DE】示例客户支持群",
            sender_id="lisi",
            sender_name="李四",
            sender_kind=SenderKind.staff,
            content="收到，我先看下导出任务日志，稍回你。",
            sent_at=ts - 5 * 60,
        ),
    ]
