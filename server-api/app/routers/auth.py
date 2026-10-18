"""认证路由：注册 / 登录 / 注销。

注册时的昵称（P1-24）与「个人中心改昵称」走**同一条校验链**（形状 + 敏感词）：
两套规则必然漂移，而用户分不清自己的名字是从哪个入口设的——漂移的方向
永远是「注册放过、改名拦住」，用户侧表现为「同一个名字有时能设有时不能」。
"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import JWT_ACCESS_EXPIRE_MINUTES, JWT_REFRESH_EXPIRE_MINUTES
from ..database import get_db
from ..deps import get_current_user, get_current_user_optional
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
from ..schemas import LoginIn, PasswordChangeIn, RefreshIn, RegisterIn, StepUpIn, TokenOut
from ..security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    create_token,
    decode_token_full,
    hash_password,
    verify_password,
)
from ..step_up import issue_step_up_token, require_step_up
from .admin import is_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("toutiao.auth")


def _token_payload(user: User, db: Session) -> dict:
    """签发**双令牌**（C1）。

    - `token`（access）：短（默认 30 分钟），用于业务接口。字段名沿用 `token`，
      老客户端 / 契约脚本无需改动；
    - `refreshToken`：长（默认 7 天），**只能**调 `/auth/refresh` 换新 access，
      直接访问业务接口会被 `get_current_user` 拒绝（typ 校验）；
    - `expiresIn`：access 剩余秒数，前端据此提前续期（而不是等 401）。

    吊销：两者都带 `ver`，`/logout` 自增 `token_version` 会让**新旧两类令牌同时失效**。
    """
    ver = user.token_version or 0
    return {
        "token": create_token(
            user.id,
            ver,
            token_type=TOKEN_TYPE_ACCESS,
            expires_minutes=JWT_ACCESS_EXPIRE_MINUTES,
        ),
        "refreshToken": create_token(
            user.id,
            ver,
            token_type=TOKEN_TYPE_REFRESH,
            expires_minutes=JWT_REFRESH_EXPIRE_MINUTES,
        ),
        "expiresIn": JWT_ACCESS_EXPIRE_MINUTES * 60,
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
            # C4：管理员刚代其重置过密码 → 前端强制跳改密页，
            # 并禁用发评论/收藏等动作。放在登录响应里是因为**登录是唯一**
            # 能让用户立刻知道这件事的时机。
            "mustChangePassword": bool(user.must_change_password),
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


@router.put("/password", response_model=None)
def change_password(
    body: PasswordChangeIn,
    user: User = Depends(get_current_user),
    # 改密是「换掉最重要的那把钥匙」，必须比普通写操作多一道证明：
    # 令牌被偷时，攻击者拿临时密码或会话就能把账号永久占住。
    step: User = Depends(require_step_up),
    db: Session = Depends(get_db),
):
    """自助改密（C4）。

    两道证明都要过：`require_step_up`（持令牌者再验一次密码）+ `oldPassword`。
    单靠 step-up 不够——用户被诱导弹窗时输入的仍是他自己的密码，
    而"旧密码错误"这道校验能挡住其中一部分。

    成功后**自增 token_version**：改密的语义就是"作废旧的一切凭证"，
    其他设备上的 access/refresh 立即失效（与 /logout 同款做法）。
    调用方手上的令牌也因此失效，所以直接下发**新的一对**——否则用户改完密
    会被自己踢下线，那体验比不改还糟。
    """
    if not verify_password(body.oldPassword, user.password_hash):
        raise HTTPException(
            status_code=401, detail={"code": 40101, "message": "原密码不正确"}
        )
    if verify_password(body.newPassword, user.password_hash):
        raise HTTPException(
            status_code=422, detail={"code": 42205, "message": "新密码不能与原密码相同"}
        )
    user.password_hash = hash_password(body.newPassword)
    # 解除受限态（C4）：这一步是「强制改密」的终点，改完即恢复正常
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.refresh(user)
    logger.info("用户 %s 修改了密码（受限态解除=%s）", user.phone, not user.must_change_password)
    return ok(_token_payload(user, db), message="密码已更新")


@router.post("/logout", response_model=None)
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """退出：自增 token 版本，使该用户**所有**已签发 token 立即失效（服务端吊销）。"""
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return ok(None, message="已退出登录")


def _user_from_refresh_token(token: str, db: Session) -> User | None:
    """用 refresh 令牌定位用户（C1）。

    与 `deps._user_from_token` 同构，唯一差别是 `expect_type=TOKEN_TYPE_REFRESH`：
    **类型不符一律拒绝**，否则 refresh 令牌就能当 access 用，「短 access」形同虚设。
    """
    decoded = decode_token_full(token, expect_type=TOKEN_TYPE_REFRESH)
    if decoded is None:
        return None
    user_id, token_version = decoded
    user = db.get(User, user_id)
    # 吊销校验与 access 完全一致：/logout 自增 token_version → 两类令牌同时失效
    if user is None or (user.token_version or 0) != token_version:
        return None
    return user


@router.post("/step-up", response_model=None)
def step_up(
    body: StepUpIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """高敏操作二次校验（C10）：验密码 → 换一张 5 分钟的 step-up 令牌。

    前置是**已登录**（access 有效）：身份由令牌给出，body 里不再收手机号——
    否则这个端点就成了「可指定账号的登录端点」，猜密码的人顺带获得账号枚举。

    复用登录的账号维度锁定（scope=`stepup`）：二次校验若不做防爆破，
    它本身就是密码 oracle——猜对一次即可删源 / 授权管理员。
    """
    locked = lock_detail(user.phone, scope="stepup")
    if locked is not None:
        logger.warning("二次校验锁定生效 phone=%s", user.phone)
        raise HTTPException(
            status_code=429,
            detail={"code": 42902, "message": locked["message"]},
            headers={"Retry-After": str(locked["retry_after"])},
        )
    if not verify_password(body.password, user.password_hash):
        if record_failure(user.phone, scope="stepup"):
            logger.warning("二次校验失败达上限，账号进入锁定 phone=%s", user.phone)
        # 与登录同文案：不提示「密码错」以外的任何信息
        raise HTTPException(
            status_code=401, detail={"code": 40101, "message": "手机号或密码错误，请重新输入"}
        )
    record_success(user.phone, scope="stepup")
    return ok(issue_step_up_token(user), message="二次校验通过")


@router.post("/refresh", response_model=None)
def refresh(
    body: RefreshIn | None = None,
    current: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """续期（C1）：换新 access。

    两条入口，**优先 refresh 令牌**：

    1. `body.refreshToken`（长令牌，7 天）：access 短（默认 30 分钟）之后，
       用户不能靠已过期的 access 续期，必须走这条；
    2. 缺省时回退到 `Authorization` 里的 access 滑动续期（旧客户端 / 契约脚本
        / 既有测试的路径，升级期不打断）。

    refresh 令牌无效时**不回退**到 access：那会把「refresh 已失效」掩盖成
    「还能续」，用户在 7 天后被静默续期下去，与设定保留期相悖。

    不改变 `token_version`，因此旧 access 在过期前仍可用（标准续期语义）。
    isAdmin 随续期重新判定（P1-14 起白名单可在应用内变动，不能把标志写死在令牌里）。
    """
    refresh_token = ((body.refreshToken if body else "") or "").strip()
    user = _user_from_refresh_token(refresh_token, db) if refresh_token else current
    if user is None:
        raise HTTPException(
            status_code=401, detail={"code": 40100, "message": "未登录或登录已过期"}
        )
    return ok(_token_payload(user, db), message="已续期")