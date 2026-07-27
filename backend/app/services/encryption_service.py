"""凭证加密服务 — AES-256-GCM 认证加密

使用方式:
    key = derive_key(settings.credential_key)
    ciphertext = encrypt_secret(plaintext, key)
    plaintext = decrypt_secret(ciphertext, key)
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def derive_key(credential_key: str) -> bytes:
    """从配置的 credential_key 派生 256 位 AES 密钥（PBKDF2）"""
    salt = b"wechat-ai-platform-2024"
    return hashlib.pbkdf2_hmac("sha256", credential_key.encode("utf-8"), salt, 600000, dklen=32)


def encrypt_secret(plaintext: str, key: bytes) -> str:
    """加密明文，返回 base64 编码的密文（nonce + ciphertext + tag）"""
    if not plaintext:
        return ""
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # 格式: nonce (12 bytes) + ciphertext
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(ciphertext_b64: str, key: bytes) -> str:
    """解密 base64 编码的密文，返回明文"""
    if not ciphertext_b64:
        return ""
    try:
        raw = base64.b64decode(ciphertext_b64)
        nonce = raw[:12]
        ciphertext = raw[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception as exc:
        raise ValueError("Failed to decrypt credential secret") from exc
