"""明日头条 API 服务入口。

开发：ENVIRONMENT=development（默认，放开 Swagger / CORS *）
生产：ENVIRONMENT=production
    - 必须设置 JWT_SECRET，否则启动即失败（fail-fast）
    - 关闭 Swagger / ReDoc / OpenAPI
    - CORS 默认同源（需显式配置 CORS_ORIGINS）
    - 开启接口限流与访问日志
    - 演示账号默认不播种（SEED_DEMO_ACCOUNT=false）

启动：
    uvicorn main:app --reload --port 8000
"""
import ipaddress
import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.config import (
    ADMIN_PHONES,
    CORS_ORIGINS,
    DOCS_URL,
    FEED_CRON_ENABLED,
    FEED_CRON_INTERVAL_MINUTES,
    IS_PRODUCTION,
    JWT_SECRET_IS_DEFAULT,
    LOG_LEVEL,
    LOGIN_RATE_LIMIT_PER_MINUTE,
    OPENAPI_URL,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_WINDOW_SECONDS,
    REDIS_URL,
    REDOC_URL,
    TRUSTED_PROXIES,
)
from app.database import get_db, pool_status
from app.deps import get_current_user
from app.models import User
from app.ratelimit import build_limiter
from app.routers import admin, auth, comments, me, messages, news
from app.scheduler import start_scheduler, stop_scheduler
from app.seed import init_db

# 生产环境强制校验：默认密钥必须拒绝启动
if IS_PRODUCTION and JWT_SECRET_IS_DEFAULT:
    raise RuntimeError(
        "[安全] 生产环境必须设置 JWT_SECRET 环境变量（当前仍是默认值）。\n"
        "生成方式：python -c \"import secrets;print(secrets.token_urlsafe(48))\""
    )

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("toutiao.api")


