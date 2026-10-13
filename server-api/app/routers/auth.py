"""认证路由：注册 / 登录 / 注销。"""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User, now_str
from ..response import ok
from ..schemas import LoginIn, RegisterIn, TokenOut
from ..security import create_token, hash_password, verify_password
from .admin import is_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_payload(user: User) -> dict:
    return {
        "token": create_token(user.id, user.token_version or 0),
        # isAdmin 让前端直接决定是否展示管理入口，省掉一次探测请求；
        # 只下发布尔值，不下发白名单本身（避免把配置暴露给客户端）。
        "user": {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "isAdmin": is_admin(user.phone),
        },
    }


@router.post("/register", response_model=None)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if not re.fullmatch(r"1\d{10}", body.phone):
        raise HTTPException(status_code=400, detail={"code": 40001, "message": "请输入正确的 11 位手机号"})
    if db.query(User).filter(User.phone == body.phone).first():
        raise HTTPException(status_code=409, detail={"code": 40901, "message": "该手机号已注册，请直接登录"})
    user = User(
        phone=body.phone,
        password_hash=hash_password(body.password),
        name=body.name or "明日头条用户",
        created_at=now_str(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return ok(_token_payload(user), message="注册成功")


@router.post("/login", response_model=None)
def login(body: LoginIn, db: Session = Depends(get_db)):
    if not re.fullmatch(r"1\d{10}", body.phone):
        raise HTTPException(status_code=400, detail={"code": 40001, "message": "请输入正确的 11 位手机号"})
    user = db.query(User).filter(User.phone == body.phone).first()
    # 账号不存在与密码错误返回同一提示，避免账号枚举
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=401, detail={"code": 40101, "message": "手机号或密码错误，请重新输入"}
        )
    return ok(_token_payload(user), message="登录成功")


@router.post("/logout", response_model=None)
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """退出：自增 token 版本，使该用户**所有**已签发 token 立即失效（服务端吊销）。"""
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return ok(None, message="已退出登录")


@router.post("/refresh", response_model=None)
def refresh(user: User = Depends(get_current_user)):
    """滑动续期：用当前有效 token 换取一张新 token。

    不改变 `token_version`，因此旧 token 在过期前仍可用（标准 access-token 续期语义）。
    """
    return ok(_token_payload(user), message="已续期")