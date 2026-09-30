from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)


class WeComNotifier:
    """通过自建应用消息提醒值班支持号；可选群机器人 Webhook。"""

    def __init__(
        self,
        corp_id: str,
        secret: str,
        agent_id: int,
        webhook: str = "",
        timeout: float = 20.0,
    ) -> None:
        self.corp_id = corp_id or ""
        self.secret = secret or ""
        self.agent_id = int(agent_id or 0)
        self.webhook = webhook or ""
        self.timeout = timeout
        self._token = ""
        self._token_expire_at = 0.0

    @property
    def app_enabled(self) -> bool:
        return bool(self.corp_id and self.secret and self.agent_id)

    async def get_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expire_at - 60:
            return self._token
        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params={"corpid": self.corp_id, "corpsecret": self.secret})
            resp.raise_for_status()
            data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"gettoken failed: {data}")
        self._token = data["access_token"]
        self._token_expire_at = now + int(data.get("expires_in", 7200))
        return self._token

    async def notify_users(self, userids: List[str], content: str, safe_mode: bool) -> Dict[str, Any]:
        result: Dict[str, Any] = {"app": None, "webhook": None, "safe_mode": safe_mode}
        if safe_mode:
            logger.info("[SAFE_MODE] would notify users=%s content=\n%s", userids, content)
            return result

        if self.app_enabled and userids:
            result["app"] = await self._send_app_text(userids, content)
        if self.webhook:
            result["webhook"] = await self._send_webhook(content)
        if not self.app_enabled and not self.webhook:
            logger.warning("no notify channel configured; skip real notify")
        return result

    async def _send_app_text(self, userids: List[str], content: str) -> Dict[str, Any]:
        # 企微 text 单次约 2048 字节，截断保底
        text = content if len(content.encode("utf-8")) <= 2000 else content[:600] + "\n...(已截断)"
        access_token = await self.get_access_token()
        payload = {
            "touser": "|".join(userids),
            "msgtype": "text",
            "agentid": self.agent_id,
            "text": {"content": text},
            "safe": 0,
        }
        url = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, params={"access_token": access_token}, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if data.get("errcode", 0) != 0:
            logger.error("send app message failed: %s", data)
            raise RuntimeError(f"send app message failed: {data}")
        return data

    async def _send_webhook(self, content: str) -> Dict[str, Any]:
        payload = {"msgtype": "markdown", "markdown": {"content": content[:3500]}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.webhook, json=payload)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:  # noqa: BLE001
                return {"status_code": resp.status_code}
