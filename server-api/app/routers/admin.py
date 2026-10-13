"""管理端路由：RSS 源配置（可运行时增删启停）+ 管理员白名单（P1-14）。

鉴权与 `POST /api/news/refresh` 一致：需登录且具备管理能力——
`ADMIN_PHONES` 环境变量 **∪** `admin_users` 表白名单（P1-14 起）；
两边都为空表示该能力不可用（403）——避免生产环境因配置疏漏而暴露管理接口。

设计要点：
- **读取即播种**：列表/抓取前确保内置源已入库，新环境无需手工初始化；
- **停用而非删除**为默认建议，但删除能力保留（运营确实加错了源时需要）；
- **URL 协议白名单**：仅允许 http/https，防止注入 `file://` 等被抓取器请求；
- 健康字段由抓取流程回写，本模块只读不写；
- **末位管理员保护**：移除后若任何来源都不再有管理员，拒绝（40904）——
  否则生产环境可能把自己锁在门外，只能改环境变量重启来恢复。
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from urllib.parse import urlparse

from ..config import ADMIN_PHONES
from ..database import get_db
from ..deps import get_current_user
from ..feeds.sources import BUILTIN_SOURCES, ensure_seeded
from ..models import AdminAuditLog, AdminUser, RssSource, User, now_str
from ..response import ok
from ..schemas import AdminIn, SourceIn, SourceUpdateIn

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger("toutiao.admin")

ALLOWED_SCHEMES = ("http", "https")

# 审计动作：`对象.动作` 命名，前端据此映射中文标签（无需再维护一份枚举表）
AUDIT_SOURCE_CREATE = "source.create"
AUDIT_SOURCE_UPDATE = "source.update"
AUDIT_SOURCE_DELETE = "source.delete"
AUDIT_SOURCE_RESET = "source.reset"
AUDIT_ADMIN_ADD = "admin.add"
AUDIT_ADMIN_REMOVE = "admin.remove"


def is_admin(phone: str | None, db: Session | None = None) -> bool:
    """是否具备管理能力。

    **双源并集**（P1-14）：
    - `ADMIN_PHONES` 环境变量保留为 break-glass——改配置重启即生效，不依赖库；
    - `admin_users` 表供日常运营——应用内增删，无需重启服务；
    - 两边都为空 = 整体关闭：配置疏漏时不应暴露管理接口（生产安全）。

    抽成独立函数而非在调用处各写一遍：登录响应也要复用同一判定
    （前端据此决定是否展示管理入口），两处实现一旦漂移，就会出现
    "入口看得见、一点就 403"或反之的怪象。
    """
    if not phone:
        return False
    if ADMIN_PHONES and phone in ADMIN_PHONES:
        return True
    return db is not None and db.get(AdminUser, phone) is not None


def _require_admin(user: User, db: Session) -> None:
    if not is_admin(user.phone, db):
        raise HTTPException(status_code=403, detail={"code": 40301, "message": "无权限执行该操作"})


def record_audit(db: Session, actor: str, action: str, target: str = "", detail=None) -> None:
    """写一条审计（**不提交**，由调用方与业务变更一起 commit）。

    刻意不做 try/except 静默：「改成功了但没留痕」比「改失败」更糟——
    前者会让事后排查误以为没人动过。同事务还能保证两者要么都成、要么都不成。
    """
    db.add(
        AdminAuditLog(
            actor_phone=actor,
            action=action,
            target=target or "",
            detail=json.dumps(detail or {}, ensure_ascii=False, default=str),
            created_at=now_str(),
        )
    )


def _validate_url(url: str) -> None:
    """仅允许 http/https：抓取器会真的去请求这个地址，必须挡住 file:// 等协议。"""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise HTTPException(
            status_code=400, detail={"code": 40004, "message": "源地址必须为 http/https URL"}
        )


def _out(row: RssSource) -> dict:
    return {
        "key": row.key,
        "name": row.name,
        "channel": row.channel,
        "url": row.url,
        "weight": row.weight,
        "enabled": bool(row.enabled),
        "lastStatus": row.last_status,
        "lastError": row.last_error,
        "lastItemCount": row.last_item_count,
        "lastFetchedAt": row.last_fetched_at,
    }


