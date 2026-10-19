"""pg_trgm 扩展的可用性探测。

`pg_trgm` 提供 `similarity()` 与 GIN 三元组索引，是搜索相关度与入库判重的
共同前提。迁移 0005 会在 PostgreSQL 上创建它，但：

- **权限不足时会跳过**创建；
- SQLite 等方言根本没有这个扩展。

因此必须**运行时探测**，不能无条件用 `func.similarity()`——否则在 SQLite 上
会直接报"函数不存在"。

放在中立模块而不是 `routers/` 或 `feeds/` 下，是因为这两层都要用它：
`routers/news.py`（搜索相关度排序）与 `feeds/ingest.py`（入库判重）。
让其中一层反向依赖另一层，或者两边各抄一份，都比这个第三选项差。
"""
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger("toutiao.trgm")

# 扩展是否存在不会在运行时变化，没必要每次请求/每条入库都查一遍
_CHECKED = False
_AVAILABLE = False


def available(db: Session) -> bool:
    """`pg_trgm` 是否可用（决定能否用 `similarity()` 做模糊匹配）。"""
    global _CHECKED, _AVAILABLE
    if _CHECKED:
        return _AVAILABLE
    if db.bind is None or db.bind.dialect.name != "postgresql":
        _AVAILABLE = False
    else:
        try:
            from sqlalchemy import text

            _AVAILABLE = bool(
                db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).scalar()
            )
        except Exception as e:
            logger.warning("pg_trgm 探测失败，模糊匹配退回精确口径：%s", e)
            _AVAILABLE = False
    _CHECKED = True
    return _AVAILABLE


def reset() -> None:
    """清掉探测结果（仅测试用：换方言/换库后缓存会失效）。"""
    global _CHECKED, _AVAILABLE
    _CHECKED = False
    _AVAILABLE = False
