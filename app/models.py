from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SenderKind(str, Enum):
    customer = "customer"
    staff = "staff"
    unknown = "unknown"


class IngestMessage(BaseModel):
    msg_id: str = Field(..., description="消息唯一 ID，用于去重")
    room_id: str = Field(..., description="客户群 ID / chat_id")
    room_name: str = ""
    sender_id: str = ""
    sender_name: str = ""
    # customer | staff | unknown；留空则由服务端按 groups.yaml 推断
    sender_kind: Optional[SenderKind] = None
    content: str = ""
    # Unix 秒；也可传 ISO8601 字符串由接口解析
    sent_at: float
    msg_type: str = "text"


class IngestRequest(BaseModel):
    messages: List[IngestMessage]


class GroupConfig(BaseModel):
    room_id: str
    name: str = ""
    product: str = ""
    support_userids: List[str] = Field(default_factory=list)
    enabled: bool = True


class GroupsFile(BaseModel):
    groups: List[GroupConfig] = Field(default_factory=list)
    ignore_unknown_rooms: bool = True
    staff_userids: List[str] = Field(default_factory=list)


class UnansweredCase(BaseModel):
    room_id: str
    group_name: str
    product: str
    support_userids: List[str]
    last_customer_msg_id: str
    last_customer_at: float
    waiting_minutes: float
    recent_messages: List[IngestMessage]
    customer_excerpt: str


class SuggestResult(BaseModel):
    suggestion: str
    source: str = "none"  # none | workbuddy_webhook | workbuddy_local | fallback
    raw: Optional[dict] = None


class ScanResult(BaseModel):
    scanned_rooms: int = 0
    unanswered: int = 0
    alerted: int = 0
    skipped_cooldown: int = 0
    skipped_offhours: int = 0
    details: List[dict] = Field(default_factory=list)
