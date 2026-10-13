"""ORM 模型：用户 / 资讯 / 评论 / 消息 / 消息已读。"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    false as sa_false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base

# SQLite 用 JSON，PostgreSQL 自动用 JSONB（可索引、查询更快）
JSONType = JSON().with_variant(JSONB, "postgresql")


def now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(50), nullable=False, default="明日头条用户")
    created_at = Column(String(30), nullable=False, default=now_str)
    # token 版本：JWT 载荷携带 ver，校验时比对；自增即可吊销该用户全部已签发 token
    token_version = Column(Integer, nullable=False, default=0, server_default="0")


class News(Base):
    __tablename__ = "news"

    id = Column(String(32), primary_key=True)
    title = Column(String(255), nullable=False)
    summary = Column(String(500), default="")
    source = Column(String(50), default="")
    comment_count = Column(Integer, default=0)
    time = Column(String(30), default="")
    images = Column(JSONType, default=list)
    cover = Column(String(500), default="")
    video_url = Column(String(500), default="")
    play_count = Column(Integer)
    is_top = Column(Boolean, default=False)
    channel = Column(String(30), default="recommend", index=True)
    variant = Column(String(20), default="left-right")
    # 绝对发布时间（ISO 字符串），供增量/时效清理排序；time 存相对展示文案
    published_at = Column(String(30), index=True)
    # 热度分（落库而非查询时计算）：评论/收藏/播放加权后乘时间衰减。
    # 落库才能建索引并被游标作为标量比较；由 ranking.recompute_hot_scores 周期重算。
    hot_score = Column(Float, nullable=False, default=0.0, server_default="0")

    comments = relationship(
        "Comment", back_populates="news", cascade="all, delete-orphan", passive_deletes=True
    )

    # 索引需在模型中声明，与迁移 0004/0005 保持一致。
    # 否则 `alembic check` 判定漂移、`--autogenerate` 会生成 drop_index 把它们删掉。
    __table_args__ = (
        # 支撑 feed 排序 is_top DESC, published_at DESC, id DESC 与频道过滤
        Index("ix_news_feed", "channel", "is_top", "published_at", "id"),
        # 支撑热榜/个性化排序 is_top DESC, hot_score DESC, id DESC
        Index("ix_news_hot", "channel", "is_top", "hot_score", "id"),
        # 搜索用 ILIKE '%kw%'，前缀通配符使 B-tree 失效，需 pg_trgm GIN 索引。
        # postgresql_* 参数仅在 PostgreSQL 方言生效，其它方言（如 SQLite 测试库）自动忽略。
        Index(
            "ix_news_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_news_summary_trgm",
            "summary",
            postgresql_using="gin",
            postgresql_ops={"summary": "gin_trgm_ops"},
        ),
    )


class Comment(Base):
    __tablename__ = "comments"

    id = Column(String(64), primary_key=True)
    news_id = Column(String(32), ForeignKey("news.id"), index=True, nullable=False)
    # 发表者。**可空**：历史数据（种子/迁移前）没有作者归属，无法回填。
    # 有了它才能做「删除自己的评论」「我的评论」「按用户治理」——此前只有
    # user_name 字符串，无法可靠判定归属（重名即越权）。
    # 用户被删除时置空而非级联删除：评论本身仍有讨论价值。
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    user_name = Column(String(50), default="匿名")
    seed = Column(String(50), default="")
    content = Column(String(500), nullable=False)
    # time 存"1小时前"等展示文案；created_at 存绝对 ISO 时间，**排序唯一依据**
    # （相对文案字典序与真实时间不一致，不能用于排序）
    time = Column(String(30), default=now_str)
    created_at = Column(String(30), default=now_str, index=True)
    likes = Column(Integer, default=0)
    # 两级评论（0014）：
    # - parent_id：被回复的评论 id（前端展示「回复 @谁」用）；
    # - root_id：所属**主评论** id；NULL 表示自身就是主评论。
    # 回复的回复拍平到同一 root（root = parent.root_id or parent.id），
    # 列表只出主评论，回复按 root_id 聚合挂在主评论下。
    # 自引用外键 ON DELETE CASCADE：评论物理删除时其回复/子结构随之清理，
    # 不会留下 root_id 悬空导致回复「升级」成主评论。
    parent_id = Column(String(64), ForeignKey("comments.id", ondelete="CASCADE"), nullable=True)
    root_id = Column(
        String(64), ForeignKey("comments.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # 软删除：**不物理删除**——评论可能已被他人点赞，抹掉会丢失互动记录，
    # 且后续审核/申诉需要留存原文。列表接口过滤掉已删除项。
    is_deleted = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    deleted_at = Column(String(30))

    news = relationship("News", back_populates="comments")


class CommentLike(Base):
    """评论点赞关联表（comment × user），0014 取代 `comments.liked_by` JSON 数组。

    与 message_reads（0004）同款处方：JSON 数组无法建索引，点赞判定要整行
    读出解析，并发点赞还会读-改-写丢失。关联表 + 复合主键后：
    - 「同一用户对同一评论只点一次」由主键天然保证（重复 INSERT 即冲突）；
    - 判定当前用户是否点赞过 = 一次 O(log n) 主键/索引查询。

    不建 ORM relationship：评论走软删除、永不物理删除，没有需要级联的
    对象图；路由直接查本表即可（关系表的意义就在于绕开对象图）。
    """

    __tablename__ = "comment_likes"

    comment_id = Column(
        String(64), ForeignKey("comments.id", ondelete="CASCADE"), primary_key=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(String(30), default=now_str)


class Message(Base):
    """消息中心：系统通知 / 评论互动 / 推荐。"""

    __tablename__ = "messages"

    id = Column(String(64), primary_key=True)
    type = Column(String(20), default="system")  # system | comment | recommend
    title = Column(String(120), nullable=False)
    summary = Column(String(300), default="")
    # 关联资讯（可空）。ON DELETE SET NULL：资讯被清理时消息保留，仅解除关联
    news_id = Column(String(32), ForeignKey("news.id", ondelete="SET NULL"), index=True)
    # 收件人：**NULL = 广播**（全员可见，如系统通知）；非 NULL = 定向给该用户。
    # 真实互动事件（有人评论/点赞）必须定向，否则会出现"收到别人的私信"。
    # 用户被删除时其消息一并删除（定向消息对他人无意义）。
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    time = Column(String(30), default=now_str)

    news = relationship("News")
    reads = relationship(
        "MessageRead", back_populates="message", cascade="all, delete-orphan", passive_deletes=True
    )


class MessageRead(Base):
    """消息已读关联表（user × message）。

    取代早期的 `messages.read_by` JSON 数组：JSON 数组无法建索引，
    未读数/已读判定只能全表遍历；改为关联表后可用复合主键索引做 O(log n) 判定。
    """

    __tablename__ = "message_reads"

    message_id = Column(
        String(64), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(String(30), default=now_str)

    message = relationship("Message", back_populates="reads")


class UserFavorite(Base):
    """用户收藏（user × news）。

    此前收藏只存在前端 localStorage：换设备/清缓存即丢失，且无法跨端一致。
    落到服务端后，登录即可同步；复合主键天然保证「同一用户同一资讯只收藏一次」（幂等）。
    资讯被清理时随之删除（PG 走 ON DELETE CASCADE；SQLite 由 prune_old_news 显式清理）。
    """

    __tablename__ = "user_favorites"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    news_id = Column(String(32), ForeignKey("news.id", ondelete="CASCADE"), primary_key=True)
    # 绝对 ISO 时间：列表按收藏时间倒序的唯一依据
    created_at = Column(String(30), default=now_str, nullable=False)

    __table_args__ = (Index("ix_user_favorites_user_created", "user_id", "created_at"),)


class UserHistory(Base):
    """用户浏览历史（user × news）。

    与收藏同为「用户行为标记」，但语义是"最近看过"：同一资讯重复浏览只刷新
    `viewed_at`（不产生多行），并按上限裁剪最旧记录。
    """

    __tablename__ = "user_history"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    news_id = Column(String(32), ForeignKey("news.id", ondelete="CASCADE"), primary_key=True)
    viewed_at = Column(String(30), default=now_str, nullable=False)

    __table_args__ = (Index("ix_user_history_user_viewed", "user_id", "viewed_at"),)


class SearchLog(Base):
    """搜索词日志（热词统计的原始来源）。

    记**原始日志**而非聚合计数：聚合表（`term` + `count`）只能算出"历史累计最热"，
    无法表达"近期热度"，旧词会永久占榜。日志表配合时间窗口即可得到真正的热词，
    代价是需要定期清理（见 `prune_search_logs`）。

    `user_id` 可空（未登录也允许搜索），用于**同一用户短时间内的重复词去重**——
    否则某人反复搜同一个词就能把该词刷上热搜榜。
    """

    __tablename__ = "search_logs"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    term = Column(String(50), nullable=False, index=True)
    created_at = Column(String(30), default=now_str, nullable=False, index=True)

    __table_args__ = (Index("ix_search_logs_term_created", "term", "created_at"),)


class RssSource(Base):
    """RSS 源配置（可运行时增删启停）。

    此前源**硬编码**在 `feeds/sources.py`，改一个源要改代码并重新部署。
    落到库后运营可通过管理接口调整；内置源在表为空时自动播种，
    保证新环境开箱可用、无需手工初始化。

    `key` 为内部标识（也是内置源与库中记录的对齐依据）。
    健康字段（`last_status` / `last_error` / `last_item_count` / `last_fetched_at`）
    由抓取流程回写，用于快速判断"哪个源挂了"——源多了以后这是排障的主要依据。
    """

    __tablename__ = "rss_sources"

    key = Column(String(64), primary_key=True)
    name = Column(String(100), nullable=False)
    channel = Column(String(30), nullable=False, default="recommend", index=True)
    url = Column(String(500), nullable=False)
    # 权重：多源转载同一稿件时，高权重者先入库（collect 先到先得）
    weight = Column(Integer, nullable=False, default=5)
    enabled = Column(Boolean, nullable=False, default=True)
    # ---- 抓取健康状态（旁路信息，失败不影响源本身）----
    last_status = Column(String(10))  # ok | error
    last_error = Column(String(300))
    last_item_count = Column(Integer)
    last_fetched_at = Column(String(30))
    created_at = Column(String(30), default=now_str, nullable=False)
    updated_at = Column(String(30), default=now_str, nullable=False)

    __table_args__ = (Index("ix_rss_sources_channel_enabled", "channel", "enabled"),)


class AdminUser(Base):
    """管理员白名单（应用内可管理的「指定账号」名单）。

    与 `ADMIN_PHONES` 环境变量是**并集**关系（见 `routers/admin.is_admin`）：
    - env 保留为 break-glass：改配置重启即生效，不依赖数据库可用；
    - 本表供日常运营：应用内增删管理员，无需重启服务；
    - 两边都为空 = 管理能力整体关闭（配置疏漏时不应暴露管理接口）。

    手机号即主键且**不设 users 外键**：支持预授权——账号尚未注册也可
    先加入名单，其注册/登录时 `isAdmin` 即为 true。列表接口用一次
    `db.get(User, phone)` 区分「已注册 / 预授权」供界面展示。
    """

    __tablename__ = "admin_users"

    phone = Column(String(20), primary_key=True)
    note = Column(String(100), nullable=False, default="")  # 备注（如「运营-张三」）
    added_by = Column(String(20), nullable=False, default="")  # 操作者手机号（审计用）
    created_at = Column(String(30), nullable=False, default=now_str, index=True)