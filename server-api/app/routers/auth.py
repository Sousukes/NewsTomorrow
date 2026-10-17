"""认证路由：注册 / 登录 / 注销。

注册时的昵称（P1-24）与「个人中心改昵称」走**同一条校验链**（形状 + 敏感词）：
两套规则必然漂移，而用户分不清自己的名字是从哪个入口设的——漂移的方向
永远是「注册放过、改名拦住」，用户侧表现为「同一个名字有时能设有时不能」。
"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..login_guard import lock_detail, record_failure, record_success
from ..models import User, now_str
from ..nickname_moderation import (
    RESULT_BLOCK,
    RESULT_REVIEW,
    engine,
    record_hits,
    validate_nickname_shape,
)
from ..response import ok
from ..schemas import LoginIn, RegisterIn, TokenOut
from ..security import create_token, hash_password, verify_password
from .admin import is_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("toutiao.auth")


def _token_payload(user: User, db: Session) -> dict:
    return {
        "token": create_token(user.id, user.token_version or 0),
        # isAdmin 让前端直接决定是否展示管理入口，省掉一次探测请求；
        # 只下发布尔值，不下发白名单本身（避免把配置暴露给客户端）。
        # 双源判定（env ∪ admin_users 表）需要查库，故 db 必须透传到这里。
        "user": {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "isAdmin": is_admin(user.phone, db),
            # 昵称待人工复核（P1-24）：让个人中心在下次打开时仍能显示"审核中"——
            # 否则这个标志只有管理员看得见，用户侧只剩保存那一刻的一次性提示，
            # 刷新即失忆（写而不读的状态，迟早被当成没用的字段删掉）。
            "namePendingReview": bool(user.name_pending_review),
        },
    }


@router.post("/register", response_model=None)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if not re.fullmatch(r"1\d{10}", body.phone):
        raise HTTPException(status_code=400, detail={"code": 40001, "message": "请输入正确的 11 位手机号"})
    if db.query(User).filter(User.phone == body.phone).first():
        raise HTTPException(status_code=409, detail={"code": 40901, "message": "该手机号已注册，请直接登录"})
    # 昵称是**可选**字段：None / 空 / 纯空白一律走默认名——默认名是我们自己的，
    # 不是用户起的，不过敏感词校验。一旦提供了非空白值，就与「个人中心改昵称」
    # 走同一条链（形状 42203 / 拦截 40006 / 待复核置位）。
    clean = (body.name or "").strip()
    verdict = None
    if clean:
        valid, msg = validate_nickname_shape(clean)
        if not valid:
            raise HTTPException(status_code=422, detail={"code": 42203, "message": msg})
        verdict = engine.check(db, clean)
        if verdict.result == RESULT_BLOCK:
            # 拦注册而不是悄悄换成默认名：静默替换会让用户以为名字注册成功了，
            # 事后在个人中心才发现「名字怎么变了」——更糟。手机号此时尚未落库，
            # 换个昵称可以立即重试。被拦的尝试同样留痕（账号还没有 id，记 NULL）。
            record_hits(
                db, verdict.hits, user_id=None, user_name="", source="register", name=clean
            )
            db.commit()
            raise HTTPException(
                status_code=400, detail={"code": 40006, "message": verdict.message}
            )
    user = User(
        phone=body.phone,
        password_hash=hash_password(body.password),
        name=clean or "明日头条用户",
        created_at=now_str(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if verdict is not None and verdict.result == RESULT_REVIEW:
        user.name_pending_review = True
        # 落库后才有 user.id：命中日志要能按人定位
        record_hits(
            db, verdict.hits, user_id=user.id, user_name=user.name, source="register", name=clean
        )
        db.commit()
        db.refresh(user)
    return ok(_token_payload(user, db), message="注册成功")


@router.post("/login", response_model=None)
def login(body: LoginIn, db: Session = Depends(get_db)):
    if not re.fullmatch(r"1\d{10}", body.phone):
        raise HTTPException(status_code=400, detail={"code": 40001, "message": "请输入正确的 11 位手机号"})
    # 账号维度锁定（C2）：IP 限流防不住「换 IP 打同一个账号」。锁定判定
    # 先于查库与验密——已锁账号连哈希都不算（成本趋零），且继续尝试
    # 不会给锁定期续期。不存在的账号同样计数：否则「同号有时 429
    # 有时 401」就是账号枚举 oracle。
    locked = lock_detail(body.phone)
    if locked is not None:
        logger.warning("登录锁定生效 phone=%s", body.phone)
        raise HTTPException(
            status_code=429,
            detail={"code": 42902, "message": locked["message"]},
            headers={"Retry-After": str(locked["retry_after"])},
        )
    user = db.query(User).filter(User.phone == body.phone).first()
    # 账号不存在与密码错误返回同一提示，避免账号枚举
    if user is None or not verify_password(body.password, user.password_hash):
        if record_failure(body.phone):
            logger.warning("登录失败达上限，账号进入锁定 phone=%s", body.phone)
        raise HTTPException(
            status_code=401, detail={"code": 40101, "message": "手机号或密码错误，请重新输入"}
        )
    # 成功即清零：失败计数只反映「连续」失败，不让历史手滑累积成锁定
    record_success(body.phone)
    return ok(_token_payload(user, db), message="登录成功")


@router.post("/logout", response_model=None)
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """退出：自增 token 版本，使该用户**所有**已签发 token 立即失效（服务端吊销）。"""
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return ok(None, message="已退出登录")


@router.post("/refresh", response_model=None)
def refresh(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """滑动续期：用当前有效 token 换取一张新 token。

    不改变 `token_version`，因此旧 token 在过期前仍可用（标准 access-token 续期语义）。
    isAdmin 随续期重新判定（P1-14 起白名单可在应用内变动，不能把标志写死在 token 里）。
    """
    return ok(_token_payload(user, db), message="已续期")