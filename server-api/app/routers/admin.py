"""管理端路由：RSS 源配置（可运行时增删启停）。

鉴权与 `POST /api/news/refresh` 一致：需登录且手机号在 `ADMIN_PHONES` 白名单内；
白名单为空表示该能力不可用（403）——避免生产环境因配置疏漏而暴露管理接口。

设计要点：
- **读取即播种**：列表/抓取前确保内置源已入库，新环境无需手工初始化；
- **停用而非删除**为默认建议，但删除能力保留（运营确实加错了源时需要）；
- **URL 协议白名单**：仅允许 http/https，防止注入 `file://` 等被抓取器请求；
- 健康字段由抓取流程回写，本模块只读不写。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from urllib.parse import urlparse

from ..config import ADMIN_PHONES
from ..database import get_db
from ..deps import get_current_user
from ..feeds.sources import BUILTIN_SOURCES, ensure_seeded
from ..models import RssSource, User, now_str
from ..response import ok
from ..schemas import SourceIn, SourceUpdateIn

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger("toutiao.admin")

ALLOWED_SCHEMES = ("http", "https")


def is_admin(phone: str | None) -> bool:
    """是否具备管理能力。

    **白名单为空 = 整体关闭**：配置疏漏时不应暴露管理接口（生产安全）。

    抽成独立函数而非在调用处各写一遍 `phone in ADMIN_PHONES`：登录响应也要复用同一判定
    （前端据此决定是否展示管理入口），两处实现一旦漂移，就会出现"入口看得见、
    一点就 403"或反之的怪象。
    """
    return bool(ADMIN_PHONES) and phone in ADMIN_PHONES


def _require_admin(user: User) -> None:
    if not is_admin(user.phone):
        raise HTTPException(status_code=403, detail={"code": 40301, "message": "无权限执行该操作"})


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
    _require_admin(user)
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
    _require_admin(user)
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
    _require_admin(user)
    # 先确保内置源已入库：否则在尚未触发过抓取的新环境里，改一个"明明存在"的内置源会 404
    ensure_seeded(db)
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    if body.url is not None:
        _validate_url(body.url)
    for field in ("name", "channel", "url", "weight", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    row.updated_at = now_str()
    db.commit()
    db.refresh(row)
    return ok(_out(row), message="已更新源")


@router.delete("/sources/{key}", response_model=None)
def delete_source(
    key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """删除源。删除后若表空了，下次读取会重新播种内置源。"""
    _require_admin(user)
    ensure_seeded(db)  # 同上：避免删除内置源时因尚未播种而 404
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    db.delete(row)
    db.commit()
    return ok({"key": key}, message="已删除源")


@router.post("/sources/reset", response_model=None)
def reset_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """重置为内置源：删除库中全部源并重新播种。

    用于把运营改坏的配置恢复出厂。会丢弃自定义源，故单独一个端点而非复用删除。
    """
    _require_admin(user)
    db.query(RssSource).delete()
    db.commit()
    seeded = ensure_seeded(db)
    logger.info("管理员 %s 重置 RSS 源为内置 %d 个", user.phone, seeded)
    return ok({"seeded": seeded}, message=f"已重置为 {seeded} 个内置源")
