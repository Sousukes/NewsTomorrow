"""应用配置：可通过环境变量或 .env 文件覆盖。

优先级：真实环境变量 > .env 文件 > 默认值。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# 加载 server-api/.env（若存在）；真实环境变量优先级更高，不会被覆盖
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    """读取环境变量：空字符串视为“未设置”，回退到默认值。

    .env 里常写 `JWT_SECRET=` 这种空占位，若直接采用会得到空密钥，
    导致 JWT 签名报「HMAC key must not be empty」。
    """
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


# 运行环境：development（默认，放开 Swagger / CORS *） | production（收紧）
ENVIRONMENT = _env("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

# 运维接口令牌（C5）：`/api/health` 与 `/api/metrics` 会暴露连接池状态与 Top20 路径分布，
# 不应公开可读。设置后必须携带 `Authorization: Bearer <token>` 或 `X-Ops-Token: <token>`。
# 未设置时：**开发放行**（本地 / CI / 契约检查不受影响）、**生产拒绝**（fail-closed，
# 避免忘记配置就把内部状态暴露在公网）。
OPS_TOKEN = _env("OPS_TOKEN")

# 数据库：默认 PostgreSQL；回退 SQLite 改 DATABASE_URL=sqlite:///./toutiao.db
DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'toutiao.db').as_posix()}")

# ---- 连接池（PostgreSQL 生效；SQLite 走文件库/内存库的对应策略）----
DB_POOL_SIZE = int(_env("DB_POOL_SIZE", "5"))  # 常驻连接数
DB_MAX_OVERFLOW = int(_env("DB_MAX_OVERFLOW", "10"))  # 峰值可额外创建的连接
DB_POOL_TIMEOUT = int(_env("DB_POOL_TIMEOUT", "30"))  # 等待可用连接的最长秒数
DB_POOL_RECYCLE = int(_env("DB_POOL_RECYCLE", "1800"))  # 连接最大存活秒数，应小于 PG 的 idle_timeout
DB_POOL_PRE_PING = _env("DB_POOL_PRE_PING", "true").lower() in ("1", "true", "yes", "on")
DB_CONNECT_TIMEOUT = int(_env("DB_CONNECT_TIMEOUT", "10"))  # 建连超时秒数
DB_APP_NAME = _env("DB_APP_NAME", "toutiao-api")  # 便于在 pg_stat_activity 中识别应用

# JWT 配置
DEFAULT_JWT_SECRET = "dev-secret-change-me"
JWT_SECRET = _env("JWT_SECRET", DEFAULT_JWT_SECRET)
JWT_SECRET_IS_DEFAULT = JWT_SECRET == DEFAULT_JWT_SECRET
JWT_ALG = "HS256"
JWT_EXPIRE_MINUTES = int(_env("JWT_EXPIRE_MINUTES", str(60 * 24 * 7)))  # 默认 7 天

# CORS：开发默认放开；生产默认同源（不跨域），需显式配置 CORS_ORIGINS
CORS_ORIGINS: list[str] = [o.strip() for o in _env("CORS_ORIGINS").split(",") if o.strip()] or (
    [] if IS_PRODUCTION else ["*"]
)

# 接口文档：生产默认关闭
DOCS_URL = None if IS_PRODUCTION else "/docs"
REDOC_URL = None if IS_PRODUCTION else "/redoc"
OPENAPI_URL = None if IS_PRODUCTION else "/openapi.json"

# 访问日志级别
LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# 限流（内存固定窗口；多实例部署请换成 Redis 等共享实现）
RATE_LIMIT_ENABLED = _env("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
RATE_LIMIT_PER_MINUTE = int(_env("RATE_LIMIT_PER_MINUTE", "120"))
LOGIN_RATE_LIMIT_PER_MINUTE = int(_env("LOGIN_RATE_LIMIT_PER_MINUTE", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(_env("RATE_LIMIT_WINDOW_SECONDS", "60"))
# 登录失败锁定（C2，账号维度）：与 IP 限流互补——IP 维度防不住
# 「换 IP 打同一个账号」。同一账号连续失败达上限即临时锁定整个窗口，
# 锁定期间连密码验证都不做（否则继续尝试会无限续期）。
# 默认 5 次 / 15 分钟：给真人足够的手滑空间，又把爆破压到 5 次/15 分钟。
LOGIN_FAIL_LIMIT = int(_env("LOGIN_FAIL_LIMIT", "5"))
LOGIN_FAIL_WINDOW_SECONDS = int(_env("LOGIN_FAIL_WINDOW_SECONDS", "900"))
# Redis 连接串：设置后限流改用 Redis 共享计数（多实例/多 worker 正确）；留空用进程内实现
REDIS_URL = _env("REDIS_URL")

# 种子演示账号
DEMO_PHONE = _env("DEMO_PHONE", "13800138000")
DEMO_PASSWORD = _env("DEMO_PASSWORD", "123456")
# 是否播种示例数据（演示账号 + 示例资讯/评论/消息）：
# 生产环境默认关闭（避免弱口令默认账号与演示内容）；测试可显式置 false 保证确定性。
SEED_SAMPLE_DATA = _env(
    "SEED_SAMPLE_DATA", "false" if IS_PRODUCTION else "true"
).lower() in ("1", "true", "yes", "on")

# ---- 受信反向代理 ----
# 仅当**直连来源 IP** 落在这些网段内时，才采信 X-Forwarded-For。
# 留空表示不信任任何代理头（直接用 socket 对端 IP），可防止伪造 XFF 绕过限流。
# 例：TRUSTED_PROXIES=127.0.0.1,::1,10.0.0.0/8
TRUSTED_PROXIES: list[str] = [p.strip() for p in _env("TRUSTED_PROXIES").split(",") if p.strip()]

# ---- 管理员 ----
# 可触发敏感操作（如手动抓取 /api/news/refresh）的手机号白名单。
# 留空表示该能力在生产不可用（返回 403）。
ADMIN_PHONES: list[str] = [p.strip() for p in _env("ADMIN_PHONES").split(",") if p.strip()]

# ---- RSS 抓取 ----
# 定时抓取开关与周期
FEED_CRON_ENABLED = _env("FEED_CRON_ENABLED", "true").lower() in ("1", "true", "yes", "on")
FEED_CRON_INTERVAL_MINUTES = int(_env("FEED_CRON_INTERVAL_MINUTES", "30"))
# ---- 热度重算（P1-21）：独立于抓取的 cron ----
# 此前热度重算寄生在抓取任务里：抓取被关停（FEED_CRON_ENABLED=false）时热度
# 永不衰减，hot / for-you 排序冻结；重算节奏也被抓取周期绑定。
# 现在它是独立任务——抓取停不停，衰减都按时推进。
HOT_CRON_ENABLED = _env("HOT_CRON_ENABLED", "true").lower() in ("1", "true", "yes", "on")
# 重算周期。互动（评论/收藏）发生时是即时 bump 的，周期任务只负责时间衰减的
# 缓慢推进，无需跟抓取同频。
HOT_CRON_INTERVAL_MINUTES = max(1, int(_env("HOT_CRON_INTERVAL_MINUTES", "15")))
# staleness 窗口：只重算锚点（hot_score_at）早于该窗口的行。窗口大于周期时，
# 每行每窗口最多被重写一次，避免无变动也全表重写（写入放大）；
# 代价是分数最多陈旧一个窗口——半衰期 48h 下 60 分钟约 2% 衰减，排序无感。
HOT_STALE_MINUTES = max(1, int(_env("HOT_STALE_MINUTES", "60")))
# 单源抓取条数上限
FEED_LIMIT_PER_SOURCE = int(_env("FEED_LIMIT_PER_SOURCE", "30"))
# HTTP 超时（读/写）与连接超时
FEED_TIMEOUT = float(_env("FEED_TIMEOUT", "15"))
FEED_CONNECT_TIMEOUT = float(_env("FEED_CONNECT_TIMEOUT", "8"))
# 旧数据清理：保留天数与最小保留条数
FEED_KEEP_DAYS = int(_env("FEED_KEEP_DAYS", "30"))
FEED_KEEP_MIN = int(_env("FEED_KEEP_MIN", "200"))
# ---- 抓取治理（P1-16）：并发化 + 礼貌抓取 + 告警 ----
# 并发抓取的线程数。源变多后串行会让一轮耗时线性增长，但并行必须配套下列礼貌控制，
# 否则等于把压力转嫁给对方站点（也可能被封）。
FEED_MAX_CONCURRENCY = max(1, int(_env("FEED_MAX_CONCURRENCY", "4")))
# 同一站点两次请求之间的最小间隔（秒）：同一 host 的多个源串行抓取时的喘息
FEED_HOST_GAP_SECONDS = max(0.0, float(_env("FEED_HOST_GAP_SECONDS", "1.5")))
# 单源最小抓取间隔（分钟）：定时任务与手动触发叠加时不重复打同一个源
FEED_MIN_INTERVAL_MINUTES = max(0, int(_env("FEED_MIN_INTERVAL_MINUTES", "10")))
# robots.txt 缓存时长（小时）：不应当每轮每源都去取一次
FEED_ROBOTS_TTL_HOURS = max(1, int(_env("FEED_ROBOTS_TTL_HOURS", "24")))
# 告警：源超过该时长**未成功抓取**即视为异常；同一源的告警最小间隔（节流，防刷屏）
FEED_ALERT_ENABLED = _env("FEED_ALERT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
FEED_ALERT_STALE_HOURS = max(1, int(_env("FEED_ALERT_STALE_HOURS", "24")))
FEED_ALERT_MIN_INTERVAL_HOURS = max(1, int(_env("FEED_ALERT_MIN_INTERVAL_HOURS", "6")))
# 告警聚合（P1-17）：本轮待告警源达到该数量时合并为**一条汇总**。
# 断网 / 对方站点整体挂掉时十几个源会同时异常，逐源发一条等于刷屏，
# 而收件人对十几条消息也做不了逐个处置——他要的信息是"出事了，N 条，去看源管理"。
FEED_ALERT_AGGREGATE_MIN = max(2, int(_env("FEED_ALERT_AGGREGATE_MIN", "3")))
# 汇总消息里每类原因最多列出几个源名，其余用"等"概括（消息预览是单行省略号，列多了看不见）
FEED_ALERT_AGGREGATE_LIST = max(1, int(_env("FEED_ALERT_AGGREGATE_LIST", "5")))
# ---- 失败分级与熔断（P1-18）----
# 只有**可重试**失败（超时 / 连接错误 / 5xx / 429）才退避重试；
# 其余 4xx、robots 拒绝、解析无条目都是永久性的，重试多少次结果都一样，
# 白白多打 N 个请求（对对方站点也是负担）。
FEED_RETRY_TRANSIENT = max(0, int(_env("FEED_RETRY_TRANSIENT", "2")))
FEED_RETRY_BACKOFF = max(0.0, float(_env("FEED_RETRY_BACKOFF", "0.6")))
# 连续失败达阈值即进入冷却（熔断）：已经连挂 5 轮的源不需要每轮再打 3 次。
# 冷却期内该源**整轮跳过且不动健康状态**（与按礼仪跳过同口径——刷新会让"多久没成功"归零）。
FEED_CIRCUIT_THRESHOLD = max(0, int(_env("FEED_CIRCUIT_THRESHOLD", "5")))
FEED_CIRCUIT_COOLDOWN_MINUTES = max(1, int(_env("FEED_CIRCUIT_COOLDOWN_MINUTES", "30")))
# 连续**永久**失败达阈值自动禁用该源；**默认 0 = 关闭**。
# 刻意默认关闭：源临时 404 一周就被静默关掉、且没人记得重新打开，比一直重试更糟。
# 开启后每次触发都必须留痕（审计 + 告警），绝不静默。
FEED_AUTO_DISABLE_PERMANENT = max(0, int(_env("FEED_AUTO_DISABLE_PERMANENT", "0")))
# ---- 健康历史（P1-19）----
# 逐轮抓取事件的保留天数。趋势窗口的上限——想看 90 天趋势就调大它。
FEED_HEALTH_KEEP_DAYS = max(1, int(_env("FEED_HEALTH_KEEP_DAYS", "30")))
# 源列表上「近期成功率」的统计窗口（天）。它回答的不是"现在挂没挂"，
# 而是"这个源最近靠不靠谱"——7 天足以看出退化，又不至于被一次偶发抖动带偏。
FEED_HEALTH_LIST_DAYS = max(1, int(_env("FEED_HEALTH_LIST_DAYS", "7")))

# ---- 评论内容审核：敏感词（P1-23）----
# 总开关。关闭后词库仍在、管理端仍可维护，但提交链路不再检测——
# 出问题（大面积误杀）时的**一键止血**，不需要发版。
MODERATION_ENABLED = _env("MODERATION_ENABLED", "true").lower() in ("1", "true", "yes", "on")
# 是否连**昵称**一起检测。昵称是账号级属性但会随评论展示，脏昵称等于
# 让绕过者换个地方贴。默认开；误杀敏感的场景可关（昵称的语言组合远比正文随机）。
MODERATION_CHECK_NAME = _env("MODERATION_CHECK_NAME", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# 词库缓存有效期（秒）。到期才回库查一次「指纹」（MAX(updated_at)+COUNT(*)），
# 指纹没变就继续用内存索引——**检测本身永不查库**，这是性能的关键。
# 管理端改词会主动失效本进程缓存（保存即生效），TTL 只是集群下其他实例的兜底。
MODERATION_CACHE_TTL_SECONDS = max(0, int(_env("MODERATION_CACHE_TTL_SECONDS", "30")))
# 词库最短长度（**按归一化后的字符数计**）。1 字词的误杀率不可接受
# （单字「射」「毒」「杀」在正常表达里遍地都是），默认挡在入库这一步。
MODERATION_MIN_WORD_LEN = max(1, int(_env("MODERATION_MIN_WORD_LEN", "2")))
# 脱敏替换字符
MODERATION_MASK_CHAR = _env("MODERATION_MASK_CHAR", "*")[:1] or "*"
# 兜底动作：库中 action 缺失/未知时按此处理。刻意取 mask 而非 block——
# 数据异常时应当降级为"先脱敏留给人看"，而不是把用户的发言直接吞掉。
MODERATION_DEFAULT_ACTION = _env("MODERATION_DEFAULT_ACTION", "mask").lower()
# 词库规模上限（条）。防止误导入百万级词表把内存与构建时间拖垮；
# 超出部分在加载时截断并告警——宁可少拦几个词，也不能让评论提交变慢。
MODERATION_MAX_WORDS = max(100, int(_env("MODERATION_MAX_WORDS", "20000")))

# ---- 昵称敏感词校验（P1-24）----
# 总开关。关闭后词库仍在、管理端仍可维护，但注册/改昵称不再检测——
# 出问题（大面积误杀）时的**一键止血**，不需要发版。
NICKNAME_MODERATION_ENABLED = _env("NICKNAME_MODERATION_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# 昵称长度边界（按 Unicode **码点**计，Python len() 即码点数，emoji 记 1）。
# 下限 2：单字名既没有识别度也极易撞车；上限 24：评论列表/通知里放得下，
# 再长就会被截断得看不出原样。
NICKNAME_MIN_LENGTH = max(1, int(_env("NICKNAME_MIN_LENGTH", "2")))
NICKNAME_MAX_LENGTH = max(2, int(_env("NICKNAME_MAX_LENGTH", "24")))
# 词库最短长度（按**每个归一化形态**的字符数计）。与评论同一理由：
# 1 字词在正常表达里遍地都是。对每个形态都把关（而非只 base）是因为
# 折叠形态天然更短（"约约"压缩成"约"），放任它会把任何含"约"的昵称
# 都算成命中"约约"。
NICKNAME_MIN_WORD_LEN = max(1, int(_env("NICKNAME_MIN_WORD_LEN", "2")))
# 词库缓存有效期（秒）：与 MODERATION_CACHE_TTL_SECONDS 同一机制——
# 到期才回库查一次指纹，**校验本身永不查库**。管理端改词主动失效本进程缓存
# （保存即生效），TTL 只是集群下其他实例的兜底。
NICKNAME_CACHE_TTL_SECONDS = max(0, int(_env("NICKNAME_CACHE_TTL_SECONDS", "30")))
# 词库规模上限（条，含每条词的别名在各形态下的展开前的词条数）。
NICKNAME_MAX_WORDS = max(100, int(_env("NICKNAME_MAX_WORDS", "20000")))
# 是否启用「重复字符压缩」形态（"aabbcc" → "abc"）。昵称无语义、长度有限，
# 压缩的误杀面远比评论正文小，故默认开；若出现误杀可关掉这一形态
# （其余形态——大小写/全半角/符号/数字混淆/繁简——不受影响）。
NICKNAME_COLLAPSE_REPEATS = _env("NICKNAME_COLLAPSE_REPEATS", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# 昵称允许的**符号**白名单（字母/数字/汉字另按 Unicode L*/N* 放行）。
# 空格不在这里：`_env` 会 strip 整个值，尾随空格在环境变量里活不下来，
# 故空格由代码额外允许（首尾空白一律 trim，只放行内部空格）。
NICKNAME_ALLOWED_SYMBOLS = _env("NICKNAME_ALLOWED_SYMBOLS", "_-·.・")

# ---- 命中日志留存（C7，合规）----
# `sensitive_hits` / `nickname_hits` 的 `snippet` 存的是**用户原文**（脱敏不可逆，
# 它是误杀复盘与申诉的唯一证据）。留存必须有期限：无限期保存用户原话是合规风险，
# 而日志的价值只在"最近发生的误杀申诉"。到点即删，**不设保底条数**。
# `0` = 不清理（仅取证期临时使用，不建议长期如此）。
HIT_LOG_KEEP_DAYS = max(0, int(_env("HIT_LOG_KEEP_DAYS", "90")))
