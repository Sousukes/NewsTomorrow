"""ORM 模型：用户 / 资讯 / 评论 / 消息 / 消息已读。"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false as sa_false,
    true as sa_true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base

# SQLite 用 JSON，PostgreSQL 自动用 JSONB（可索引、查询更快）
JSONType = JSON().with_variant(JSONB, "postgresql")


def now_str() -> str:
    """当前 UTC 时间（ISO 秒精度，**带 +00:00 偏移**）——全库唯一时间口径（A3）。

    统一前此处返回 `datetime.now()`（**本地 naive**），而抓取/种子写 UTC
    aware、`ranking._iso_seconds` 写 naive-UTC——同一列混三种口径，
    「同格式 ISO 的字典序即时间序」这条契约被破坏（字符串排序与真实时间
    序脱节），读侧只能靠 `_to_naive_utc` 打补丁，而补丁对 naive 值
    （实为本地时间）按 UTC 解释，`now` 参与衰减计算时有整小时 skew。

    现在写入侧统一为 aware UTC；存量数据由迁移 0027 回填（只补偏移、
    不改数字——顺序精确保留）。**不要再引入 `datetime.now()` 写库**：
    新代码用本函数，或 `datetime.now(timezone.utc)`。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    """解析 ISO 时间串并归一成 **UTC aware** datetime（读取侧唯一入口）。

    - aware 输入（迁移 0027 后的全部存量 + 所有新写入）：转成 UTC；
    - naive 输入（只可能来自调用方锚点/测试）：**按 UTC 解释**——与
      0027 的回填口径一致（回填也只补偏移、不改数字），两处对 naive
      的解释必须相同，否则回填后的数据与读取侧各说各话；
    - 不可解析返回 None（调用方按"未知"处理）。
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(50), nullable=False, default="明日头条用户")
    # ---- P1-24 昵称待人工复核 ----
    # 命中 review 级昵称词时置位：放行但需运营看一眼。与 comments.pending_review
    # 同一处方——**不建索引**（待复核集预期很小，索引只会拖慢每次昵称写入），
    # 队列查询走全表扫描 + 按 id 倒序截断即可。
    # 与昵称本身正交：复核通过只清标志，不改用户自己起的名字。
    name_pending_review = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    # 复核时间：复核动作同样要留痕（审计 `nickname.review`）。NULL = 未复核过
    name_reviewed_at = Column(String(30))
    created_at = Column(String(30), nullable=False, default=now_str)
    # token 版本：JWT 载荷携带 ver，校验时比对；自增即可吊销该用户全部已签发 token
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    # ---- C4 强制改密 ----
    # 管理员代重置密码后置位：账号处于「受限态」——可读可改密，但发评论/点赞/
    # 收藏/进管理端都被拦（40305）。没有这个标志，「代重置」就等于给用户发了一个
    # 可以长期使用的临时密码：用户完全可以不改，然后那个密码就永久有效了。
    must_change_password = Column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )


class News(Base):
    __tablename__ = "news"

    id = Column(String(32), primary_key=True)
    title = Column(String(255), nullable=False)
    summary = Column(String(500), default="")
    # ---- E1 正文 ----
    # 正文（RSS content:encoded / Atom content，去标签后按段落以 \n 分隔）。
    # Text + 可空：长度不可控；存量行与无正文源没有正文，NULL 即「暂无正文」，
    # 与空串的区别是「抓过但源没给」也能被详情接口如实呈现。
    # **不加索引**：只按 id 点查，没有按正文检索的需求（搜索走标题/摘要）。
    # 列表接口（to_out）刻意不输出它，避免一页 20 条几十 KB 的无效带宽。
    content = Column(Text, nullable=True)
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
    # 热度分最近一次重算的时间（P1-21，naive 本地 ISO，秒精度）。
    # 增量重算的锚点：只有早于 staleness 窗口（或为 NULL）的行才参与本轮，
    # 避免无变动也全表重写。互动 bump 与全量重算都会刷新它。
    # **不建索引**：表有 prune 上限（保留天数 + 最小条数），每轮扫一遍找过期行足够便宜。
    hot_score_at = Column(String(30))
    # ---- P1-20 软下架 ----
    # **软下架而非硬删**：评论的 is_deleted 是同一处方——合规要求「保存记录」，
    # 误下架也要能一键恢复。公开查询（列表/详情/搜索/相关推荐）统一过滤 hidden，
    # 管理端可见并可恢复。
    # **不给 hidden 建索引**：下架集预期很小，加了只会让热 feed 的索引膨胀、
    # 拖慢每轮写入；过滤条件本身对全表扫描也足够便宜。
    # 热度重算仍计算已下架行：恢复的那一刻就该拿到新鲜分数，不必等下一轮。
    hidden = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    hidden_at = Column(String(30))
    # 下架理由：**必填**（trim 后 ≤300 字符，空则 422）——「留痕是准入条件」，
    # 与 P1-18 自动禁用必须能解释同理。恢复时清空（理由只对下架状态有意义）。
    hidden_reason = Column(String(300))

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
    # 运营软隐藏（P1-22，对齐 news 软下架处方）：作者删除（is_deleted）管不了
    # 别人的违规评论，治理需要运营侧手段——软隐藏而非物理删除：理由留痕、
    # 可恢复，点赞/回复结构全部保留。公开查询（主列表/回复/预览/计数）实时
    # 过滤，恢复即时可见，无需等任何定时任务。
    # **不给 hidden 建索引**：隐藏集预期很小，与 is_deleted 同理。
    # 隐藏/恢复同步维护 news.comment_count 与热度分：界面看到的评论数必须
    # 与真实可见条数一致（P1-12 修过的旧 bug 不能以另一种形式回来）。
    hidden = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    hidden_at = Column(String(30))
    # 隐藏理由：**必填**（trim 后 ≤300，空则 42200）——「留痕是准入条件」。
    # 恢复时清空（理由只对隐藏状态有意义，留着会让下一次隐藏的审计张冠李戴）。
    hidden_reason = Column(String(300))
    # 待人工复核（P1-23）：命中敏感词被**脱敏后发布**的评论不是"通过了审核"，
    # 而是"机器先兜住、人再看一眼"。与 `hidden` 正交：
    #   hidden  = 已处置、公开不可见；
    #   pending = 未处置、公开可见（内容已脱敏）。
    # 与 hidden / is_deleted 同处方——**不建索引**：待复核集预期很小，
    # 索引只会拖慢每次评论写入。
    pending_review = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    # 复核时间：复核动作同样留痕（审计 comment.review），故记时间而非只清标记
    reviewed_at = Column(String(30))

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
    # ---- A2 排序键（迁移 0026）----
    # 绝对创建时间（ISO 字符串）。列表按它倒序 + id 倒序分页——此前按随机 id
    # （`msg_{uuid4hex}`）排序，顺序与真实时间无关、keyset 游标语义失效。
    # **可空**：存量行没有可信时间源（`time` 存的是「1小时前」相对文案，
    # 不能反推绝对时间，回填等于编造数据）；排序用
    # `coalesce(created_at, '')` 兜底，空串在 DESC 下排最后 → 存量行自然沉底
    # （与 news.published_at 的 _PUB 同款处方，PG/SQLite 行为一致）。
    # `default=now_str`：notify/alert/种子等所有新建路径自动写入，单一来源。
    created_at = Column(String(30), default=now_str, index=True)
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
    # P1-23 补平：迁移 0015 建表时就带了这一列，但模型里一直没有——
    # 「迁移超前于模型」会让 `alembic check` 恒红，掩盖真正的漂移。
    # 索引同样要补：0015 建了 `ix_admin_users_created_at`，模型不带 index=True
    # 的话 check 会一直要求删掉它。
    created_at = Column(String(30), nullable=False, default=now_str, index=True)


class SensitiveWord(Base):
    """敏感词库（P1-23）：运营可维护，三种动作而非一刀切。

    为什么落库而不是写进配置文件：改词是**运营动作**且频率不低（新黑话、
    新引流方式每周都在变），配置文件意味着每改一个词都要发版重启。

    - `block`：直接拒绝提交（高危内容，让它根本没有出现在页面上的机会）；
    - `mask`：脱敏后照常发布 + 置 `pending_review` 交人工复核。**默认**——
      机器的判断是概率性的，"先脱敏再让人看"比"直接吞掉用户的话"代价小得多；
    - `allow`：**白名单豁免**。命中它的区间不再按敏感处理，是降低误杀的主要
      手段（短词「射」会命中「射击」「射手」，把后者加白即可）。
    - `enabled=False` 是停用而非删除：删错的词要能一秒恢复。
    - `hit_count`：累计命中次数。高频词要么是热点、要么是误杀大户——两种情况
      运营都必须看见，否则词库会悄悄腐化（加了一堆没人管的词，谁也不知道
      它每天在拦掉正常发言）。
    - `updated_at` 参与**热更新指纹**（`MAX(updated_at)` + `COUNT(*)`）：
      进程据此判断要不要重建匹配索引，比整表拉取便宜得多。
    """

    __tablename__ = "sensitive_words"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 唯一：重复词会让「改哪个」变得不确定，也意味着索引里有两个相同模式。
    word = Column(String(100), nullable=False, unique=True, index=True)
    action = Column(String(10), nullable=False, default="mask")
    category = Column(String(30), nullable=False, default="")  # 分组：广告/辱骂/违禁…
    enabled = Column(Boolean, nullable=False, default=True, server_default=sa_true())
    hit_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String(20), nullable=False, default="")  # 加词的运营（手机号）
    created_at = Column(String(30), default=now_str)
    updated_at = Column(String(30), default=now_str, index=True)


class SensitiveHit(Base):
    """敏感词命中日志（P1-23）：脱敏会丢原文，复盘与申诉全靠这一行。

    脱敏后正文里的原文**不可复原**（这是脱敏的目的）。于是"这条是不是误杀"
    只能靠命中时留的证据回答——没有日志，误杀就是无头案，申诉也无从受理。

    - 记**词本身**而非 `word_id`：词被删掉后，日志仍要读得懂（同期数据不能
      因为后来的维护动作变成一堆悬空外键）。
    - `snippet` 存**原文片段**：判断误杀需要上下文（"他说的是不是别的意思"）。
    - `field` 区分正文 / 昵称：昵称误杀率与正文不是一个量级，统计时必须分开看。
    - `comment_id` 在 block 场景为 NULL（没写成评论）——拦截也是要留痕的，
      否则"到底拦了多少人"是个黑箱，也无法识别"反复尝试绕过"的行为。
    """

    __tablename__ = "sensitive_hits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    word = Column(String(100), nullable=False)
    action = Column(String(10), nullable=False)  # block | mask（allow 不记）
    field = Column(String(10), nullable=False, default="content")  # content | name
    comment_id = Column(String(64), nullable=True)
    news_id = Column(String(32), nullable=True)
    user_id = Column(Integer, nullable=True)
    user_name = Column(String(50), nullable=False, default="")
    snippet = Column(String(300), nullable=False, default="")
    created_at = Column(String(30), nullable=False, default=now_str, index=True)

    __table_args__ = (
        # 「这个词命中过多少次 / 最近命中」按词查且要按时间倒序
        Index("ix_sensitive_hits_word_id", "word", "id"),
    )


class NicknameWord(Base):
    """昵称敏感词库（P1-24）：运营可维护，动作集与评论词库**不同**。

    为什么不复用 `sensitive_words`：评论有 `mask`（脱敏后照常发布），昵称没有
    这个语义——昵称是账号属性，"脱敏"意味着改掉别人的名字，而用户此刻改不了。
    昵称的动作是 `block`（拒绝设置）与 `review`（放行 + 待人工复核）。
    两张表的动作集不同、别名只对昵称有意义，硬合并会让"这个词拦的是评论还是
    昵称"永远说不清（错误码/文案分发也会被迫共用一套）。

    - `block`：拒绝设置（**40006**，提示不回显命中词）；
    - `review`：**默认**——放行但置 `users.name_pending_review` 进人工队列。
      机器的判断是概率性的，"先放行再让人看"比"直接吞掉用户起的名字"代价小；
    - `allow`：白名单豁免（命中区间被 allow 区间完全包裹才生效），降误杀的逃生门；
    - `aliases`：**拼音全拼 / 首字母 / 谐音 / 常见变形**。与主词走同一条归一化
      管道、作为额外模式串参与匹配（"shabi"、"sb" 这类字母写法只有配了别名
      才拦得住——同音汉字替换没有字符级算法可解，靠运营补别名）；
    - `enabled=False` 是停用而非删除：删错的词要能一秒恢复；
    - `hit_count`：累计命中次数。高频词要么是热点、要么是误杀大户，运营都必须看见；
    - `updated_at` 参与**热更新指纹**（`MAX(updated_at)` + `COUNT(*)`）。
    """

    __tablename__ = "nickname_words"

    id = Column(Integer, primary_key=True, autoincrement=True)
    word = Column(String(100), nullable=False, unique=True, index=True)
    action = Column(String(10), nullable=False, default="review")
    category = Column(String(30), nullable=False, default="")  # 分组：广告/辱骂/违禁…
    # 别名（JSON 数组）：拼音/谐音/变形。引擎加载时与主词走同一条归一化管道，
    # 每个别名按各形态展开成额外模式串；命中别名时日志记 `matched` 便于回溯。
    aliases = Column(JSONType, default=list)
    enabled = Column(Boolean, nullable=False, default=True, server_default=sa_true())
    hit_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String(20), nullable=False, default="")  # 加词的运营（手机号）
    created_at = Column(String(30), default=now_str)
    updated_at = Column(String(30), default=now_str, index=True)


class NicknameHit(Base):
    """昵称敏感词命中日志（P1-24）：误杀复盘与申诉的唯一证据。

    与 `sensitive_hits` 分开存：两张词库的动作集、复核流程都不同，混在一张表里
    查询时要先按词反查是哪种词，且 `field` 之类的区分列会变成第二套语义。

    - 记**主词本身**而非 `word_id`：词被删掉后日志仍要读得懂；
    - `matched` 记**实际命中的模式**（主词或某条别名）：回答"是哪条别名拦住的"，
      也是判断"该补哪个别名"的依据；
    - `source` 区分注册 / 改昵称：反复尝试绕过基本发生在改名入口；
    - `snippet` 存**提交的昵称原文**（≤24 字符，整体就是上下文）；
    - block 时同样留痕：否则"拦了多少次改名"是黑箱。
    """

    __tablename__ = "nickname_hits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    word = Column(String(100), nullable=False)
    matched = Column(String(100), nullable=False, default="")
    action = Column(String(10), nullable=False)  # block | review（allow 不记）
    source = Column(String(10), nullable=False, default="update")  # register | update
    user_id = Column(Integer, nullable=True)
    user_name = Column(String(50), nullable=False, default="")
    snippet = Column(String(300), nullable=False, default="")
    created_at = Column(String(30), nullable=False, default=now_str, index=True)

    __table_args__ = (
        # 「这个词命中过多少次 / 最近命中」按词查且要按时间倒序
        Index("ix_nickname_hits_word_id", "word", "id"),
    )