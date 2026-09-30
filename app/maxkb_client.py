from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class MaxKBClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        mode: str = "openai",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.mode = (mode or "openai").lower()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def ask(self, question: str, session_key: str) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "answer": (
                    "（MaxKB 未配置）请人工查看客户原话后回复。\n"
                    f"客户内容：\n{question}"
                ),
                "chat_id": None,
                "raw": None,
            }
        if self.mode == "native":
            return await self._ask_native(question)
        return await self._ask_openai(question, session_key)

    async def _ask_openai(self, question: str, session_key: str) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "maxkb",
            "stream": False,
            "messages": [{"role": "user", "content": question}],
            "user": session_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        answer = self._extract_openai_answer(data)
        return {"answer": answer, "chat_id": None, "raw": data}

    async def _ask_native(self, question: str) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            open_resp = await client.get(f"{self.base_url}/open", headers=headers)
            open_resp.raise_for_status()
            open_data = open_resp.json()
            chat_id = self._extract_chat_id(open_data)
            if not chat_id:
                raise RuntimeError(f"MaxKB open failed: {open_data}")
            resp = await client.post(
                f"{self.base_url}/chat_message/{chat_id}",
                headers=headers,
                json={"message": question, "stream": False, "re_chat": False},
            )
            resp.raise_for_status()
            data = resp.json()
        return {"answer": self._extract_native_answer(data), "chat_id": chat_id, "raw": data}

    @staticmethod
    def _extract_openai_answer(data: Dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"].strip()
        except Exception:  # noqa: BLE001
            logger.warning("unexpected openai response: %s", data)
            return str(data)

    @staticmethod
    def _extract_chat_id(data: Dict[str, Any]) -> Optional[str]:
        if isinstance(data.get("data"), str):
            return data["data"]
        if isinstance(data.get("data"), dict):
            return data["data"].get("chat_id") or data["data"].get("id")
        return data.get("chat_id")

    @staticmethod
    def _extract_native_answer(data: Dict[str, Any]) -> str:
        payload = data.get("data", data)
        if isinstance(payload, dict):
            for key in ("content", "answer"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            content = payload.get("content")
            if isinstance(content, list):
                texts = [str(item.get("content", item)) for item in content]
                return "\n".join(texts).strip()
        return str(payload)
