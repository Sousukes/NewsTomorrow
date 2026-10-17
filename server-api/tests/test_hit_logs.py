"""C7：命中日志留存治理。

`sensitive_hits` / `nickname_hits` 的 `snippet` 是**用户原文**（脱敏不可逆，
是误杀复盘与申诉的唯一证据）。无限期保存用户原话是合规风险，故：

- 按 `HIT_LOG_KEEP_DAYS`（默认 90 天）删除；
- **不设保底条数**——保留期就是它的全部价值，保底反而留下一批最旧的用户原话；
- `keep_days=0` 关闭清理（取证期临时用）。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.database import Base, SessionLocal, engine
from app.hit_logs import prune_hit_logs
from app.models import NicknameHit, NicknameWord, SensitiveHit


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        yield session
    Base.metadata.drop_all(bind=engine)


def _iso(days_ago: float, base: datetime | None = None) -> str:
    b = base or datetime.now(timezone.utc)
    return (b - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _sensitive(db, created_at: str, snippet: str = "原文片段") -> None:
    db.add(
        SensitiveHit(
            word="坏词",
            action="mask",
            field="content",
            snippet=snippet,
            created_at=created_at,
        )
    )


def _nickname(db, created_at: str, snippet: str = "原文昵称") -> None:
    db.add(
        NicknameHit(
            word="坏词",
            matched="坏词",
            action="block",
            source="update",
            snippet=snippet,
            created_at=created_at,
        )
    )


class TestRetention:
    def test_deletes_expired_rows_in_both_tables(self, db):
        _sensitive(db, _iso(120))
        _sensitive(db, _iso(1))
        _nickname(db, _iso(200))
        _nickname(db, _iso(2))
        db.commit()

        out = prune_hit_logs(db, keep_days=90)

        assert out == {"sensitive": 1, "nickname": 1}
        assert db.query(SensitiveHit).count() == 1
        assert db.query(NicknameHit).count() == 1

    def test_boundary_row_is_kept(self, db):
        """正好等于截止时间的行保留（`<` 严格比较）。"""
        now = datetime.now(timezone.utc)
        _sensitive(db, _iso(90, base=now))
        db.commit()

        assert prune_hit_logs(db, keep_days=90, now=now)["sensitive"] == 0
        assert db.query(SensitiveHit).count() == 1

    def test_no_minimum_floor(self, db):
        """不设保底：只剩一条且已超期也照删（与 prune_old_news 的 keep_min 不同）。"""
        _sensitive(db, _iso(400))
        db.commit()

        assert prune_hit_logs(db, keep_days=90)["sensitive"] == 1
        assert db.query(SensitiveHit).count() == 0

    def test_zero_disables_cleanup(self, db):
        _sensitive(db, _iso(999))
        _nickname(db, _iso(999))
        db.commit()

        assert prune_hit_logs(db, keep_days=0) == {"sensitive": 0, "nickname": 0}
        assert db.query(SensitiveHit).count() == 1
        assert db.query(NicknameHit).count() == 1

    def test_default_retention_is_90_days(self):
        assert config.HIT_LOG_KEEP_DAYS == 90

    def test_aggregate_hit_count_survives(self, db):
        """累计计数是聚合值：删日志不回退（"历史命中过多少次"与"还剩多少原文证据"是两件事）。"""
        db.add(NicknameWord(word="坏词", hit_count=7, updated_at=_iso(1)))
        _nickname(db, _iso(200))
        db.commit()

        prune_hit_logs(db, keep_days=90)

        row = db.query(NicknameWord).filter(NicknameWord.word == "坏词").first()
        assert row is not None and row.hit_count == 7


class TestStartupSweep:
    """lifespan 启动即清理：**不依赖 cron**（`FEED_CRON_ENABLED=false` 时也要删）。

    注意：断言必须在 `with TestClient(...)` **上下文内**。退出上下文会触发
    `stop_scheduler()` → `dispose()`，而内存 SQLite（StaticPool）一旦 dispose，
    整个库就没了——之后任何查询都是 "no such table"（不是清理失败）。
    """

    def test_startup_removes_expired_rows(self):
        from fastapi.testclient import TestClient

        import main

        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        with SessionLocal() as db:
            _sensitive(db, _iso(400))
            _nickname(db, _iso(400))
            db.commit()

        with TestClient(main.app):
            # 进入上下文即触发 lifespan（init_db → 清理 → 启动调度）
            with SessionLocal() as db:
                assert db.query(SensitiveHit).count() == 0
                assert db.query(NicknameHit).count() == 0
