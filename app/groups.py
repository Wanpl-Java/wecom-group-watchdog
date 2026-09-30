from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Set

import yaml

from .models import GroupConfig, GroupsFile

logger = logging.getLogger(__name__)


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
        return ids

    def is_staff(self, sender_id: str) -> bool:
        if not sender_id:
            return False
        return sender_id in self.staff_ids()
