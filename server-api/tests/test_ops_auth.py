"""C5：运维接口（`/api/health`、`/api/metrics`）访问控制。

这两个接口暴露连接池状态与 Top20 路径分布，不应公开可读。规则：

- 配置 `OPS_TOKEN` → 必须携带 `Authorization: Bearer <token>` 或 `X-Ops-Token: <token>`；
- 未配置 → 开发放行（本地 / CI / 契约检查不受影响）、**生产拒绝**（fail-closed）。

测试要点：`require_ops_access` 在**调用时**读 `app.config.OPS_TOKEN` / `IS_PRODUCTION`
（而非 import 时固定），因此这里用 monkeypatch 覆盖模块属性即可，无需重载模块。
"""
import pytest
from fastapi.testclient import TestClient

import main
from app import config
from app.database import Base, engine

OPS_ENDPOINTS = ("/api/health", "/api/metrics")


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


class TestDevWithoutToken:
    """未配置令牌 + 开发环境：放行（保持本地/CI/契约检查零改动）。"""

    @pytest.mark.parametrize("path", OPS_ENDPOINTS)
    def test_open_in_development(self, client, path):
        assert client.get(path).status_code == 200


class TestTokenConfigured:
    @pytest.fixture(autouse=True)
    def _token(self, monkeypatch):
        monkeypatch.setattr(config, "OPS_TOKEN", "ops-secret-123")

    @pytest.mark.parametrize("path", OPS_ENDPOINTS)
    def test_missing_credentials_rejected(self, client, path):
        r = client.get(path)
        assert r.status_code == 403
        assert r.json()["code"] == 40303

    @pytest.mark.parametrize("path", OPS_ENDPOINTS)
    def test_wrong_token_rejected(self, client, path):
        r = client.get(path, headers={"X-Ops-Token": "nope"})
        assert r.status_code == 403
        assert r.json()["code"] == 40303

    @pytest.mark.parametrize("path", OPS_ENDPOINTS)
    def test_bearer_accepted(self, client, path):
        r = client.get(path, headers={"Authorization": "Bearer ops-secret-123"})
        assert r.status_code == 200

    @pytest.mark.parametrize("path", OPS_ENDPOINTS)
    def test_x_ops_token_accepted(self, client, path):
        """Prometheus 用自定义头更方便，不必伪造 Bearer 格式。"""
        r = client.get(path, headers={"X-Ops-Token": "ops-secret-123"})
        assert r.status_code == 200

    def test_metrics_body_still_prometheus_text(self, client):
        """鉴权不影响成功响应：仍是 Prometheus 文本。"""
        r = client.get("/api/metrics", headers={"X-Ops-Token": "ops-secret-123"})
        assert "toutiao_uptime_seconds" in r.text

    def test_user_jwt_is_not_an_ops_token(self, client):
        """用户 JWT 不能当运维令牌用（两者是不同信任域）。"""
        r = client.get(
            "/api/metrics", headers={"Authorization": "Bearer mock-token-not-ops"}
        )
        assert r.status_code == 403


class TestProductionFailClosed:
    """未配置令牌 + 生产环境：拒绝（避免忘记配置就把内部状态挂公网）。"""

    @pytest.fixture(autouse=True)
    def _prod(self, monkeypatch):
        monkeypatch.setattr(config, "OPS_TOKEN", "")
        monkeypatch.setattr(config, "IS_PRODUCTION", True)

    @pytest.mark.parametrize("path", OPS_ENDPOINTS)
    def test_rejected_without_token(self, client, path):
        r = client.get(path)
        assert r.status_code == 403
        assert r.json()["code"] == 40303
