"""HTTP client for wecom-group-watchdog."""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class WatchdogClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8092", timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def healthz(self) -> Dict[str, Any]:
        with httpx.Client(timeout=15.0) as c:
            r = c.get(f"{self.base_url}/healthz")
            r.raise_for_status()
            return r.json()

    def get_interval(self) -> Dict[str, Any]:
        with httpx.Client(timeout=15.0) as c:
            r = c.get(f"{self.base_url}/admin/scan-interval")
            r.raise_for_status()
            return r.json()

    def set_interval(self, minutes: int) -> Dict[str, Any]:
        with httpx.Client(timeout=15.0) as c:
            r = c.post(
                f"{self.base_url}/admin/scan-interval",
                params={"minutes": minutes},
            )
            r.raise_for_status()
            return r.json()

    def force_scan(self) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/admin/scan", params={"force": "true"})
            r.raise_for_status()
            return r.json()

    def suggest(
        self,
        question: str,
        group_name: str = "【JS】GUI模拟群",
        waiting_minutes: float = 6.0,
        product: str = "js",
        notify_wecom: bool = False,
        force_real: bool = True,
    ) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(
                f"{self.base_url}/admin/suggest",
                json={
                    "question": question,
                    "group_name": group_name,
                    "waiting_minutes": waiting_minutes,
                    "product": product,
                    "notify_wecom": notify_wecom,
                    "force_real": force_real,
                },
            )
            r.raise_for_status()
            return r.json()

    def wecom_push(
        self,
        suggestion: str,
        question: str = "",
        group_name: str = "【JS】GUI模拟群",
        source: str = "gui",
        force_real: bool = True,
    ) -> Dict[str, Any]:
        """推送到企微群消息推送。"""
        with httpx.Client(timeout=30.0) as c:
            r = c.post(
                f"{self.base_url}/admin/wecom-push",
                json={
                    "suggestion": suggestion,
                    "question": question,
                    "group_name": group_name,
                    "source": source,
                    "force_real": force_real,
                },
            )
            r.raise_for_status()
            return r.json()

    # 兼容旧调用名
    def feishu_push(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.wecom_push(*args, **kwargs)

    def rooms(self) -> Dict[str, Any]:
        with httpx.Client(timeout=15.0) as c:
            r = c.get(f"{self.base_url}/admin/rooms")
            r.raise_for_status()
            return r.json()
