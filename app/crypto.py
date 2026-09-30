"""企业微信回调加解密（仅用于 URL 校验解锁「企业可信 IP」）。"""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
import struct
from typing import Optional


class WeComCryptoError(Exception):
    pass


class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str) -> None:
        self.token = token
        self.corp_id = corp_id
        key = encoding_aes_key + "="
        try:
            self.aes_key = base64.b64decode(key)
        except Exception as exc:  # noqa: BLE001
            raise WeComCryptoError(f"invalid EncodingAESKey: {exc}") from exc
        if len(self.aes_key) != 32:
            raise WeComCryptoError("EncodingAESKey must decode to 32 bytes")

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        self._check_signature(msg_signature, timestamp, nonce, echostr)
        return self.decrypt(echostr)

    def decrypt(self, encrypt: str) -> str:
        try:
            from Crypto.Cipher import AES

            cipher_bytes = base64.b64decode(encrypt)
            cipher = AES.new(self.aes_key, AES.MODE_CBC, self.aes_key[:16])
            plain = self._pkcs7_unpad(cipher.decrypt(cipher_bytes))
            content = plain[16:]
            msg_len = struct.unpack("!I", content[:4])[0]
            msg = content[4 : 4 + msg_len].decode("utf-8")
            from_id = content[4 + msg_len :].decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise WeComCryptoError(f"decrypt failed: {exc}") from exc
        if from_id != self.corp_id:
            raise WeComCryptoError("corp_id mismatch after decrypt")
        return msg

    def _check_signature(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> None:
        expected = self._signature(timestamp, nonce, encrypt)
        if expected != msg_signature:
            raise WeComCryptoError("invalid msg_signature")

    def _signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        items = sorted([self.token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

    @staticmethod
    def _pkcs7_unpad(data: bytes) -> bytes:
        amount = data[-1]
        if amount < 1 or amount > 32:
            raise WeComCryptoError("invalid pkcs7 padding")
        return data[:-amount]


def maybe_crypto(settings) -> Optional[WeComCrypto]:
    token = (getattr(settings, "wecom_callback_token", "") or "").strip()
    aes = (getattr(settings, "wecom_encoding_aes_key", "") or "").strip()
    corp = (getattr(settings, "wecom_corp_id", "") or "").strip()
    if not token or not aes or not corp or corp.startswith("wwxxxx"):
        return None
    return WeComCrypto(token, aes, corp)
