"""密码哈希（PBKDF2-SHA256，标准库实现）与 JWT 签发/校验。"""
import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from .config import (
    JWT_ACCESS_EXPIRE_MINUTES,
    JWT_ALG,
    JWT_REFRESH_EXPIRE_MINUTES,
    JWT_SECRET,
    STEP_UP_TOKEN_MINUTES,
)

_PBKDF2_ROUNDS = 120_000

# 令牌类型（C1）：access 访问业务接口，refresh 只能换 access。
# 校验侧**必须**按类型区分——否则 refresh 令牌可以直接当 access 用，
# 「短 access」就完全失去意义。
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
# C10：step-up 令牌只用于「高敏操作二次校验」，5 分钟，且与 access 同源签发
# （同 `ver`），故登出/吊销对它同样有效。
TOKEN_TYPE_STEP_UP = "stepup"

#: 各类型的默认有效期（秒级换算在签发处做）。显式传 `expires_minutes` 优先，
# 但**不传时必须按类型取**——默认成 access 的时长会让 refresh/step-up 被
# 静默签成短命令牌（见 C1 的教训）。
_DEFAULT_MINUTES = {
    TOKEN_TYPE_ACCESS: JWT_ACCESS_EXPIRE_MINUTES,
    TOKEN_TYPE_REFRESH: JWT_REFRESH_EXPIRE_MINUTES,
    TOKEN_TYPE_STEP_UP: STEP_UP_TOKEN_MINUTES,
}


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


def create_token(
    user_id: int,
    token_version: int = 0,
    *,
    token_type: str = TOKEN_TYPE_ACCESS,
    expires_minutes: int | None = None,
) -> str:
    """签发令牌；`ver` 用于服务端吊销（比对 users.token_version）。

    @param token_type       `access`（默认）或 `refresh`
    @param expires_minutes  覆盖有效期（测试注入过期令牌用）
    """
    # 有效期**按类型取默认**，不能一律用 access 的：
    # 否则 `create_token(uid, ver, token_type=REFRESH)` 会静默签出一张 30 分钟的
    # refresh 令牌——会话 30 分钟即死，且从代码上看不出错（类型对、签名对，只是短）。
    # 这类"默认值陷阱"只有断言有效期能暴露。
    if expires_minutes is not None:
        minutes = expires_minutes
    else:
        minutes = _DEFAULT_MINUTES.get(token_type, JWT_ACCESS_EXPIRE_MINUTES)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "ver": int(token_version),
        "typ": token_type,
        # jti 保证同一秒内多次签发也各不相同（便于审计与去重）
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token_full(
    token: str, *, expect_type: str = TOKEN_TYPE_ACCESS
) -> tuple[int, int] | None:
    """返回 (user_id, token_version)；无效 / 过期 / **类型不符**返回 None。

    `typ` 缺失的历史令牌按 access 处理（迁移期兼容：它们最多再活到自身过期）。
    类型校验是双轨方案的安全前提：refresh 令牌若能被业务接口接受，
    攻击者拿到它就能当永久 access 用，「短 access」形同虚设。
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if payload.get("typ", TOKEN_TYPE_ACCESS) != expect_type:
            return None
        return int(payload["sub"]), int(payload.get("ver", 0))
    except Exception:
        return None


def decode_token(token: str) -> int | None:
    """返回 user_id；无效/过期返回 None（不含吊销校验）。"""
    full = decode_token_full(token)
    return full[0] if full else None