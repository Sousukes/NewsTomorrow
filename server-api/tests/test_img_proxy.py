"""B4：资讯配图代理，**重点测 SSRF 防线**。

图片代理是典型的 SSRF 攻击面（把"能发请求的服务器"交给用户指定目标）。
所以本文件的主体不是"能不能取到图"，而是**"哪些目标必须被拒绝"**——
包括最容易被漏掉的两种：DNS 多记录里混一条内网、以及外站 302 跳到内网。

网络调用全部打桩：`socket.getaddrinfo` 与 `httpx.Client.get` 被替换，
因此这些用例**不联网**，也能稳定复现每一次攻击路径。

运行：
    cd server-api
    pytest tests/test_img_proxy.py -q
"""
import os

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app import config  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.routers import img as img_mod  # noqa: E402

PUBLIC_IP = "93.184.216.34"  # example.com 的公网地址，仅用于打桩
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def _clean():
    img_mod._cache_clear()
    yield
    img_mod._cache_clear()


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c


def _fake_dns(monkeypatch, public_hosts=("*",)):
    """把 DNS 解析打桩成"返回公网 IP"；IP 字面量则原样返回（贴近真实行为）。"""
    real = __import__("socket").getaddrinfo

    def fake(host, port, *args, **kwargs):
        import socket as s

        # IP 字面量：真实解析会直接返回它自己，不能被替换掉
        try:
            s.inet_aton(host)
            return real(host, port, *args, **kwargs)
        except OSError:
            pass
        return [(s.AF_INET, s.SOCK_STREAM, 6, "", (PUBLIC_IP, port or 80))]

    monkeypatch.setattr(img_mod.socket, "getaddrinfo", fake)


def _stub_get(monkeypatch, responses):
    """把"回源"这一步打桩成按调用顺序返回预置响应。

    打桩点是 `img._build_client` 而不是 `httpx.Client.get`：FastAPI 的
    `TestClient` 本身就是 `httpx.Client` 的子类，直接打桩会把测试自己的
    请求也一起打掉（症状是一堆用例莫名返回上游的预置响应）。
    """
    calls: list[str] = []
    seq = list(responses)

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, *args, **kwargs):
            calls.append(str(url))
            item = seq.pop(0) if seq else responses[-1]
            if isinstance(item, Exception):
                raise item
            status, headers, content = item
            return httpx.Response(
                status_code=status,
                headers=headers,
                content=content,
                request=httpx.Request("GET", str(url)),
            )

    monkeypatch.setattr(img_mod, "_build_client", FakeClient)
    return calls


def _image_response(status=200, content_type="image/png", content=PNG_1PX):
    return (status, {"content-type": content_type}, content)


class TestSsrfRejections:
    """必须在**建连之前**就被拒的目标。"""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://example.com:70/x",
            "data:image/png;base64,AAAA",
            "ftp://example.com/x.png",
        ],
    )
    def test_non_http_scheme(self, client, url):
        r = client.get("/api/img", params={"url": url})
        assert r.status_code == 400
        assert r.json()["code"] == 40008

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/x.png",
            "http://localhost/x.png",
            "http://169.254.169.254/latest/meta-data/iam/",
            "http://10.1.2.3/x.png",
            "http://172.16.0.1/x.png",
            "http://192.168.1.1/x.png",
            "http://0.0.0.0/x.png",
            "http://[::1]/x.png",
        ],
    )
    def test_private_and_loopback_targets(self, client, url):
        """云元数据（169.254.169.254）与各段私有网段必须在 DNS 解析后被拒。"""
        r = client.get("/api/img", params={"url": url})
        assert r.status_code == 400, url
        assert r.json()["code"] == 40008

    def test_multi_a_record_with_one_private_is_rejected(self, client, monkeypatch):
        """攻击者给域名配多条 A 记录（一条公网、一条内网）想蒙混过关。"""
        import socket as s

        def mixed(host, port, *args, **kwargs):
            return [
                (s.AF_INET, s.SOCK_STREAM, 6, "", (PUBLIC_IP, port)),
                (s.AF_INET, s.SOCK_STREAM, 6, "", ("10.0.0.9", port)),
            ]

        monkeypatch.setattr(img_mod.socket, "getaddrinfo", mixed)
        r = client.get("/api/img", params={"url": "https://sneaky.example/a.png"})
        assert r.status_code == 400
        assert r.json()["code"] == 40008

    def test_redirect_to_internal_is_rejected(self, client, monkeypatch):
        """最经典的绕过：公网入口 302 → 内网目标。每一跳都要复检。"""
        _fake_dns(monkeypatch)
        _stub_get(
            monkeypatch,
            [(302, {"location": "http://169.254.169.254/latest/meta-data/"}, b"")],
        )
        r = client.get("/api/img", params={"url": "https://img.example/a.png"})
        assert r.status_code == 400
        assert r.json()["code"] == 40008

    def test_redirect_loop_is_bounded(self, client, monkeypatch):
        _fake_dns(monkeypatch)
        _stub_get(
            monkeypatch,
            [(302, {"location": "https://img.example/a.png"}, b"")],
        )
        r = client.get("/api/img", params={"url": "https://img.example/a.png"})
        assert r.status_code == 502
        assert r.json()["code"] == 50201

    def test_no_network_call_happens_for_rejected_target(self, client, monkeypatch):
        """被拒的目标**一次 HTTP 请求都不该发出**（否则 SSRF 已经发生）。"""
        _fake_dns(monkeypatch)
        calls = _stub_get(monkeypatch, [_image_response()])
        r = client.get("/api/img", params={"url": "http://169.254.169.254/x"})
        assert r.status_code == 400
        assert calls == [], f"拒绝路径仍然发起了请求：{calls}"


