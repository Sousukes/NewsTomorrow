"""管理端子模块共用：审计动作常量、`record_audit`、排序键与协议白名单。

刻意独立成文件而不是塞进 `__init__.py`：子模块 import 它不会触发包的
初始化顺序问题（`_auth`/`require_admin` 的循环依赖靠 `__init__` 底部的
延迟导入解决，共享常量不必跟着一起绕）。
"""
import json
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models import AdminAuditLog, News, now_str

logger = logging.getLogger("toutiao.admin")


ALLOWED_SCHEMES = ("http", "https")

# 审计动作：`对象.动作` 命名，前端据此映射中文标签（无需再维护一份枚举表）
AUDIT_SOURCE_CREATE = "source.create"
AUDIT_SOURCE_UPDATE = "source.update"
AUDIT_SOURCE_DELETE = "source.delete"
AUDIT_SOURCE_RESET = "source.reset"
AUDIT_ADMIN_ADD = "admin.add"
AUDIT_ADMIN_REMOVE = "admin.remove"
# C4：代重置密码。**detail 里绝不含明文密码**——审计表是长期留存的，
# 把凭据写进去等于给任何有只读权限的人发了一份密码清单。
AUDIT_PASSWORD_RESET = "user.password.reset"
# P1-20：资讯审核与下架
AUDIT_NEWS_HIDE = "news.hide"
AUDIT_NEWS_RESTORE = "news.restore"
AUDIT_NEWS_UPDATE = "news.update"
# P1-22：评论治理（软隐藏/恢复）
AUDIT_COMMENT_HIDE = "comment.hide"
AUDIT_COMMENT_RESTORE = "comment.restore"
# P1-23：敏感词库与复核
AUDIT_WORD_CREATE = "word.create"
AUDIT_WORD_UPDATE = "word.update"
AUDIT_WORD_DELETE = "word.delete"
AUDIT_WORD_RELOAD = "word.reload"
AUDIT_COMMENT_REVIEW = "comment.review"
# P1-24：昵称词库与复核（对象命名与 P1-23 对齐：前端据此映射中文标签）
AUDIT_NICKNAME_WORD_CREATE = "nicknameWord.create"
AUDIT_NICKNAME_WORD_UPDATE = "nicknameWord.update"
AUDIT_NICKNAME_WORD_DELETE = "nicknameWord.delete"
AUDIT_NICKNAME_WORD_RELOAD = "nicknameWord.reload"
AUDIT_NICKNAME_REVIEW = "nickname.review"

# 管理端资讯排序键：published_at 为空按空串处理（倒序时排最后），与公开列表同口径
_PUB = func.coalesce(News.published_at, "")


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

