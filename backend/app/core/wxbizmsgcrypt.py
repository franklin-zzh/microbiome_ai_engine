import base64
import hashlib
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
        items = sorted([self.token, timestamp, nonce, echostr_or_msg])
        sha1 = hashlib.sha1()
        sha1.update("".join(items).encode("utf-8"))
        digest = sha1.hexdigest()
        return digest == signature

    def decrypt_echo_str(self, signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        if not self.verify_signature(signature, timestamp, nonce, echostr):
            raise ValueError("Invalid signature for WeChat Callback verification")

        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv), backend=default_backend())
        decryptor = cipher.decryptor()
        encrypted_bytes = base64.b64decode(echostr)
        decrypted_bytes = decryptor.update(encrypted_bytes) + decryptor.finalize()

        # PKCS#7 unpad
        pad_len = decrypted_bytes[-1]
        if 1 <= pad_len <= 32:
            decrypted_bytes = decrypted_bytes[:-pad_len]

        # Extract content: 16-byte random + 4-byte msg_len + msg + receive_id
        content_len = struct.unpack(">I", decrypted_bytes[16:20])[0]
        msg = decrypted_bytes[20 : 20 + content_len].decode("utf-8")
        return msg

    def decrypt_msg(self, signature: str, timestamp: str, nonce: str, encrypted_msg: str) -> str:
        if not self.verify_signature(signature, timestamp, nonce, encrypted_msg):
            raise ValueError("Invalid signature for encrypted message")

        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv), backend=default_backend())
        decryptor = cipher.decryptor()
        encrypted_bytes = base64.b64decode(encrypted_msg)
        decrypted_bytes = decryptor.update(encrypted_bytes) + decryptor.finalize()

        # PKCS#7 unpad
        pad_len = decrypted_bytes[-1]
        if 1 <= pad_len <= 32:
            decrypted_bytes = decrypted_bytes[:-pad_len]

        # Extract content: 16-byte random + 4-byte msg_len + msg + receive_id
        content_len = struct.unpack(">I", decrypted_bytes[16:20])[0]
        msg = decrypted_bytes[20 : 20 + content_len].decode("utf-8")
        return msg