# ---- 受信代理网段（仅当直连来源命中才采信 X-Forwarded-For）----
def _parse_networks(entries: list[str]) -> list:
    nets = []
    for e in entries:
        try:
            nets.append(ipaddress.ip_network(e, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXIES 条目非法，已忽略: %s", e)
    return nets


_TRUSTED_NETS = _parse_networks(TRUSTED_PROXIES)


def _is_trusted(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_NETS)


def client_ip(request: Request) -> str:
    """取客户端 IP。

    仅当**直连对端**位于受信代理网段内时，才采信 X-Forwarded-For 首段；
    否则一律使用 socket 对端地址——避免任意客户端伪造 XFF 绕过限流。
    """
    direct = request.client.host if request.client else "unknown"
    if _TRUSTED_NETS and _is_trusted(direct):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return direct


def break_glass_missing() -> bool:
    """生产环境是否**未配置**应急通道（`ADMIN_PHONES` 为空）。

    只告警、不阻断启动：库白名单（`admin_users`）本身是完整的日常通道，
    缺的只是"数据库不可用时仍能进后台"的那条路——DB 故障 / 误删行 / 迁移回滚
    都可能让库白名单整体失效，届时 env 是唯一入口。
    """
    return IS_PRODUCTION and not ADMIN_PHONES


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动：建表 + 启动定时抓取；关停：停调度 + 释放连接池。"""
    init_db()
    await start_scheduler()
    if break_glass_missing():
        logger.warning(
            "[运维提醒] 生产环境未配置 ADMIN_PHONES，缺少应急通道："
            "一旦 admin_users 表不可用（DB 故障 / 行被误删 / 迁移回滚），"
            "管理后台将无人可进。建议至少保留一条 env 管理员。"
        )
    logger.info(
        "启动完成 | env=%s | docs=%s | rate_limit=%s(%d/min, login %d/min) | "
        "cron=%s(%d分钟) | cors=%s | trusted_proxies=%s | admins=%d",
        "production" if IS_PRODUCTION else "development",
        "关闭" if not DOCS_URL else DOCS_URL,
        "on" if RATE_LIMIT_ENABLED else "off",
        RATE_LIMIT_PER_MINUTE,
        LOGIN_RATE_LIMIT_PER_MINUTE,
        "on" if FEED_CRON_ENABLED else "off",
        FEED_CRON_INTERVAL_MINUTES,
        CORS_ORIGINS or "同源",
        len(_TRUSTED_NETS) or "无",
        len(ADMIN_PHONES),
    )
    yield
    await stop_scheduler()


app = FastAPI(
    title="明日头条 API",
    version="1.0.0",
    description="为「明日头条」Vue 前端提供资讯 / 评论 / 认证 / 消息接口。",
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
    openapi_url=OPENAPI_URL,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(news.router)
app.include_router(comments.router)
app.include_router(messages.router)
app.include_router(me.router)
app.include_router(admin.router)

limiter = build_limiter(RATE_LIMIT_WINDOW_SECONDS, REDIS_URL)

# ---- 轻量进程内指标（供 /api/metrics 暴露；多实例需在网关侧聚合）----
_METRICS_STARTED_AT = time.time()
_metrics: dict = {
    "requests_total": 0,
    "inflight": 0,
    "by_status": defaultdict(int),
    "by_path": defaultdict(int),
}


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """访问日志：方法 / 路径 / 状态码 / 耗时，并附带请求 ID。"""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    _metrics["inflight"] += 1
    try:
        response = await call_next(request)
    except Exception:
        cost = (time.perf_counter() - started) * 1000
        logger.exception(
            "[%s] %s %s -> 500 (%.1fms)", request_id, request.method, request.url.path, cost
        )
        raise
    finally:
        _metrics["inflight"] -= 1

    cost = (time.perf_counter() - started) * 1000
    _metrics["requests_total"] += 1
    _metrics["by_status"][response.status_code] += 1
    _metrics["by_path"][request.url.path] += 1

    response.headers["X-Process-Time-Ms"] = f"{cost:.1f}"
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "[%s] %s %s -> %s (%.1fms) ip=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        cost,
        client_ip(request),
    )
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """按客户端 IP 限流。

    键为纯 IP（登录类为 `login:<ip>`），**不按 path 分桶**——否则攻击者可
    通过轮换路径绕过总量限制；登录单独一类阈值更严。
    """
    if not RATE_LIMIT_ENABLED:
        return await call_next(request)

    path = request.url.path
    ip = client_ip(request)
    if path.startswith("/api/auth/login"):
        limit, code, key = LOGIN_RATE_LIMIT_PER_MINUTE, 42901, f"login:{ip}"
    else:
        limit, code, key = RATE_LIMIT_PER_MINUTE, 42900, ip

    allowed, remaining = limiter.allow(key, limit)
    if not allowed:
        logger.warning("限流触发 %s %s ip=%s", request.method, path, ip)
        return JSONResponse(
            status_code=429,
            content={"code": code, "message": "请求过于频繁，请稍后再试", "data": None},
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """把 HTTPException 统一成 { code, message, data } 约定。"""
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        code, message = detail["code"], detail.get("message", "")
    else:
        code = {401: 40100, 403: 40300, 404: 40400, 400: 40000}.get(exc.status_code, exc.status_code)
        message = detail if isinstance(detail, str) else "请求失败"
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "message": message, "data": None},
    )


def _jsonable_errors(errors: list) -> list:
    """把 Pydantic v2 的错误列表转成可 JSON 序列化的结构。

    Pydantic v2 在自定义 validator 抛 `ValueError` 时，会把**异常对象本身**塞进
    `ctx.error`；原样交给 `JSONResponse` 会 `TypeError: Object of type ValueError
    is not JSON serializable`——一个本该返回 422 的请求就此变成 500。
    故只保留字段路径/类型/消息，并把 ctx 里的异常对象换成其字符串形式。
    """
    out: list[dict] = []
    for e in errors:
        ctx = e.get("ctx") or {}
        clean_ctx = {k: (str(v) if isinstance(v, BaseException) else v) for k, v in ctx.items()}
        item = {
            "loc": list(e.get("loc", [])),
            "msg": e.get("msg", ""),
            "type": e.get("type", ""),
        }
        if clean_ctx:
            item["ctx"] = clean_ctx
        out.append(item)
    return out


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数校验失败（422）也纳入统一契约，避免前端拿到 FastAPI 默认结构。"""
    errors = _jsonable_errors(exc.errors())
    first = errors[0] if errors else {}
    field = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    message = f"参数校验失败：{field} {first.get('msg', '')}".strip()
    return JSONResponse(
        status_code=422,
        content={"code": 42200, "message": message, "data": {"errors": errors}},
    )


# 启动/关停逻辑已统一收敛到 lifespan（见文件上方），避免 on_event 弃用告警


@app.get("/api/health", response_model=None)
def health():
    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "message": "ok",
            "data": {
                "status": "up",
                "pool": pool_status(),
                # 应急通道是否配置：只下发布尔值，不下发号码。
                # 库白名单可用但 env 为空时，DB 故障/误删/迁移回滚会让后台彻底进不去。
                "adminBreakGlass": bool(ADMIN_PHONES),
            },
        },
    )


