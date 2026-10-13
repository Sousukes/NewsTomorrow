"""密码哈希（PBKDF2-SHA256，标准库实现）与 JWT 签发/校验。"""
import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from .config import JWT_ALG, JWT_EXPIRE_MINUTES, JWT_SECRET

_PBKDF2_ROUNDS = 120_000


def hash_password(password: str) -> str:
    """格式：pbkdf2_sha256$rounds$salt_hex$hash_hex"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def create_token(user_id: int, token_version: int = 0) -> str:
    """签发访问令牌；`ver` 用于服务端吊销（比对 users.token_version）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "ver": int(token_version),
        # jti 保证同一秒内多次签发也各不相同（便于审计与去重）
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token_full(token: str) -> tuple[int, int] | None:
    """返回 (user_id, token_version)；无效/过期返回 None。"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return int(payload["sub"]), int(payload.get("ver", 0))
    except Exception:
        return None


def decode_token(token: str) -> int | None:
    """返回 user_id；无效/过期返回 None（不含吊销校验）。"""
    full = decode_token_full(token)
    return full[0] if full else None