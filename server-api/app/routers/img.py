"""资讯配图代理（B4）。

为什么需要它：资讯配图此前由浏览器**直连源站**，于是有三件事同时不成立：
1. **源站一挂就裂图**——图存在源站，我们只存了 URL；
2. **CSP 的 `img-src` 收不紧**——必须放行 `https:` 才装得下任意外站图，
   而 CSP 恰恰挡不住 `<img src=https://evil/x>` 这类**外泄型载荷**
   （referer 泄露、用户 IP 与浏览行为暴露给几十个无关站点）；
3. 没有尺寸协商，同一张 2000px 的封面在小卡片里照原样下载。

本模块把图收进自己的源站：`GET /api/img?url=<原地址>&w=<目标宽度>`。

**它同时是一个教科书式的 SSRF 攻击面**，所以这里的防线是本文件的主体，
功能只是顺带的事：

- **协议白名单**：仅 http/https（挡 `file://`、`gopher://`、`data:`）；
- **拒绝一切非公网 IP**：DNS 解析后逐条检查，只要有一条 A/AAAA 落在
  私有网段 / 回环 / **链路本地（169.254.169.254 是云元数据）** / 组播 / 保留段
  即拒绝。刻意"任一条不合法就整体拒绝"而不是"用第一条"——
  攻击者给域名配多个 A 记录，正是为了让你解析到内网那条；
- **跳转逐跳复检**：`follow_redirects=False` + 手动跟随（≤3 跳），
  每一跳都重新跑一遍上面两条。外站 302 到内网是最常见的绕过手法；
- **体积上限 + 超时**：超过 5MB 直接拒，避免内存炸弹；
- **只放行图片 Content-Type**：代理与站点**同源**，若能把任意 HTML/JS 以
  同源发出去，就等于开了一个存储型 XSS 入口。`nosniff` + `Content-Disposition`
  是同一道防线的第二层；
- **缓存键固定为 (原 URL, 宽度)**，且宽度只能取白名单里的值——
  否则任意 `w` 就是把缓存撑爆的入口。

**已知边界（诚实写在这里）**：DNS 解析与 httpx 建连之间存在 TOCTOU 窗口
（若能在这两个时刻之间改 DNS 记录，仍可能绕过 IP 检查）。彻底消除需要自定义
transport 把连接钉到已校验的 IP 上，代价是复杂度与兼容性；本项目当前不接受，
改以「仅供资讯配图、上游为已抓取过的 RSS 源」来降低暴露面。

**缩略图 / WebP**：装了 Pillow 才生效（`try: import PIL`），否则原样透传。
它是 native 依赖，**刻意不进 requirements.txt**（部署方显式决定，见
`requirements-optional.txt`）——未安装时代理功能不受影响，只是少了尺寸协商。
"""
import io
import ipaddress
import logging
import socket
import time
from collections import OrderedDict
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from .. import config

router = APIRouter(prefix="/api", tags=["img"])

logger = logging.getLogger("toutiao.img")

ALLOWED_SCHEMES = ("http", "https")
#: 只放行常见位图。**刻意不含 svg**：SVG 可内嵌脚本，虽在 <img> 里不执行，
#: 但用户直接访问代理地址就会在本源下拿到一份可执行的 SVG。
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/bmp",
    }
)
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 8.0
#: 允许的宽度白名单（0 = 不做变换）：只认这几个值
ALLOWED_WIDTHS = (0, 200, 320, 480, 640, 960, 1280)
IMAGE_CACHE_TTL = 24 * 3600

# ---- 字节缓存（LRU，按字节封顶；进程内，多实例不共享，与 ratelimit 同理）----
_MAX_CACHE_BYTES = 64 * 1024 * 1024
_MAX_CACHE_ENTRIES = 400
_cache: "OrderedDict[str, tuple[float, str, bytes]]" = OrderedDict()
_cache_bytes = 0

try:  # Pillow 是可选 native 依赖：没装就只做代理
    from PIL import Image  # type: ignore

    _HAS_PILLOW = True
except ImportError:  # pragma: no cover —— 取决于部署环境
    Image = None  # type: ignore
    _HAS_PILLOW = False


# ---------------------------------------------------------------- SSRF 防线


