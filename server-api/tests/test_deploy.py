"""D8：部署形态的可验证部分。

分两类，强度不同，**都说明了为什么只能到这一步**：

1. **entrypoint.sh 实际执行**——用桩替掉 `alembic` / `uvicorn`，断言
   「迁移先于服务」且「迁移失败就不起服务」。这是本批真正可执行的部分。
2. **部署文件静态不变量**——本机没有 Docker 守护进程，`docker build` 无法
   验证（背���里已登记为 backlog 后续项）。退而求其次：把"配错就会出事故"的
   那几条钉成断言，防止后续改动悄悄破坏它们。静态断言抓不到运行时问题，
   这一点必须诚实，不能让它冒充"已验证"。

运行：
    cd server-api
    pytest tests/test_deploy.py -q
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "server-api"
FRONTEND = REPO / "mingri-toutiao-vue"
COMPOSE = REPO / "docker-compose.yml"

ENTRYPOINT = SERVER / "scripts" / "entrypoint.sh"


# ---------------------------------------------------------------- entrypoint


def _make_stub(dir_: Path, name: str, exit_code: int, log: Path, label: str = "") -> Path:
    """造一个假可执行文件：把调用记进 log，按 exit_code 退出。

    注意 entrypoint 调的是 **`python -m alembic`**（与 CI 的
    `python -m alembic upgrade head` 同一形态），所以替的是 `python` 而不是
    `alembic` 控制台脚本 —— 早先按 `alembic` 打桩，结果真 alembic 跑了起来
    （Windows 上恰好因没装 alembic 而快速失败，把问题掩盖成"行为正确"）。
    """
    p = dir_ / name
    p.write_text(f'#!/bin/sh\necho "{label or name}" >> "{log}"\nexit {exit_code}\n', encoding="utf-8")
    p.chmod(0o755)
    return p


@pytest.fixture
def run_entrypoint(tmp_path):
    """跑 entrypoint，返回 (退出码, 调用序列)。"""

    def _run(alembic_exit: int = 0) -> tuple[int, list[str]]:
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir(exist_ok=True)
        log = tmp_path / "calls.log"
        log.write_text("", encoding="utf-8")
        _make_stub(stub_dir, "python", alembic_exit, log, label="alembic")
        _make_stub(stub_dir, "uvicorn", 0, log)
        env = dict(os.environ)
        env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
        proc = subprocess.run(
            ["sh", str(ENTRYPOINT)],  # noqa: S603 —— 固定路径，非用户输入
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        return proc.returncode, calls

    return _run


@pytest.mark.skipif(sys.platform == "win32", reason="entrypoint 是 sh 脚本，需 POSIX shell")
class TestEntrypoint:
    def test_migration_runs_before_server(self, run_entrypoint):
        code, calls = run_entrypoint()
        assert code == 0
        assert calls == ["alembic", "uvicorn"], f"顺序不对：{calls}"

    def test_migration_failure_stops_startup(self, run_entrypoint):
        """迁移失败必须**阻止**服务启动：起了也只会满屏 500，
        还会把「环境没升级」伪装成「代码有 bug」。"""
        code, calls = run_entrypoint(alembic_exit=1)
        assert code != 0
        assert calls == ["alembic"], f"迁移失败后仍启动了服务：{calls}"

    def test_skip_migration_flag(self, tmp_path, run_entrypoint):
        """RUN_MIGRATIONS=false：compose 的多副本形态依赖它。"""
        stub_dir = tmp_path / "bin2"
        stub_dir.mkdir()
        log = tmp_path / "calls2.log"
        log.write_text("", encoding="utf-8")
        _make_stub(stub_dir, "python", 0, log, label="alembic")
        _make_stub(stub_dir, "uvicorn", 0, log)
        env = dict(os.environ)
        env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
        env["RUN_MIGRATIONS"] = "false"
        proc = subprocess.run(
            ["sh", str(ENTRYPOINT)], capture_output=True, text=True, env=env, timeout=30
        )
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        assert proc.returncode == 0
        assert calls == ["uvicorn"], f"RUN_MIGRATIONS=false 仍跑了迁移：{calls}"


# ---------------------------------------------------------------- 静态不变量


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


COMPOSE_TEXT = _read(COMPOSE)
NGINX_TEXT = _read(FRONTEND / "nginx.conf")
BACKEND_DOCKERFILE = _read(SERVER / "Dockerfile")
DEPLOY_DOC = _read(REPO / "docs" / "deploy.md")


class TestCompose:
    def test_migration_is_a_one_shot_service(self):
        """多副本并发跑迁移会互相锁死同一份 DDL。"""
        assert '"alembic"' in COMPOSE_TEXT and '"upgrade", "head"' in COMPOSE_TEXT
        assert "service_completed_successfully" in COMPOSE_TEXT
        # migrate 与 backend 都要引用同一个环境锚点（YAML 锚点）
        assert "&backend_env" in COMPOSE_TEXT and "<<: *backend_env" in COMPOSE_TEXT

    def test_backend_waits_for_migration_and_db_health(self):
        assert re.search(r"migrate:\s*\n\s*condition: service_completed_successfully", COMPOSE_TEXT)
        assert re.search(r"db:\s*\n\s*condition: service_healthy", COMPOSE_TEXT)

    def test_database_not_exposed_to_host(self):
        """db 不用 ports 映射（只在 compose 网络内可达）。"""
        db_block = COMPOSE_TEXT.split("\n  redis:")[0]
        assert "ports:" not in db_block

    def test_redis_not_persistent(self):
        """缓存与限流计数都是可丢数据；持久化只会带来"缓存脏了"的运维动作。"""
        redis_block = COMPOSE_TEXT.split("\n  migrate:")[0]
        assert "--save" in redis_block and "appendonly" in redis_block

    def test_frontend_build_explicitly_disables_mock(self):
        """漏掉 VITE_USE_MOCK=false → 生产镜像静默跑内置 mock，界面上看不出来。"""
        assert 'VITE_USE_MOCK: "false"' in COMPOSE_TEXT

    def test_secrets_are_required_not_defaulted(self):
        """compose 用 ${VAR:?} 强制拦截，绝不能给密钥一个默认值。"""
        assert "${JWT_SECRET:?" in COMPOSE_TEXT
        assert "${POSTGRES_PASSWORD:?" in COMPOSE_TEXT


class TestFrontendContainer:
    def test_api_is_not_spa_fallback(self):
        """API 404 时返回 index.html 会让前端 json() 报一个完全误导的错误。"""
        api_block = NGINX_TEXT.split("location /api/")[1].split("}")[0]
        assert "try_files" not in api_block

    def test_spa_fallback_exists(self):
        assert "try_files $uri $uri/ /index.html" in NGINX_TEXT

    def test_index_html_is_not_cached(self):
        """它是"引用了哪些哈希资源"的唯一清单，缓存住会让用户一直拿旧版本。"""
        assert "location = /index.html" in NGINX_TEXT
        assert "no-cache" in NGINX_TEXT

    def test_header_csp_only_adds_what_meta_cannot(self):
        """两份策略各写一遍必然漂移；CSP 多头取交集，所以只补 meta 表达不了的。"""
        csp_line = [ln for ln in NGINX_TEXT.splitlines() if "Content-Security-Policy" in ln][0]
        assert "frame-ancestors" in csp_line
        for other in ("script-src", "default-src", "img-src"):
            assert other not in csp_line

    def test_client_ip_is_forwarded(self):
        """限流按客户端 IP 计数，XFF 不透传就全站共享一个计数桶。"""
        assert "X-Forwarded-For" in NGINX_TEXT

    def test_dockerfile_declares_mock_flag(self):
        dockerfile = _read(FRONTEND / "Dockerfile")
        assert "VITE_USE_MOCK" in dockerfile


class TestBackendContainer:
    def test_healthcheck_carries_ops_token(self):
        """C5 是 fail-closed 的：生产不设 OPS_TOKEN 时 /api/health 恒 403，
        容器永远 unhealthy —— 刻意让"忘记配密钥"表现为起不来，而不是
        悄悄裸奔。"""
        health = BACKEND_DOCKERFILE.split("HEALTHCHECK")[1]
        assert "X-Ops-Token" in health

    def test_migration_not_run_at_build_time(self):
        """构建是无状态的，连不上运行期的库；迁移必须由 entrypoint 跑。"""
        build_stage = BACKEND_DOCKERFILE.split("ENTRYPOINT")[0]
        assert "alembic upgrade" not in build_stage

    def test_entrypoint_is_the_entry(self):
        assert 'ENTRYPOINT ["./scripts/entrypoint.sh"]' in BACKEND_DOCKERFILE


class TestDeployDoc:
    def test_doc_mentions_required_secrets(self):
        for token in ("JWT_SECRET", "POSTGRES_PASSWORD", "OPS_TOKEN", "TRUSTED_PROXIES"):
            assert token in DEPLOY_DOC, f"部署文档没提 {token}"

    def test_doc_states_docker_build_unverified(self):
        """边界必须写在文档里，不能让人以为镜像已经在 CI 验证过。"""
        assert "不在 CI 里跑" in DEPLOY_DOC or "无法验证" in DEPLOY_DOC


def test_sh_is_available_for_posix_only():
    """Windows 本机有 Git Bash，但 CI 才有真正的 sh；缺 sh 时整体跳过。"""
    if sys.platform != "win32" and not shutil.which("sh"):
        pytest.skip("环境里没有 sh")