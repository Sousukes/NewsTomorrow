"""鉴权依赖：解析 Authorization: Bearer <token>。"""
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_token_full


def _user_from_token(authorization: Optional[str], db: Session) -> Optional[User]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    decoded = decode_token_full(parts[1])
    if decoded is None:
        return None
    user_id, token_version = decoded
    user = db.get(User, user_id)
    # 吊销校验：token 携带的 ver 必须与库中一致（/logout 自增版本即失效全部旧 token）
    if user is None or (user.token_version or 0) != token_version:
        return None
    return user


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """未登录返回 None（用于公开接口按当前用户计算 liked）。"""
    return _user_from_token(authorization, db)


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    user = _user_from_token(authorization, db)
    if user is None:
        raise HTTPException(
            status_code=401, detail={"code": 40100, "message": "未登录或登录已过期"}
        )
    return user