def _reject(status: int, code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _ip_is_public(addr: str) -> bool:
    """单个 IP 是否属于公网可路由范围。"""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 —— 含云厂商元数据地址
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _assert_fetchable(raw: str) -> str:
    """校验并返回 URL；不合法直接抛 HTTP 错误。

    每一跳跳转都要重新过这一遍——只查入口 URL 等于给 302 绕过留门。
    """
    url = (raw or "").strip()
    if not url:
        raise _reject(422, 42206, "缺少图片地址")
    try:
        parsed = urlparse(url)
    except ValueError:
        raise _reject(400, 40008, "图片地址无法解析") from None
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise _reject(400, 40008, "只支持 http/https 地址")
    host = parsed.hostname or ""
    if not host:
        raise _reject(400, 40008, "图片地址缺少主机名")
    if host.lower() in ("localhost", "localhost.localdomain"):
        raise _reject(400, 40008, "不允许访问本机地址")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        # 解析失败一律按「不可达」处理：宁可少一张图，也不要在解析异常时放行
        raise _reject(400, 40008, "图片地址无法解析") from None
    for family, _type, _proto, _canon, sockaddr in infos:
        if not _ip_is_public(sockaddr[0]):
            logger.warning("图片代理拒绝内网目标：%s -> %s", host, sockaddr[0])
            raise _reject(400, 40008, "不允许访问内网地址")
        _ = family
    return url


# ---------------------------------------------------------------- 变换


def _build_client() -> httpx.Client:
    """构造上游客户端。

    单独抽成函数是给测试留接缝：测试要打桩"回源"这一步，而
    `httpx.Client.get` 不能直接打桩——FastAPI 的 `TestClient` 本身就是
    `httpx.Client` 的子类，一打桩连测试自己的请求都会被打掉。
    """
    return httpx.Client(
        follow_redirects=False,  # 手动跟随：每跳都要复检
        timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
        headers={"User-Agent": "toutiao-img-proxy/1.0"},
    )


def _maybe_resize(content: bytes, width: int) -> tuple[bytes, str]:
    """按目标宽度缩放并转 WebP；没有 Pillow 或不需要变换时原样返回。"""
    if not width or not _HAS_PILLOW:
        return content, ""
    try:
        with Image.open(io.BytesIO(content)) as im:
            im.load()
            if im.width <= width:
                return content, ""
            ratio = width / im.width
            resized = im.resize((width, max(1, round(im.height * ratio))))
            if resized.mode not in ("RGB", "RGBA"):
                resized = resized.convert("RGB")
            buf = io.BytesIO()
            resized.save(buf, format="WEBP", quality=82, method=4)
            return buf.getvalue(), "image/webp"
    except Exception as exc:  # noqa: BLE001 —— 变换失败不该让图挂掉
        logger.info("缩略图变换失败，原样透传：%s", exc)
        return content, ""


# ---------------------------------------------------------------- 缓存


def _cache_get(key: str) -> Optional[tuple[str, bytes]]:
    hit = _cache.get(key)
    if hit is None:
        return None
    expire_at, content_type, data = hit
    if expire_at <= time.time():
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return content_type, data


def _cache_put(key: str, content_type: str, data: bytes) -> None:
    global _cache_bytes
    if len(data) > _MAX_CACHE_BYTES:
        return  # 单张就超过整个缓存预算，不入缓存
    _cache[key] = (time.time() + IMAGE_CACHE_TTL, content_type, data)
    _cache_bytes += len(data)
    _cache.move_to_end(key)
    while _cache_bytes > _MAX_CACHE_BYTES or len(_cache) > _MAX_CACHE_ENTRIES:
        _, evicted = _cache.popitem(last=False)
        _cache_bytes -= len(evicted[2])


def _cache_clear() -> None:
    """清空字节缓存（测试与运维用）。"""
    global _cache_bytes
    _cache.clear()
    _cache_bytes = 0


# ---------------------------------------------------------------- 端点


@router.get("/img")
def get_image(
    url: str = Query(..., description="原始图片地址（http/https）"),
    w: int = Query(0, ge=0, le=1280, description="目标宽度；仅接受白名单内的值"),
):
    """按原地址与目标宽度返回图片字节。

    缺 `url` → 42206；协议/主机/内网不合规 → 40008；上游不是图片 → 41501；
    超过体积上限 → 41301；取不到 → 50201。
    """
    if w not in ALLOWED_WIDTHS:
        raise _reject(422, 42206, "w 只接受白名单内的宽度值")

    key = f"{url}|{w}"
    if config.CACHE_ENABLED:
        hit = _cache_get(key)
        if hit is not None:
            return _image_response(hit[0], hit[1])

    target = _assert_fetchable(url)
    content_type = ""
    body = b""
    with _build_client() as client:
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                resp = client.get(target)
            except httpx.HTTPError as exc:
                logger.info("图片抓取失败 %s：%s", target, exc)
                raise _reject(502, 50201, "图片源站不可达") from None
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise _reject(502, 50201, "图片源站返回了无效跳转")
                # 关键：跳转目标重新走一遍完整校验
                target = _assert_fetchable(urljoin(target, location))
                continue
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise _reject(415, 41501, "源站返回的不是图片")
            body = resp.content
            break
        else:
            raise _reject(502, 50201, "图片源站跳转次数过多")

        if len(body) > MAX_BYTES:
            raise _reject(413, 41301, "图片体积超过上限")

    body, out_type = _maybe_resize(body, w)
    out_type = out_type or content_type
    if config.CACHE_ENABLED:
        _cache_put(key, out_type, body)
    return _image_response(out_type, body)


def _image_response(content_type: str, body: bytes) -> Response:
    """成功响应头。

    `nosniff` + `Content-Disposition` 是**第二道**防线：代理与站点同源，
    万一上游返回了 HTML，浏览器也不该把它当文档渲染。
    """
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Cache-Control": f"public, max-age={IMAGE_CACHE_TTL}",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )