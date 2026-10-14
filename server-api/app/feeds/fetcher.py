"""RSS 抓取与字段映射：把各源 XML 统一映射为 News 表字段。

对外只暴露 `RawItem`（已归一化），不泄漏源差异。
"""
import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from ..config import FEED_ROBOTS_TTL_HOURS

logger = logging.getLogger("toutiao.feed")

USER_AGENT = "Mozilla/5.0 (compatible; ToutiaoBot/1.0; +https://example.local/bot)"

# 常见 HTML 标签清理
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# 配图扩展名（用于从 description/img 标签里提图）
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@dataclass
class RawItem:
    """归一化后的抓取结果（字段与 News 表对齐）。"""

    id: str
    title: str
    summary: str = ""
    source: str = ""
    time_text: str = ""
    published_at: datetime | None = None
    url: str = ""
    images: list[str] = field(default_factory=list)
    cover: str = ""
    channel: str = "recommend"
    variant: str = "left-right"
    is_top: bool = False


def _clean(text: str | None) -> str:
    """去标签、解实体、压缩空白。"""
    if not text:
        return ""
    t = _TAG_RE.sub(" ", text)
    t = html.unescape(t)
    return _WS_RE.sub(" ", t).strip()


def _first_text(node: ET.Element, *paths: str) -> str:
    """按多个候选路径取第一个非空文本（不同源命名不一致）。"""
    for p in paths:
        found = node.find(p)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
        # 有些源把内容放在 text 之外
        if found is not None and found.get("href"):
            return found.get("href", "")
    return ""


def _extract_images(*blobs: str) -> list[str]:
    """从描述/正文里提取图片 URL（取前 3 张，符合三图变体）。"""
    urls: list[str] = []
    for blob in blobs:
        if not blob:
            continue
        for m in re.finditer(r'src=["\']([^"\']+)["\']|(https?://[^\s"\'<>\)]+\.(?:jpg|jpeg|png|webp|gif))', blob, re.I):
            u = (m.group(1) or m.group(2) or "").strip()
            if u and u not in urls and any(u.lower().split("?")[0].endswith(e) for e in _IMG_EXT):
                urls.append(u)
        if len(urls) >= 3:
            break
    return urls[:3]


def _pick_variant(images: list[str], has_video: bool) -> str:
    """按素材形态选择卡片变体。"""
    if has_video:
        return "video"
    if len(images) >= 3:
        return "triple"
    if images:
        return "left-right"
    return "text"


