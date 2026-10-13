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
# 单源抓取条数上限
FEED_LIMIT_PER_SOURCE = int(_env("FEED_LIMIT_PER_SOURCE", "30"))
# HTTP 超时（读/写）与连接超时
FEED_TIMEOUT = float(_env("FEED_TIMEOUT", "15"))
FEED_CONNECT_TIMEOUT = float(_env("FEED_CONNECT_TIMEOUT", "8"))
# 旧数据清理：保留天数与最小保留条数
FEED_KEEP_DAYS = int(_env("FEED_KEEP_DAYS", "30"))
FEED_KEEP_MIN = int(_env("FEED_KEEP_MIN", "200"))