class TestProxyBehaviour:
    def test_returns_image_bytes(self, client, monkeypatch):
        _fake_dns(monkeypatch)
        _stub_get(monkeypatch, [_image_response()])
        r = client.get("/api/img", params={"url": "https://img.example/a.png"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == PNG_1PX

    def test_hardening_headers(self, client, monkeypatch):
        """代理与站点同源：nosniff + Content-Disposition 是第二道防线。"""
        _fake_dns(monkeypatch)
        _stub_get(monkeypatch, [_image_response()])
        r = client.get("/api/img", params={"url": "https://img.example/a.png"})
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-disposition"] == "inline"

    def test_non_image_upstream_rejected(self, client, monkeypatch):
        """同源代理转发 HTML = 存储型 XSS 的入口，必须拒。"""
        _fake_dns(monkeypatch)
        _stub_get(
            monkeypatch, [(200, {"content-type": "text/html; charset=utf-8"}, b"<script>x</script>")]
        )
        r = client.get("/api/img", params={"url": "https://evil.example/x"})
        assert r.status_code == 415
        assert r.json()["code"] == 41501

    def test_svg_rejected(self, client, monkeypatch):
        """SVG 可内嵌脚本：直接访问代理地址就等于在本源下拿到可执行内容。"""
        _fake_dns(monkeypatch)
        _stub_get(monkeypatch, [(200, {"content-type": "image/svg+xml"}, b"<svg/>")])
        r = client.get("/api/img", params={"url": "https://evil.example/x.svg"})
        assert r.status_code == 415

    def test_oversize_rejected(self, client, monkeypatch):
        _fake_dns(monkeypatch)
        _stub_get(monkeypatch, [(200, {"content-type": "image/png"}, b"0" * (5 * 1024 * 1024 + 1))])
        r = client.get("/api/img", params={"url": "https://img.example/big.png"})
        assert r.status_code == 413
        assert r.json()["code"] == 41301

    def test_upstream_error(self, client, monkeypatch):
        _fake_dns(monkeypatch)
        _stub_get(monkeypatch, [httpx.ConnectError("boom")])
        r = client.get("/api/img", params={"url": "https://img.example/a.png"})
        assert r.status_code == 502
        assert r.json()["code"] == 50201

    def test_width_whitelist_rejects_arbitrary_values(self, client, monkeypatch):
        """任意 w 会让缓存键爆炸 = 把缓存撑爆的入口。"""
        calls = _stub_get(monkeypatch, [_image_response()])
        r = client.get(
            "/api/img", params={"url": "https://img.example/a.png", "w": 777}
        )
        assert r.status_code == 422
        assert calls == []

    def test_empty_url(self, client):
        r = client.get("/api/img", params={"url": "   "})
        assert r.status_code == 422
        assert r.json()["code"] == 42206


class TestCacheAndResize:
    def test_second_request_hits_cache(self, client, monkeypatch):
        monkeypatch.setattr(config, "CACHE_ENABLED", True)
        _fake_dns(monkeypatch)
        calls = _stub_get(monkeypatch, [_image_response()])
        client.get("/api/img", params={"url": "https://img.example/a.png"})
        client.get("/api/img", params={"url": "https://img.example/a.png"})
        assert len(calls) == 1, "第二次请求应命中缓存，不该再回源"

    def test_different_width_is_a_different_cache_key(self, client, monkeypatch):
        monkeypatch.setattr(config, "CACHE_ENABLED", True)
        _fake_dns(monkeypatch)
        calls = _stub_get(monkeypatch, [_image_response()])
        client.get("/api/img", params={"url": "https://img.example/a.png", "w": 200})
        client.get("/api/img", params={"url": "https://img.example/a.png", "w": 640})
        assert len(calls) == 2

    def test_cache_disabled_always_refetches(self, client, monkeypatch):
        monkeypatch.setattr(config, "CACHE_ENABLED", False)
        _fake_dns(monkeypatch)
        calls = _stub_get(monkeypatch, [_image_response()])
        client.get("/api/img", params={"url": "https://img.example/a.png"})
        client.get("/api/img", params={"url": "https://img.example/a.png"})
        assert len(calls) == 2

    def test_resize_when_pillow_available(self, client, monkeypatch):
        """装了 Pillow 就出缩略图（WebP）；这是可选增强，故按可用性跳过。"""
        if not img_mod._HAS_PILLOW:
            pytest.skip("Pillow 未安装：代理原样透传，功能不受影响")
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (800, 400), (10, 20, 30)).save(buf, format="PNG")
        wide = buf.getvalue()
        _fake_dns(monkeypatch)
        _stub_get(monkeypatch, [(200, {"content-type": "image/png"}, wide)])
        r = client.get("/api/img", params={"url": "https://img.example/w.png", "w": 200})
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/webp"
        with Image.open(io.BytesIO(r.content)) as im:
            assert im.width == 200

    def test_no_upscale(self, client, monkeypatch):
        """比原图小的宽度不放大：放大只会糊，且白花带宽。"""
        if not img_mod._HAS_PILLOW:
            pytest.skip("Pillow 未安装")
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (120, 80), (10, 20, 30)).save(buf, format="PNG")
        small = buf.getvalue()
        _fake_dns(monkeypatch)
        _stub_get(monkeypatch, [(200, {"content-type": "image/png"}, small)])
        r = client.get("/api/img", params={"url": "https://img.example/s.png", "w": 640})
        assert r.content == small