"""TTL 缓存：进程内 / Redis 双实现（B3 的基础设施）。

背景：`for-you` 排序每请求都要算频道偏好（两次 JOIN + GROUP BY），
而一次列表翻页可能打十几个请求——同一秒内算十几次完全相同的聚合。
缓存是这里最直接的一刀，顺带也是 D6（Redis 二级缓存）的第一块砖。

设计取舍（与 `ratelimit` 同一套口径，不另立规矩）：
- **双实现**：有 `REDIS_URL` 走 Redis（多 worker 共享），否则进程内字典。
  Redis 可用性探测失败时**降级到进程内**并只告警一次——
  缓存不可用只是慢，不能让请求 500。
- **任何异常都不许冒泡**：读失败 = 未命中（重算），写失败 = 不缓存。
  缓存是这个系统里**唯一**可以随时失效的组件，它出错必须无感。
- **`CACHE_ENABLED` 在调用时读 `config` 的模块属性**：值一旦被 import 到
  本模块就固定了，测试 monkeypatch 就不生效（与 `deps.require_ops_access`
  读 `config.OPS_TOKEN` 同理）。测试环境在 conftest 里钉 false。
- **值必须可 JSON 序列化**：Redis 侧存的是字符串，内存侧直接存对象，
  所以约定调用方只塞 dict/list/标量（当前只有 `dict[str, float]`）。
"""
import json
import logging
import threading
import time
from typing import Any, Optional

from . import config

logger = logging.getLogger("toutiao.cache")

#: Redis 键前缀。与 `ratelimit` 的 `rl:` 一样不能与别的模块重名——
#: 两个组件共用同一个 Redis 键空间，前缀撞了会互相污染。
_PREFIX = "cache"


class TTLCache:
    """进程内 TTL 缓存（线程安全；容量上限防「无限增长的 user_id」）。"""

    def __init__(self, default_ttl: int = 300, max_keys: int = 5000) -> None:
        self.default_ttl = default_ttl
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        if not config.CACHE_ENABLED:
            return None
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            expire_at, value = hit
            if expire_at <= now:
                # 惰性过期：不做后台清扫，命中时才判断（读多写少，够用）
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if not config.CACHE_ENABLED:
            return
        now = time.time()
        with self._lock:
            if len(self._data) >= self.max_keys:
                # 容量兜底：优先丢弃已过期的，再不行丢最早写入的一条。
                # 频道偏好这类键是 per-user 的，用户数无上限——不设闸迟早撑爆内存。
                expired = [k for k, (exp, _) in self._data.items() if exp <= now]
                for k in expired:
                    self._data.pop(k, None)
                if len(self._data) >= self.max_keys:
                    oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                    self._data.pop(oldest, None)
            self._data[key] = (now + (ttl if ttl is not None else self.default_ttl), value)

    def delete(self, key: str) -> None:
        if not config.CACHE_ENABLED:
            return
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        """清空（测试与运维用）。"""
        with self._lock:
            self._data.clear()


class RedisTTLCache:
    """Redis TTL 缓存。任何 Redis 异常都降级为「未命中」，不向上抛。"""

    def __init__(self, url: str, default_ttl: int = 300) -> None:
        import redis  # 延迟导入：未安装/未启用时不影响内存实现

        self.default_ttl = default_ttl
        self._local = TTLCache(default_ttl)
        self._client = redis.Redis.from_url(
            url, socket_connect_timeout=1, socket_timeout=1
        )

    def get(self, key: str) -> Optional[Any]:
        if not config.CACHE_ENABLED:
            return None
        try:
            raw = self._client.get(f"{_PREFIX}:{key}")
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:  # noqa: BLE001 —— 缓存异常一律按未命中处理
            return self._local.get(key)

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if not config.CACHE_ENABLED:
            return
        try:
            self._client.setex(
                f"{_PREFIX}:{key}",
                ttl if ttl is not None else self.default_ttl,
                json.dumps(value, ensure_ascii=False),
            )
        except Exception:  # noqa: BLE001
            self._local.set(key, value, ttl)

    def delete(self, key: str) -> None:
        if not config.CACHE_ENABLED:
            return
        try:
            self._client.delete(f"{_PREFIX}:{key}")
        except Exception:  # noqa: BLE001
            self._local.delete(key)
        # 本地副本也删：Redis 不可用时可能刚降级写过一份，留着就是陈旧数据
        self._local.delete(key)


def build_cache(default_ttl: int, redis_url: str = ""):
    """按配置选实现；Redis 连不上时降级到进程内并只告警一次。"""
    if not redis_url:
        return TTLCache(default_ttl)
    try:
        cache = RedisTTLCache(redis_url, default_ttl)
        cache._client.ping()  # noqa: SLF001 —— 构造期探活，失败即降级
        return cache
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "缓存后端不可用（%s），已降级为进程内缓存：多实例间不共享，"
            "功能不受影响，仅性能下降",
            exc,
        )
        return TTLCache(default_ttl)