"""限流器实现：进程内固定窗口（默认）与 Redis 共享固定窗口（多实例）。

注意：进程内实现多 worker / 多实例时每个进程各算各的；
需要全局一致时设置 `REDIS_URL`，由 `build_limiter()` 自动切换。
"""
import logging
import time
from collections import defaultdict, deque
from threading import Lock

logger = logging.getLogger("toutiao.ratelimit")


class FixedWindowLimiter:
    def __init__(self, window_seconds: int = 60) -> None:
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()
        self._last_sweep = time.time()

    def allow(self, key: str, limit: int) -> tuple[bool, int]:
        """返回 (是否放行, 剩余配额)。"""
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= limit:
                return False, 0
            q.append(now)
            remaining = limit - len(q)
            self._maybe_sweep(now)
            return True, remaining

    def blocked(self, key: str, limit: int) -> bool:
        """计数是否已达上限——**只窥探、不计次**。

        与 `allow` 的分工：`allow` 既计数又判定，用于「记一次」；
        本方法只判定，用于「已满额的预检」——若预检也用 `allow`，
        每个被拒的请求都会给计数器 +1，把窗口期无限续期
        （登录失败锁定的锁定判定就属于这类预检，见 `login_guard`）。
        """
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            return len(q) >= limit

    def reset(self, key: str) -> None:
        """清除计数（用于「成功即清零」类语义：失败计数只反映连续失败）。"""
        with self._lock:
            self._hits.pop(key, None)

    def _maybe_sweep(self, now: float) -> None:
        """定期清理过期 key，避免内存无限增长。"""
        if now - self._last_sweep < self.window:
            return
        self._last_sweep = now
        stale = [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]
        for k in stale:
            self._hits.pop(k, None)


class RedisFixedWindowLimiter:
    """基于 Redis 的固定窗口计数（多实例共享）。

    计数键按窗口切片：`rl:<key>:<floor(now/window)>`，`INCR` + `EXPIRE` 原子完成。
    存储不可用时**放行**（fail-open）——避免限流组件故障拖垮整个服务。
    """

    def __init__(self, window_seconds: int = 60, url: str = "") -> None:
        import redis  # 延迟导入：未安装/未启用时不影响内存实现

        self.window = window_seconds
        self._client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        # 立即探活，尽早暴露配置错误（由 build_limiter 捕获后回退）
        self._client.ping()

    def allow(self, key: str, limit: int) -> tuple[bool, int]:
        bucket = int(time.time() // self.window)
        redis_key = f"rl:{key}:{bucket}"
        try:
            pipe = self._client.pipeline()
            pipe.incr(redis_key, 1)
            pipe.expire(redis_key, self.window)
            count = int(pipe.execute()[0])
        except Exception as e:
            logger.warning("Redis 限流异常，本次放行：%s", e)
            return True, limit
        return count <= limit, max(limit - count, 0)

    def blocked(self, key: str, limit: int) -> bool:
        """计数是否已达上限——只读当前窗口切片，**不计数**（与内存实现同语义）。"""
        bucket = int(time.time() // self.window)
        try:
            count = int(self._client.get(f"rl:{key}:{bucket}") or 0)
        except Exception as e:
            logger.warning("Redis 限流异常，本次按未锁定放行：%s", e)
            return False
        return count >= limit

    def reset(self, key: str) -> None:
        """清除当前窗口切片的计数（成功即清零；上一切片靠 EXPIRE 自然过期）。"""
        bucket = int(time.time() // self.window)
        try:
            self._client.delete(f"rl:{key}:{bucket}")
        except Exception as e:
            logger.warning("Redis 限流异常，重置失败（不影响本次业务）：%s", e)


def build_limiter(window_seconds: int = 60, redis_url: str = ""):
    """按配置选择限流实现；Redis 不可用时回退进程内实现。

    回退会记 WARNING：配置了 REDIS_URL 却回退，意味着多实例限流实际失效，
    不能静默处理，否则运维会误以为已启用共享限流。
    """
    if redis_url:
        try:
            limiter = RedisFixedWindowLimiter(window_seconds, redis_url)
            logger.info("限流使用 Redis 共享实现")
            return limiter
        except Exception as e:
            logger.warning(
                "已配置 REDIS_URL 但 Redis 限流不可用，回退进程内实现"
                "（多实例限流将不一致，请检查 redis 依赖与连接）：%s",
                e,
            )
    return FixedWindowLimiter(window_seconds)