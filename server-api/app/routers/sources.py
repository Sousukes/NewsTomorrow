"""来源目录（E6b）：公开可读的 RSS 来源列表。

为什么需要它：前端要展示「我关注了谁」「可以关注哪些来源」，而
`GET /api/admin/sources` 需要管理员——用户侧不能复用。这里只暴露
`key / name / channel / newsCount` 四个字段：

- **不返回 `url`**：源地址属运营信息，公开出去等于给抓取滥用者一份清单；
- **不返回健康状态**（`last_status` 等）：那是排障信息，与用户无关；
- **只返回 `enabled=true`**：停用源不应出现在「可关注」列表里。

`newsCount` 用一次 `GROUP BY` 聚合取全部源的计数——**不做逐源查询**（N+1）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import News, RssSource
from ..response import ok

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=None)
def list_sources(
    channel: Optional[str] = Query(None, description="按频道过滤；未知频道返回空列表"),
    db: Session = Depends(get_db),
):
    """来源目录（公开）。`newsCount` 为该源**当前可见**资讯数（不含已下架）。"""
    q = db.query(RssSource.key, RssSource.name, RssSource.channel).filter(
        RssSource.enabled.is_(True)
    )
    if channel:
        q = q.filter(RssSource.channel == channel)
    rows = q.order_by(RssSource.channel, RssSource.name).all()

    # 一次聚合全部源的可见资讯数（源数量级是几十，比逐源 count 便宜一个数量级）
    counts = dict(
        db.query(News.source_key, func.count(News.id))
        .filter(News.source_key.isnot(None), News.hidden.is_(False))
        .group_by(News.source_key)
        .all()
    )

    return ok(
        {
            "list": [
                {
                    "key": key,
                    "name": name,
                    "channel": ch,
                    "newsCount": int(counts.get(key, 0)),
                }
                for key, name, ch in rows
            ]
        }
    )
