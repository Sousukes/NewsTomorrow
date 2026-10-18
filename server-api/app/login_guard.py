"""登录失败计数与账号临时锁定（C2）。

背景：登录限流此前只按 IP（`login:<ip>`，见 `main.py` 中间件）。它防得住
「一个 IP 猛刷」，防不住「换 IP 打同一个账号」——每个 IP 都是首次访问，
IP 维度干干净净，而受害账号被分布式爆破。账号维度的失败计数补这一面。

**scope（C10 起）**：同一套计数也被「高敏操作二次校验」复用——否则
`/auth/step-up` 本身就是一个没有防爆破的密码 oracle（猜对一次就能执行
删源/授权管理员）。按 scope 分桶（`login_fail:` / `stepup:`）：二次校验失败
不该把用户的登录也锁掉（那是两件事），但**阈值与窗口共用**一套配置。

设计取舍（每条都对应一种攻击面或误伤面）：
- **键是手机号，与 IP 无关**：锁定判定发生在查库/验密**之前**——
  已锁账号连 pbkdf2 都不算，锁定期间的请求成本趋近于零；
  同时「继续拿错误密码刷」不会给锁定期续期（预检只窥探、不计次）。
- **不存在的账号同样计数**：锁定检查先于用户查询，响应只有
  「42902 已锁」与「40101 密码错」两种。若只锁已存在账号，
  「同一手机号有时 429 有时 401」就成了账号枚举 oracle。
- **成功即清零**：失败计数只反映「连续」失败。不清零的话，
  5 次手滑 + 1 次成功 + 1 次手滑 = 锁定，而用户明明证明过自己。
- **阈值与窗口走配置**（默认 5 次 / 15 分钟，`LOGIN_FAIL_LIMIT` /
  `LOGIN_FAIL_WINDOW_SECONDS`）：给真人足够的手滑空间，又把爆破速度
  压到 5 次/15 分钟。代价是持正确密码的用户可能被攻击者故意锁
  15 分钟（账号锁定的固有取舍）——所以阈值不宜再低。
- **跟随 `RATE_LIMIT_ENABLED` 总开关**：与 IP 限流同一个开关，
  测试环境（根 conftest 钉 false）不受失败计数影响，避免用例间互相锁。
- **与 IP 限流同款双实现**：进程内 / Redis 共享计数（多 worker 一致），
  存储不可用时按既有口径放行。两实现的窗口语义差异（内存是滑动、
  Redis 是切片）与 `ratelimit` 模块一致，此处不重复造。
"""
import logging

from . import config
from .ratelimit import build_limiter

logger = logging.getLogger("toutiao.login_guard")

# 窗口独立于 IP 限流（15 分钟 vs 1 分钟），故独立实例
_limiter = build_limiter(config.LOGIN_FAIL_WINDOW_SECONDS, config.REDIS_URL)

#: scope → Redis/内存键前缀。刻意与 IP 限流的 `login:<ip>` 不同名：
#: 两个限流器共用同一个 Redis 键空间，同前缀会互相污染。
_SCOPES = {"login": "login_fail", "stepup": "stepup"}
#: scope → 面向用户的文案主语（锁定时插在「失败次数过多」前）
_SCOPE_LABEL = {"login": "登录", "stepup": "二次校验"}


def _key(phone: str, scope: str = "login") -> str:
    return f"{_SCOPES.get(scope, scope)}:{phone}"


def _minutes(retry_after: int) -> int:
    """向上取整到分钟（至少 1）：配置改窗口时文案自动跟随，不写死「15 分钟」。"""
    return max(1, round(retry_after / 60))


def lock_detail(phone: str, scope: str = "login") -> dict | None:
    """账号在某个 scope 上是否已被锁定。

    @param scope  `login`（默认，登录）或 `stepup`（二次校验）
    @returns 未锁定返回 None；已锁定返回
             `{"retry_after": 秒, "message": 面向用户的文案}`
    """
    if not config.RATE_LIMIT_ENABLED:
        return None
    if not _limiter.blocked(_key(phone, scope), config.LOGIN_FAIL_LIMIT):
        return None
    retry_after = config.LOGIN_FAIL_WINDOW_SECONDS
    return {
        "retry_after": retry_after,
        "message": (
            f"{_SCOPE_LABEL.get(scope, '登录')}失败次数过多，"
            f"请约 {_minutes(retry_after)} 分钟后再试"
        ),
    }


def record_failure(phone: str, scope: str = "login") -> bool:
    """记一次失败。

    @returns 本次失败是否**刚触发**锁定（计数达到上限）——调用方据此记日志，
             「开始锁」与「持续锁着」是两件事，后者不需要每条都刷日志。
    """
    if not config.RATE_LIMIT_ENABLED:
        return False
    allowed, _ = _limiter.allow(_key(phone, scope), config.LOGIN_FAIL_LIMIT)
    return not allowed


def record_success(phone: str, scope: str = "login") -> None:
    """成功：清零失败计数（只反映「连续」失败）。"""
    if not config.RATE_LIMIT_ENABLED:
        return
    _limiter.reset(_key(phone, scope))
