"""pytest 单元测试：限流器、密码哈希、JWT、RSS 解析与字段映射。

运行：
    cd server-api
    pytest -q
"""
import time

import pytest

from app.feeds.fetcher import (
    _clean,
    _extract_images,
    _pick_variant,
    _stable_id,
    _to_relative_time,
    parse_feed,
)
from app.ratelimit import FixedWindowLimiter
from app.security import create_token, decode_token, hash_password, verify_password


# ----------------------------- 限流器 -----------------------------

class TestFixedWindowLimiter:
    def test_allows_until_limit(self):
        rl = FixedWindowLimiter(window_seconds=60)
        assert rl.allow("ip", 3)[0] is True
        assert rl.allow("ip", 3)[0] is True
        assert rl.allow("ip", 3)[0] is True

    def test_blocks_over_limit(self):
        rl = FixedWindowLimiter(window_seconds=60)
        for _ in range(3):
            rl.allow("ip", 3)
        allowed, remaining = rl.allow("ip", 3)
        assert allowed is False
        assert remaining == 0

    def test_remaining_counts_down(self):
        rl = FixedWindowLimiter(window_seconds=60)
        assert rl.allow("ip", 3)[1] == 2
        assert rl.allow("ip", 3)[1] == 1
        assert rl.allow("ip", 3)[1] == 0

    def test_keys_are_isolated(self):
        rl = FixedWindowLimiter(window_seconds=60)
        for _ in range(2):
            rl.allow("a", 2)
        assert rl.allow("a", 2)[0] is False
        assert rl.allow("b", 2)[0] is True  # 不同 key 不受影响

    def test_window_expires(self):
        rl = FixedWindowLimiter(window_seconds=1)
        for _ in range(2):
            rl.allow("ip", 2)
        assert rl.allow("ip", 2)[0] is False
        time.sleep(1.1)
        assert rl.allow("ip", 2)[0] is True  # 窗口过后恢复

    def test_path_scoped_keys(self):
        """登录与普通接口用不同 key，限流互不干扰。"""
        rl = FixedWindowLimiter(window_seconds=60)
        for _ in range(2):
            rl.allow("ip:/api/auth/login", 2)
        assert rl.allow("ip:/api/auth/login", 2)[0] is False
        assert rl.allow("ip:/api/news", 2)[0] is True


# ----------------------------- 密码与JWT -----------------------------

class TestSecurity:
    def test_hash_and_verify(self):
        h = hash_password("secret123")
        assert h.startswith("pbkdf2_sha256$")
        assert verify_password("secret123", h) is True
        assert verify_password("wrong", h) is False

    def test_hash_is_salted(self):
        """相同密码两次哈希应不同（随机盐）。"""
        assert hash_password("same") != hash_password("same")

    def test_verify_malformed(self):
        assert verify_password("x", "garbage") is False
        assert verify_password("x", "") is False

    def test_token_roundtrip(self):
        token = create_token(42)
        assert decode_token(token) == 42

    def test_token_invalid(self):
        assert decode_token("garbage") is None
        assert decode_token("") is None


# ----------------------------- RSS 解析与映射 -----------------------------

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>示例源</title>
  <item>
    <title>  暴雨蓝色预警  </title>
    <link>http://example.com/a.html</link>
    <description>&lt;p&gt;正文&lt;/p&gt;&lt;img src="http://cdn.x.com/1.jpg" /&gt;</description>
    <pubDate>Mon, 07 Oct 2026 10:00:00 +0800</pubDate>
  </item>
  <item>
    <title>无图消息</title>
    <link>http://example.com/b.html</link>
    <description>纯文字内容</description>
    <pubDate>Mon, 07 Oct 2026 09:00:00 +0800</pubDate>
  </item>
</channel></rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom 标题</title>
    <link href="http://example.com/atom"/>
    <summary>摘要</summary>
    <published>2026-10-07T10:00:00Z</published>
  </entry>
</feed>
"""


class TestFetcher:
    def test_parse_rss_items(self):
        items = parse_feed(RSS_SAMPLE, {"name": "示例源", "channel": "hot"})
        assert len(items) == 2
        assert items[0].title == "暴雨蓝色预警"  # 已 trim
        assert items[0].id.startswith("r_")

    def test_strip_tags_and_entities(self):
        items = parse_feed(RSS_SAMPLE, {"name": "s", "channel": "hot"})
        # 描述里的 <p> 被清洗
        assert "<p>" not in items[0].summary

    def test_extract_image_and_variant(self):
        items = parse_feed(RSS_SAMPLE, {"name": "s", "channel": "hot"})
        assert items[0].images == ["http://cdn.x.com/1.jpg"]
        assert items[0].cover == "http://cdn.x.com/1.jpg"
        assert items[0].variant == "left-right"

    def test_no_image_gives_text_variant(self):
        items = parse_feed(RSS_SAMPLE, {"name": "s", "channel": "hot"})
        assert items[1].images == []
        assert items[1].variant == "text"

    def test_parse_atom(self):
        items = parse_feed(ATOM_SAMPLE, {"name": "s", "channel": "hot"})
        assert len(items) == 1
        assert items[0].title == "Atom 标题"
        assert items[0].url == "http://example.com/atom"

    def test_malformed_xml_returns_empty(self):
        assert parse_feed("<not xml", {"name": "s", "channel": "hot"}) == []

    def test_skip_item_without_title(self):
        bad = "<rss><channel><item><link>x</link></item></channel></rss>"
        assert parse_feed(bad, {"name": "s", "channel": "hot"}) == []

    def test_stable_id_is_deterministic(self):
        """同链接两次解析必须同 id（幂等入库的基础）。"""
        a = parse_feed(RSS_SAMPLE, {"name": "s", "channel": "hot"})[0].id
        b = parse_feed(RSS_SAMPLE, {"name": "s", "channel": "hot"})[0].id
        assert a == b

    def test_stable_id_falls_back_to_title(self):
        assert _stable_id("", "仅标题") == _stable_id("", "仅标题")
        assert _stable_id("http://x", "标题") != _stable_id("http://y", "标题")


class TestHelpers:
    def test_clean(self):
        assert _clean("<b>加粗</b>&nbsp;文本") == "加粗 文本"
        assert _clean(None) == ""

    def test_extract_images_caps_at_three(self):
        blob = "".join(f'<img src="http://c.com/{i}.jpg"/>' for i in range(5))
        assert len(_extract_images(blob)) == 3

    def test_extract_images_ignores_non_image(self):
        assert _extract_images('<a href="http://x.com/a.pdf">链接</a>') == []

    def test_pick_variant(self):
        three = ["a.jpg", "b.jpg", "c.jpg"]
        assert _pick_variant(three, False) == "triple"
        assert _pick_variant(["a.jpg"], False) == "left-right"
        assert _pick_variant([], False) == "text"
        assert _pick_variant([], True) == "video"

    def test_relative_time(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        assert _to_relative_time(now) == "刚刚"
        assert _to_relative_time(now - timedelta(minutes=5)) == "5分钟前"
        assert _to_relative_time(now - timedelta(hours=3)) == "3小时前"
        assert _to_relative_time(now - timedelta(days=2)) == "2天前"
        assert _to_relative_time(None) == ""
