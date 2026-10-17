"""C6：管理端鉴权的**结构性**守卫。

router 级依赖的价值不在于「少写几行」，而在于让「新增端点漏判」在结构上不可能发生。
本文件用 OpenAPI 枚举**全部** `/api/admin` 操作（方法 × 路径），逐个断言：

- 未登录 → **401**（不能被 404/422/500 抢先）；
- 已登录但非管理员 → **403 / 40301**。

新增管理端点若忘了鉴权，这里会立刻变红——而不是等上线后被人越权才发现。
注意：`app.routes` 里的 `include_router` 是 `_IncludedRouter` 包装（`path=None`），
**必须用 `app.openapi()["paths"]`** 才能拿到完整清单。
"""
import re

import pytest
from fastapi.testclient import TestClient

import main
from app.database import Base, engine

# conftest.py 固定 ADMIN_PHONES=13800138000；本文件的"路人"号必须不在其中
USER_PHONE = "13700000001"


def _admin_ops() -> list[tuple[str, str]]:
    """枚举全部管理端操作（method, path），顺序稳定便于定位失败项。"""
    paths = main.app.openapi()["paths"]
    ops: list[tuple[str, str]] = []
    for path, item in paths.items():
        if not path.startswith("/api/admin"):
            continue
        for method in item:
            ops.append((method.upper(), path))
    return sorted(ops, key=lambda x: (x[1], x[0]))


def _concrete(path: str) -> str:
    """把 `{param}` 换成占位值：路径不匹配会先返回 404，就测不到鉴权了。"""
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def non_admin_headers(client) -> dict:
    client.post(
        "/api/auth/register",
        json={"phone": USER_PHONE, "password": "abc123", "name": "路人"},
    )
    r = client.post("/api/auth/login", json={"phone": USER_PHONE, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


class TestAdminGuardIsStructural:
    def test_every_admin_op_requires_login(self, client):
        """未登录访问任何管理端点都必须 401（鉴权先于参数校验）。"""
        failures = []
        for method, path in _admin_ops():
            r = client.request(method, _concrete(path), json={})
            if r.status_code != 401:
                failures.append((method, path, r.status_code))
        assert not failures, f"未受登录保护的管理端点: {failures}"

    def test_every_admin_op_rejects_non_admin(self, client, non_admin_headers):
        """已登录但非管理员必须 403 + 40301。"""
        failures = []
        for method, path in _admin_ops():
            r = client.request(method, _concrete(path), json={}, headers=non_admin_headers)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 403 or body.get("code") != 40301:
                failures.append((method, path, r.status_code, body.get("code")))
        assert not failures, f"非管理员未被拒绝: {failures}"

    def test_guard_is_declared_on_router_not_per_endpoint(self):
        """结构断言：router 级依赖存在，且源码中不再有逐端点守卫调用。

        这条测试防的是「重构回退」——有人为了省事又把守卫写回函数体，
        下一个人新增端点时仍会漏。
        """
        from app.routers import admin as admin_mod

        assert admin_mod.router.dependencies, "router 未声明鉴权依赖"
        source = open(admin_mod.__file__, encoding="utf-8").read()
        calls = [ln for ln in source.splitlines() if ln.strip().startswith("_require_admin(")]
        assert not calls, f"仍有逐端点守卫调用: {calls}"