@router.get("/sources", response_model=None)
def list_sources(
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """源列表（含抓取健康状态）。"""
    _require_admin(user, db)
    ensure_seeded(db)
    rows = db.query(RssSource).order_by(RssSource.weight.desc(), RssSource.key).all()
    return ok({"list": [_out(r) for r in rows], "total": len(rows)})


@router.post("/sources", response_model=None)
def create_source(
    body: SourceIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新增源。key 冲突返回 409。"""
    _require_admin(user, db)
    _validate_url(body.url)
    ensure_seeded(db)
    if db.get(RssSource, body.key) is not None:
        # 40902 而非 40901：40901 已表示「手机号已注册」，同一码两义会让前端文案错配
        raise HTTPException(
            status_code=409, detail={"code": 40902, "message": "该源标识已存在"}
        )
    now = now_str()
    row = RssSource(
        key=body.key,
        name=body.name,
        channel=body.channel,
        url=body.url,
        weight=body.weight,
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    record_audit(
        db,
        user.phone,
        AUDIT_SOURCE_CREATE,
        body.key,
        {"snapshot": _out(row)},
    )
    db.commit()
    db.refresh(row)
    return ok(_out(row), message="已新增源")


@router.patch("/sources/{key}", response_model=None)
def update_source(
    key: str,
    body: SourceUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """局部更新源（改权重 / 启停 / 换地址）。"""
    _require_admin(user, db)
    # 先确保内置源已入库：否则在尚未触发过抓取的新环境里，改一个"明明存在"的内置源会 404
    ensure_seeded(db)
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    if body.url is not None:
        _validate_url(body.url)
    before = _out(row)
    for field in ("name", "channel", "url", "weight", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    row.updated_at = now_str()
    after = _out(row)
    # 只记真正变化的字段：无变化的 PATCH（前端常因表单整体提交而触发）不该灌进时间线
    changes = {k: [before[k], after[k]] for k in ("name", "channel", "url", "weight", "enabled") if before[k] != after[k]}
    if changes:
        record_audit(db, user.phone, AUDIT_SOURCE_UPDATE, key, {"changes": changes})
    db.commit()
    db.refresh(row)
    return ok(_out(row), message="已更新源")


@router.delete("/sources/{key}", response_model=None)
def delete_source(
    key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """删除源。删除后若表空了，下次读取会重新播种内置源。"""
    _require_admin(user, db)
    ensure_seeded(db)  # 同上：避免删除内置源时因尚未播种而 404
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    # 删除后行就没了，快照必须在 delete 之前取
    record_audit(db, user.phone, AUDIT_SOURCE_DELETE, key, {"snapshot": _out(row)})
    db.delete(row)
    db.commit()
    return ok({"key": key}, message="已删除源")


@router.post("/sources/reset", response_model=None)
def reset_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """重置为内置源：删除库中全部源并重新播种。

    用于把运营改坏的配置恢复出厂。会丢弃自定义源，故单独一个端点而非复用删除。
    """
    _require_admin(user, db)
    db.query(RssSource).delete()
    db.commit()
    seeded = ensure_seeded(db)
    record_audit(db, user.phone, AUDIT_SOURCE_RESET, "", {"seeded": seeded})
    db.commit()
    logger.info("管理员 %s 重置 RSS 源为内置 %d 个", user.phone, seeded)
    return ok({"seeded": seeded}, message=f"已重置为 {seeded} 个内置源")


# ---------------------------------------------------------------- 管理员白名单（P1-14）


def _admin_out(row: AdminUser, db: Session) -> dict:
    """白名单行 → 输出。`registered` 区分「已注册账号 / 预授权」供界面展示。"""
    # 按 phone 查而非 db.get(User, phone)：User 主键是自增 id，
    # 拿手机号当主键查会永远查不到（registered 恒为 False）。
    account = db.query(User).filter(User.phone == row.phone).first()
    return {
        "phone": row.phone,
        "note": row.note or "",
        "addedBy": row.added_by or "",
        "createdAt": row.created_at,
        "registered": account is not None,
        "name": account.name if account else "",
    }


@router.get("/users", response_model=None)
def list_admin_users(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """管理员白名单列表（DB 源）。

    附 `envAdminCount`（环境变量来源的管理员**数量**，不下发号码）：
    界面据此解释「为什么移除最后一位库管理员仍被允许」——env 是 break-glass。
    """
    _require_admin(user, db)
    rows = db.query(AdminUser).order_by(AdminUser.created_at, AdminUser.phone).all()
    return ok(
        {
            "list": [_admin_out(r, db) for r in rows],
            "total": len(rows),
            "envAdminCount": len(ADMIN_PHONES),
        }
    )


@router.post("/users", response_model=None)
def add_admin_user(
    body: AdminIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """添加管理员。

    已在**任一来源**（env 或库）中的手机号返回 40903——重复入库只会让
    界面出现一行"管不了其实也无需管"的冗余记录。
    """
    _require_admin(user, db)
    if db.get(AdminUser, body.phone) is not None or (
        ADMIN_PHONES and body.phone in ADMIN_PHONES
    ):
        raise HTTPException(status_code=409, detail={"code": 40903, "message": "该手机号已是管理员"})
    row = AdminUser(
        phone=body.phone,
        note=body.note.strip(),
        added_by=user.phone,
        created_at=now_str(),
    )
    db.add(row)
    record_audit(db, user.phone, AUDIT_ADMIN_ADD, body.phone, {"note": row.note or ""})
    db.commit()
    db.refresh(row)
    logger.info("管理员 %s 添加管理员 %s（备注：%s）", user.phone, body.phone, row.note or "无")
    return ok(_admin_out(row, db), message="已添加管理员")


@router.delete("/users/{phone}", response_model=None)
def remove_admin_user(
    phone: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """移除管理员。

    **末位保护**：移除后若任何来源都不再有任何管理员，拒绝（40904）。
    注意 env 管理员不受库操作影响——移除一位"同时也在 env 里"的管理员时，
    env 仍在授权，故只有 env 为空且这是库中最后一条时才拦截。
    """
    _require_admin(user, db)
    row = db.get(AdminUser, phone)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40403, "message": "管理员不存在"})
    if db.query(AdminUser).count() - 1 <= 0 and not ADMIN_PHONES:
        raise HTTPException(
            status_code=409, detail={"code": 40904, "message": "已是最后一位管理员，不能移除"}
        )
    record_audit(db, user.phone, AUDIT_ADMIN_REMOVE, phone, {"note": row.note or ""})
    db.delete(row)
    db.commit()
    logger.info("管理员 %s 移除管理员 %s", user.phone, phone)
    return ok({"phone": phone}, message="已移除管理员")


# ---------------------------------------------------------------- 运营审计（P1-15）


def _audit_out(row: AdminAuditLog) -> dict:
    """detail 存的是 JSON 串；解析失败降级为空对象（宁可少显示，也不要整页报错）。"""
    try:
        detail = json.loads(row.detail) if row.detail else {}
        if not isinstance(detail, dict):
            detail = {}
    except (ValueError, TypeError):
        detail = {}
    return {
        "id": row.id,
        "actorPhone": row.actor_phone,
        "action": row.action,
        "target": row.target or "",
        "detail": detail,
        "createdAt": row.created_at,
    }


@router.get("/audit", response_model=None)
def list_audit(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="上一页末条的 id"),
    target: str | None = Query(None, description="按对象筛选（源 key / 手机号）"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """审计时间线（新的在前，游标分页）。

    游标用**自增 id** 而非时间戳：created_at 是秒级精度，同秒多条会让 keyset 比较
    出现重复/遗漏（评论分页踩过同一个坑）。
    """
    _require_admin(user, db)
    q = db.query(AdminAuditLog)
    if target:
        q = q.filter(AdminAuditLog.target == target)
    if cursor:
        if not cursor.isdigit():
            raise HTTPException(status_code=400, detail={"code": 40003, "message": "分页游标无效"})
        q = q.filter(AdminAuditLog.id < int(cursor))
    rows = q.order_by(AdminAuditLog.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return ok(
        {
            "list": [_audit_out(r) for r in items],
            "hasMore": has_more,
            "nextCursor": str(items[-1].id) if has_more and items else None,
        }
    )
