from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from .models import GroupConfig, GroupsFile

logger = logging.getLogger(__name__)

_SHANGHAI_MARKERS = ("上海", "Shanghai", "SHANGHAI", "沪")


def infer_region(name: str, explicit: str = "", default_region: str = "other") -> str:
    raw = (explicit or "").strip().lower()
    if raw in ("shanghai", "sh", "沪", "上海"):
        return "shanghai"
    if raw in ("other", "default", "其他", "其它"):
        return "other"
    if raw:
        return raw
    n = name or ""
    if any(m in n for m in _SHANGHAI_MARKERS):
        return "shanghai"
    return (default_region or "other").strip().lower() or "other"


class GroupRegistry:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.file = GroupsFile()
        self._by_room: Dict[str, GroupConfig] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            logger.warning("groups config missing: %s (using empty)", self.path)
            self.file = GroupsFile()
            self._by_room = {}
            return
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.file = GroupsFile.model_validate(raw)
        self._by_room = {g.room_id: g for g in self.file.groups if g.enabled}
        logger.info("loaded %s groups from %s", len(self._by_room), self.path)

    def get(self, room_id: str) -> Optional[GroupConfig]:
        return self._by_room.get(room_id)

    def all_rooms(self) -> Dict[str, GroupConfig]:
        return dict(self._by_room)

    def staff_ids(self) -> Set[str]:
        ids: Set[str] = set(self.file.staff_userids or [])
        for g in self.file.groups:
            ids.update(g.support_userids or [])
        for users in (self.file.support_by_region or {}).values():
            if isinstance(users, list):
                ids.update(str(u) for u in users if u)
        return ids

    def is_staff(self, sender_id: str) -> bool:
        if not sender_id:
            return False
        return sender_id in self.staff_ids()

    def resolve_support_userids(
        self,
        group: Optional[GroupConfig],
        room_name: str = "",
    ) -> List[str]:
        """
        企微应用消息推送对象：
        1) 群配置显式 support_userids
        2) 否则按 region / 群名推断 → support_by_region
        3) 再否则 staff_userids
        """
        if group and group.support_userids:
            return [u for u in group.support_userids if u]
        name = (group.name if group else "") or room_name or ""
        region = infer_region(
            name,
            explicit=(group.region if group else ""),
            default_region=self.file.default_region or "other",
        )
        by_region = self.file.support_by_region or {}
        users = by_region.get(region) or by_region.get("other") or []
        if isinstance(users, str):
            users = [users]
        out = [str(u) for u in users if u]
        if out:
            return out
        return [u for u in (self.file.staff_userids or []) if u]