@app.get("/api/metrics", response_model=None)
def metrics():
    """Prometheus 文本格式指标（进程内；多实例请在网关侧聚合）。"""
    uptime = time.time() - _METRICS_STARTED_AT
    pool = pool_status()
    lines = [
        "# HELP toutiao_uptime_seconds 进程运行时长",
        "# TYPE toutiao_uptime_seconds gauge",
        f"toutiao_uptime_seconds {uptime:.0f}",
        "# HELP toutiao_requests_total 累计请求数",
        "# TYPE toutiao_requests_total counter",
        f"toutiao_requests_total {_metrics['requests_total']}",
        "# HELP toutiao_requests_inflight 处理中请求数",
        "# TYPE toutiao_requests_inflight gauge",
        f"toutiao_requests_inflight {_metrics['inflight']}",
    ]
    for status, n in sorted(_metrics["by_status"].items()):
        lines.append(f'toutiao_responses_total{{status="{status}"}} {n}')
    for path, n in sorted(_metrics["by_path"].items(), key=lambda kv: -kv[1])[:20]:
        lines.append(f'toutiao_requests_by_path_total{{path="{path}"}} {n}')
    for k, v in pool.items():
        if isinstance(v, (int, float)):
            lines.append(f'toutiao_db_pool_{k} {v}')
    # 源健康（P1-16）：抓取链路的排障入口，-1 表示查询失败（不要假装成"全部健康"）
    lines.append("# HELP toutiao_sources_unhealthy 异常或超时未成功抓取的信源数")
    lines.append("# TYPE toutiao_sources_unhealthy gauge")
    try:
        from app.database import SessionLocal

        from app.feeds.alert import unhealthy_count

        with SessionLocal() as db:
            lines.append(f"toutiao_sources_unhealthy {unhealthy_count(db)}")
    except Exception as e:  # 指标不该因 DB 抖动而 500
        logger.warning("源健康指标采集失败: %s", e)
        lines.append("toutiao_sources_unhealthy -1")
    return PlainTextResponse("\n".join(lines) + "\n")


def _run_refresh(channel: str | None, keyword: str | None) -> None:
    """后台执行一次抓取（独立会话，不阻塞请求线程）。"""
    from app.database import SessionLocal
    from app.feeds.ingest import prune_old_news, run_ingest

    try:
        with SessionLocal() as db:
            report = run_ingest(db, channel=channel, keyword=keyword)
            pruned = prune_old_news(db)
        logger.info(
            "手动抓取完成：抓取 %d / 新增 %d / 更新 %d / 清理 %d",
            report.fetched,
            report.inserted,
            report.updated,
            pruned,
        )
    except Exception as e:  # 后台任务异常不得影响进程
        logger.exception("手动抓取失败: %s", e)


@app.post("/api/news/refresh", status_code=202, response_model=None)
def refresh_news(
    background: BackgroundTasks,
    channel: str | None = None,
    keyword: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动触发一次抓取（需管理员，异步执行）。

    - 鉴权：需登录且具备管理能力（`ADMIN_PHONES` ∪ `admin_users` 表）；都为空则禁用。
    - 执行：投递到后台任务，立即返回 202，不阻塞请求。
    """
    # 复用 admin 模块的判定，避免两处各写一遍而漂移
    if not admin.is_admin(user.phone, db):
        raise HTTPException(status_code=403, detail={"code": 40301, "message": "无权限执行该操作"})
    background.add_task(_run_refresh, channel, keyword)
    return JSONResponse(
        status_code=202,
        content={"code": 0, "message": "已开始后台抓取", "data": {"accepted": True}},
    )
