import base64
import hashlib
import hmac
import struct
from typing import Optional

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WXBizMsgCrypt:
    """企业微信 微信客服 消息加解密工具包"""

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str = ""):
        self.token = token
        self.receive_id = receive_id
        # Key = Base64_Decode(EncodingAESKey + "=")
        self.key = base64.b64decode(encoding_aes_key + "=")
        self.iv = self.key[:16]

    def verify_signature(self, signature: str, timestamp: str, nonce: str, echostr_or_msg: str) -> bool:
        if not signature:
            return False
        items = sorted([self.token, timestamp, nonce, echostr_or_msg])
        digest = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
        # 常量时间比较，避免时序侧信道泄露
        return hmac.compare_digest(digest, signature)

    def _decrypt(self, encrypted_text: str) -> bytes:
        """AES-CBC 解密 + PKCS#7 去填充，返回原始明文 bytes"""
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv), backend=default_backend())
        decryptor = cipher.decryptor()
        encrypted_bytes = base64.b64decode(encrypted_text)
        decrypted_bytes = decryptor.update(encrypted_bytes) + decryptor.finalize()

        # PKCS#7 unpad
        pad_len = decrypted_bytes[-1]
        if 1 <= pad_len <= 32:
            decrypted_bytes = decrypted_bytes[:-pad_len]
        return decrypted_bytes

    def _extract_msg(self, decrypted_bytes: bytes) -> str:
        """解析明文：16 字节随机 + 4 字节 msg_len + msg + receive_id，并校验 receive_id"""
        content_len = struct.unpack(">I", decrypted_bytes[16:20])[0]
        msg = decrypted_bytes[20 : 20 + content_len].decode("utf-8")
        # 尾部 receive_id（企微为 corp_id）校验；未配置 receive_id 时跳过以保持向后兼容
        actual_receive_id = decrypted_bytes[20 + content_len :].decode("utf-8", errors="ignore")
        if self.receive_id and actual_receive_id != self.receive_id:
            raise ValueError(
                f"receive_id mismatch: expected {self.receive_id!r}, got {actual_receive_id!r}"
            )
        return msg

    def decrypt_echo_str(self, signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        if not self.verify_signature(signature, timestamp, nonce, echostr):
            raise ValueError("Invalid signature for WeChat Callback verification")
        return self._extract_msg(self._decrypt(echostr))

    def decrypt_msg(self, signature: str, timestamp: str, nonce: str, encrypted_msg: str) -> str:
        if not self.verify_signature(signature, timestamp, nonce, encrypted_msg):
            raise ValueError("Invalid signature for encrypted message")
        return self._extract_msg(self._decrypt(encrypted_msg))
