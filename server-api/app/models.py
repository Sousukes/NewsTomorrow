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
    # P1-16 抓取礼仪：是否遵守对方 robots.txt。逐源可关——个别源的 robots 写得过严
    #（甚至整站 Disallow）却实际提供公开 RSS，一刀切会让这些源直接失去作用。
    respect_robots = Column(Boolean, nullable=False, default=True)
    # P1-16 告警节流：上次就该源发出告警的时间（为空 = 从未告警）。
    # 落库而非进程内变量：多实例部署时各实例共享同一节流窗口，重启也不失效。
    last_alerted_at = Column(String(30))
    # ---- P1-18 失败分级与熔断 ----
    # 连续失败次数：成功即归零。它是熔断与"是否值得继续重试"的唯一依据，
    # 必须**落库**——进程内计数在多实例下各算各的，重启也会丢失。
    consecutive_failures = Column(Integer, nullable=False, default=0)
    # 最近一次失败的**性质**：transient（可重试：超时/5xx/连接错误/429）
    # 或 permanent（永久：4xx/robots 拒绝/解析无条目）。成功时置空。
    # 区分二者决定了对策：前者等一等就好，后者必须改配置或换源。
    failure_kind = Column(String(20))
    # 熔断到期时间：连续失败达阈值后设置，冷却期内整轮跳过该源（省请求也不烦对方）。
    next_retry_at = Column(String(30))
    created_at = Column(String(30), default=now_str, nullable=False)
    updated_at = Column(String(30), default=now_str, nullable=False)

    __table_args__ = (Index("ix_rss_sources_channel_enabled", "channel", "enabled"),)


class RssSourceHealthEvent(Base):
    """源健康历史（P1-19）：**每轮抓取一行**，回答"它是什么时候开始烂的"。

    P1-16~P1-18 把「当前状态」做到了位（robots / 告警 / 失败分级 / 熔断），
    但健康字段只有"最近一次"——**没有时间维度**，于是这几个问题都答不了：

    - "这个源是今天才开始挂的，还是已经烂了两周？"（决定要不要现在处理）
    - "是不是每天凌晨固定抖？"（规律性 = 对方维护窗口，不是我们的锅）
    - "上次改了地址/权重，有好转吗？"（改动效果无从验证）
    - **"源一直返回成功，但条数从 30 条掉到 3 条"**——
      它永远是绿的，其实已经废了。这是当前设计**结构性看不见**的一类故障。

    存**原始事件**而非每日聚合表，理由与 `SearchLog` 同源：聚合表只能算出
    "历史累计"，无法回答"近期趋势"，也无法事后换窗口重新看。代价是需要清理。

    **只记录真实尝试**：按礼仪/熔断跳过的源本轮根本没发请求，
    留一行会伪造出"尝试过"的痕迹——与「跳过的源不刷新健康状态」同一条口径。
    因此趋势图上的空白 = 我们那时没抓它，不是失败。
    """

    __tablename__ = "rss_source_health_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 源被删除时其历史一并删除：留着没有可解释的对象，只会越积越多
    source_key = Column(
        String(64), ForeignKey("rss_sources.key", ondelete="CASCADE"), nullable=False
    )
    ok = Column(Boolean, nullable=False)
    # 本轮入库条数。**重点看它的长期趋势**——"成功但越来越少"是半死不活的典型
    item_count = Column(Integer, nullable=False, default=0)
    error = Column(String(300))
    # 失败性质（transient / permanent），与 P1-18 `failure_kind` 同义，成功时为空
    kind = Column(String(20))
    # 绝对 ISO 时间（与其它表的字符串时间口径一致）
    at = Column(String(30), nullable=False, default=now_str)

    __table_args__ = (
        # 主查询是「某源最近 N 天」，复合索引让范围扫描落在索引上
        Index("ix_rss_health_key_at", "source_key", "at"),
    )


class AdminAuditLog(Base):
    """运营审计：谁在什么时候改了什么（P1-15）。

    权限体系只回答了「谁能进后台」，没回答「谁干了什么」——源被停掉、地址被换、
    管理员被加进来，此前都无痕迹，事后只能靠猜。

    - `action` 用 `对象.动作` 命名（如 `source.update`），前端据此映射中文标签；
    - `detail` 存 **JSON 字符串**：更新存 `{"changes": {字段: [旧, 新]}}`，
      增删存 `{"snapshot": {...}}`；
    - 与业务变更**同事务写入**（调用方 `record_audit` 后一并 commit）——
      「改成功了但没留痕」比「改失败」更糟，所以不做 try/except 静默。
    """

    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_phone = Column(String(20), nullable=False, index=True)
    action = Column(String(40), nullable=False, index=True)
    target = Column(String(120), nullable=False, default="")  # 源 key / 手机号等对象标识
    detail = Column(String(1000), nullable=False, default="")  # JSON 串
    created_at = Column(String(30), nullable=False, default=now_str, index=True)

    __table_args__ = (
        # 「这个源被谁动过」按 target 查，且要按时间倒序
        Index("ix_admin_audit_target_id", "target", "id"),
    )


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