def _to_relative_time(dt: datetime | None) -> str:
    """把绝对时间转成前端展示用的相对时间字符串。"""
    if dt is None:
        return ""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    diff = now - dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{secs // 60}分钟前"
    if secs < 86400:
        return f"{secs // 3600}小时前"
    return f"{secs // 86400}天前"


def _parse_time(raw: str) -> datetime | None:
    """兼容 RFC822（RSS 标准）与 ISO8601（部分源）两种时间格式。"""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _stable_id(url: str, title: str) -> str:
    """稳定 id：优先用链接哈希，缺失则用标题哈希（保证多次抓取幂等）。"""
    basis = (url or "").strip() or (title or "").strip()
    return "r_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def _host(url: str, fallback: str) -> str:
    try:
        h = urlparse(url).netloc
        return h[4:] if h.startswith("www.") else h or fallback
    except Exception:
        return fallback


def parse_feed(xml_text: str, source: dict) -> list[RawItem]:
    """解析 RSS 2.0 / Atom，映射为 RawItem 列表。"""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as e:
        logger.warning("XML 解析失败[%s]: %s", source["name"], e)
        return []

    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    out: list[RawItem] = []

    for it in items:
        title = _clean(_first_text(it, "title", "{http://www.w3.org/2005/Atom}title"))
        if not title:
            continue

        link = _first_text(it, "link")
        if not link:
            le = it.find("{http://www.w3.org/2005/Atom}link")
            link = le.get("href", "") if le is not None else ""

        desc_raw = _first_text(it, "description", "{http://www.w3.org/2005/Atom}summary")
        summary = _clean(desc_raw)[:300]
        time_raw = _first_text(
            it, "pubDate", "{http://purl.org/dc/elements/1.1/}date", "{http://www.w3.org/2005/Atom}published"
        )
        published = _parse_time(time_raw)
        images = _extract_images(desc_raw, _first_text(it, "{http://purl.org/rss/1.0/modules/content/}encoded"))
        has_video = bool(re.search(r"\.mp4|\.m3u8", desc_raw or "", re.I))

        out.append(
            RawItem(
                id=_stable_id(link, title),
                title=title[:255],
                summary=summary,
                source=source["name"],
                time_text=_to_relative_time(published),
                published_at=published,
                url=link,
                images=images,
                cover=images[0] if images else "",
                channel=source["channel"],
                variant=_pick_variant(images, has_video),
            )
        )
    return out


# ---- robots.txt（P1-16 礼貌抓取）----
# 按 host 缓存解析结果，避免每轮每源都去取一次 robots.txt（那本身就是不礼貌的）。
# 值为 `RobotFileParser | None`：None 表示"取不到"，按放行处理。
_ROBOTS_CACHE: dict[str, tuple[float, object]] = {}
# TTL 走配置，避免"改了 env 却不生效"这类假开关
ROBOTS_TTL_SECONDS = FEED_ROBOTS_TTL_HOURS * 3600


def _robots_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/robots.txt"


def robots_check(client, url: str, user_agent: str = USER_AGENT) -> tuple[bool, float | None, str]:
    """查询是否允许抓取本 URL。

    @returns `(允许?, crawl_delay 秒, 原因)`——不允许时原因是给人看的日志/健康信息。

    两个刻意的选择：
    - **取不到 robots 时放行**：404 / 不可达 / 格式不认识都按"允许"处理。
      大量站点不提供 robots.txt，把"没有声明"当成"禁止"会让抓取整体停摆；
      而 robots 的本意是"我不希望被抓"，没有声明即无此项声明。
    - **按 host 缓存**：robots 是整站声明，同站多源共享一次解析；缓存带 TTL，
      对方改了规则最多滞后一个 TTL。
    """
    import time
    from urllib.robotparser import RobotFileParser

    host = urlparse(url).netloc
    now = time.time()
    cached = _ROBOTS_CACHE.get(host)
    if cached and now - cached[0] < ROBOTS_TTL_SECONDS:
        rp = cached[1]
    else:
        rp = None
        try:
            r = client.get(_robots_url(url))
            if r.status_code == 200 and r.text:
                parser = RobotFileParser()
                parser.parse(r.text.splitlines())
                rp = parser
        except Exception as e:  # robots 取不到不该影响正文抓取
            logger.info("robots.txt 获取失败[%s]，按放行处理: %s", host, e)
            rp = None
        _ROBOTS_CACHE[host] = (now, rp)

    if rp is None:
        return True, None, ""
    try:
        if not rp.can_fetch(user_agent, url):
            return False, None, "robots.txt 禁止抓取"
        delay = rp.crawl_delay(user_agent)
        return True, (float(delay) if delay else None), ""
    except Exception as e:  # 解析/匹配异常同样按放行处理
        logger.info("robots 判定异常[%s]，按放行处理: %s", host, e)
        return True, None, ""


def clear_robots_cache() -> None:
    """清空 robots 缓存（测试与"对方刚改规则想立刻生效"时用）。"""
    _ROBOTS_CACHE.clear()


class NotModified(Exception):
    """源返回 304：内容未变更。**不是错误**，调用方不应重试。"""


# ---- 失败分级（P1-18）----
# 两类失败的**对策完全不同**，混在一起就只能一刀切：
TRANSIENT = "transient"  # 可重试：超时 / 连接错误 / 5xx / 429 —— 等一等就好
PERMANENT = "permanent"  # 永久：4xx / robots 拒绝 / 解析无条目 —— 必须改配置或换源


def classify_status(status: int) -> str:
    """HTTP 状态 → 失败分类。

    - `5xx` 与 `429` 归**可重试**：服务端的错与限流都是"稍后再来"的显式信号，
      退避重试正是对的做法（429 常自带 Retry-After，但统一退避已足够）；
    - 其余 `4xx` 归**永久**：401/403 重试不会突然有权限，404/410 地址不会自己回来，
      重试只会多打几个一模一样的请求。
    """
    if status >= 500 or status == 429:
        return TRANSIENT
    return PERMANENT


class FetchError(Exception):
    """单源抓取失败：网络异常 / HTTP 错误状态 / 解析后无条目。

    为何要抛而不是静默返回空列表：调用方需要区分「源挂了」与「源正常但没内容」，
    否则 `rss_sources.last_status` 永远是 ok，源悄悄烂掉也无人发现（迁移 0013）。

    为何要带 `kind`：异常跨线程传回主线程后常被 `str(e)` 抹成字符串，
    分类信息会丢，调用方就又退化成"所有失败一视同仁"。
    """

    def __init__(self, message: str, kind: str = PERMANENT):
        super().__init__(message)
        self.kind = kind


# 每源缓存 ETag / Last-Modified（进程内；重启失效，仅用于省流）
_CONDITIONAL_CACHE: dict[str, dict[str, str]] = {}


def _conditional_headers(key: str) -> dict[str, str]:
    cached = _CONDITIONAL_CACHE.get(key) or {}
    headers: dict[str, str] = {}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]
    return headers


