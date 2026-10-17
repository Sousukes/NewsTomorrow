"""pytest 单元测试：限流器、密码哈希、JWT、RSS 解析与字段映射。

运行：
    cd server-api
    pytest -q
"""
import time

import pytest

from app.feeds.fetcher import (
    CONTENT_MAX_CHARS,
    _clean,
    _clean_paragraphs,
    _extract_images,
    _pick_variant,
    _stable_id,
    _to_relative_time,
    _truncate_paragraphs,
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

    def test_blocked_peeks_without_consuming(self):
        """C2：blocked 只窥探不计次——锁定预检不能给计数器续期。"""
        rl = FixedWindowLimiter(window_seconds=60)
        for _ in range(4):
            rl.allow("k", 5)
        assert rl.blocked("k", 5) is False
        # 反复窥探不改变计数（若用 allow 做预检，被拒请求会把窗口无限续期）
        for _ in range(10):
            assert rl.blocked("k", 5) is False
        rl.allow("k", 5)  # 第 5 次：达到上限
        assert rl.blocked("k", 5) is True
        assert rl.blocked("k", 5) is True  # 窥探本身不加数

    def test_reset_clears_count(self):
        """C2：成功登录即清零（reset），失败计数只反映连续失败。"""
        rl = FixedWindowLimiter(window_seconds=60)
        for _ in range(5):
            rl.allow("k", 5)
        assert rl.blocked("k", 5) is True
        rl.reset("k")
        assert rl.blocked("k", 5) is False
        assert rl.allow("k", 5)[0] is True  # 清零后重新计数


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

# E1：带正文的 RSS（content:encoded 与 description 同时存在，正文应优先取全文）
RSS_CONTENT_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>示例源</title>
  <item>
    <title>带正文的资讯</title>
    <link>http://example.com/c.html</link>
    <description>&lt;p&gt;摘要一段&lt;/p&gt;</description>
    <content:encoded><![CDATA[<p>第一段：开局一段话。</p><p>第二段：承接展开。</p><div>第三段：收尾。</div>]]></content:encoded>
    <pubDate>Mon, 07 Oct 2026 10:00:00 +0800</pubDate>
  </item>
</channel></rss>
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


class TestContentExtraction:
    """E1：正文提取（此前 content:encoded 只被用来提图，正文本身被丢弃）。"""

    def test_content_extracted_from_content_encoded(self):
        items = parse_feed(RSS_CONTENT_SAMPLE, {"name": "s", "channel": "hot"})
        assert items[0].content == "第一段：开局一段话。\n第二段：承接展开。\n第三段：收尾。"

    def test_content_strips_tags_and_keeps_paragraphs(self):
        items = parse_feed(RSS_CONTENT_SAMPLE, {"name": "s", "channel": "hot"})
        assert "<p>" not in items[0].content
        assert items[0].content.count("\n") == 2  # 三段 → 两个换行

    def test_summary_and_content_are_independent(self):
        """摘要仍取 description 截断 300；正文取全文——两者互不覆盖。"""
        items = parse_feed(RSS_CONTENT_SAMPLE, {"name": "s", "channel": "hot"})
        assert items[0].summary == "摘要一段"
        assert items[0].content.startswith("第一段")

    def test_content_falls_back_to_description(self):
        """无 content:encoded 时回退 description：那也是真实文本，不是占位文案。"""
        items = parse_feed(RSS_SAMPLE, {"name": "s", "channel": "hot"})
        assert items[0].content == "正文"

    def test_content_empty_when_source_gives_nothing(self):
        bad = "<rss><channel><item><title>只有标题</title><link>x</link></item></channel></rss>"
        items = parse_feed(bad, {"name": "s", "channel": "hot"})
        assert items[0].content == ""

    def test_content_truncated_at_paragraph_boundary(self):
        """超长正文按段落边界截断：不超限、不把半段留在结尾。"""
        paras = "".join(f"<p>第{i}段：{'内容' * 300}。</p>" for i in range(30))
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
            "<channel><item><title>长文</title><link>http://x/long</link>"
            f"<content:encoded><![CDATA[{paras}]]></content:encoded>"
            "</item></channel></rss>"
        )
        items = parse_feed(xml, {"name": "s", "channel": "hot"})
        content = items[0].content
        assert len(content) <= CONTENT_MAX_CHARS
        # 每一段都完整（以「。」结尾），没有句子中间砍断的残段
        assert all(p.endswith("。") for p in content.split("\n"))
        assert content.count("\n") < 29

    def test_single_oversized_paragraph_hard_truncated(self):
        """无换行的几千字段落：退化为硬截断，保证不超限。"""
        big = "字" * (CONTENT_MAX_CHARS + 5000)
        out = _truncate_paragraphs(big)
        assert len(out) == CONTENT_MAX_CHARS

    def test_truncate_keeps_text_within_limit(self):
        assert _truncate_paragraphs("短文本") == "短文本"


class TestHelpers:
    def test_clean(self):
        assert _clean("<b>加粗</b>&nbsp;文本") == "加粗 文本"
        assert _clean(None) == ""

    def test_clean_paragraphs_keeps_structure(self):
        """正文清洗：块级标签/br → 换行，空段丢弃，段内压空白（E1）。"""
        text = "<p>第一段</p><br/><p>第二&nbsp;段</p><p></p><p>  第三段  </p>"
        assert _clean_paragraphs(text) == "第一段\n第二 段\n第三段"
        assert _clean_paragraphs(None) == ""

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
