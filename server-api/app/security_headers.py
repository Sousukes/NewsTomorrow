"""安全响应头（C9）。

分工：前端文档的 CSP 在 `mingri-toutiao-vue/src/security/csp.ts`（构建期注入
`index.html`，静态托管也生效）；本模块负责**后端自己的响应**。

API 是纯 JSON——没有文档上下文，就没有任何资源需要加载，任何加载都是异常，
所以它的正确策略是 `default-src 'none'`：即便某个响应被塞进了注入内容，
浏览器也不会执行它里面的脚本或外链（API 响应本不该有，但"本不该有"正是要靠
策略兜住的那类事）。

**为什么只给 `/api/*` 上 CSP**：`/docs`（Swagger UI）依赖 CDN 脚本与内联脚本，
套上 `default-src 'none'` 会直接打不开。docs 只在 development 暴露
（`config.DOCS_URL` 在生产为 None），不为它放宽策略即可——非 `/api` 路径
仍拿到 nosniff / Referrer-Policy / X-Frame-Options。
"""
from . import config

#: API 响应策略：无文档上下文 → 不加载任何资源、不允许被嵌套
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

#: 与安全无关但常被一起要求的基础头
BASE_HEADERS: dict[str, str] = {
    # 关闭 MIME 嗅探：否则一个"看起来像 HTML"的 JSON 响应可能被当页面解析
    "X-Content-Type-Options": "nosniff",
    # 不外泄来源 URL（接口路径本身带业务语义）
    "Referrer-Policy": "no-referrer",
    # 不允许被嵌套（与 CSP frame-ancestors 互补，覆盖老浏览器）
    "X-Frame-Options": "DENY",
}


def apply_security_headers(response, path: str) -> None:
    """给响应附加安全头。

    `path` 只用到「是否 `/api/` 前缀」这一层判断：再细就变成按端点维护策略，
    那是漂移的起点。

    刻意读 `config.SECURITY_HEADERS_ENABLED` 的**模块属性**（而不是
    `from .config import SECURITY_HEADERS_ENABLED`）：值一旦 import 到本模块
    就固定了，测试无法 monkeypatch 覆盖两个分支（与 `admin.is_admin` 读
    `ADMIN_PHONES`、`deps.require_ops_access` 读 `config.OPS_TOKEN` 同理）。
    """
    if not config.SECURITY_HEADERS_ENABLED:
        return
    for name, value in BASE_HEADERS.items():
        response.headers.setdefault(name, value)
    if path.startswith("/api/"):
        response.headers["Content-Security-Policy"] = API_CSP