def _remember_validators(key: str, resp: httpx.Response) -> None:
    etag = resp.headers.get("etag")
    last_modified = resp.headers.get("last-modified")
    if etag or last_modified:
        _CONDITIONAL_CACHE[key] = {
            k: v for k, v in {"etag": etag, "last_modified": last_modified}.items() if v
        }


def fetch_source(
    client: httpx.Client,
    source: dict,
    limit: int = 30,
) -> list[RawItem]:
    """抓取单个源；失败抛出 `FetchError`（**不再静默返回空列表**）。

    支持条件请求：携带上次的 ETag / Last-Modified，源未变更时抛 `NotModified`
    ——那是"没变"不是"失败"，调用方不应重试也不应记为故障。

    抛错而非返回空列表的原因：空列表无法区分"源不可达"与"源正常但今天没有新条目"，
    调用方（`collect`）需要据此把健康状态回写 `rss_sources`，让烂掉的源在列表里现形。
    """
    key = source.get("key") or source.get("url", "")
    name = source.get("name") or key
    try:
        r = client.get(source["url"], headers=_conditional_headers(key))
    except Exception as e:  # 连接超时、DNS 失败、file:// 等非法协议
        logger.warning("请求失败 [%s]: %s %s", name, type(e).__name__, e)
        raise FetchError(f"{type(e).__name__}: {e}", TRANSIENT) from e

    if r.status_code == 304:
        logger.info("源未变更(304) [%s]", name)
        raise NotModified()

    if r.status_code >= 400:
        logger.warning("HTTP %s [%s]", r.status_code, name)
        raise FetchError(f"HTTP {r.status_code}", classify_status(r.status_code))

    _remember_validators(key, r)
    items = parse_feed(r.text, source)
    if not items:
        # 200 却解析不出条目：多半是源改版/返回了 HTML，属于真实故障。
        # 归**永久**：HTTP 这一层是成功的，说明网络与地址都通，
        # 立刻再取一次几乎必然拿到同一份内容——重试没有意义。
        logger.warning("解析后无条目 [%s]", name)
        raise FetchError("解析后无条目", PERMANENT)
    logger.info("抓取 %s: %d 条", name, len(items))
    return items[:limit]