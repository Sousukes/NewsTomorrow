"""旁路副作用的事务边界与原子性（P1-82）。

运行：cd server-api && pytest tests/test_side_effects.py -q
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"
os.environ["HOT_CRON_ENABLED"] = "false"
os.environ["ADMIN_PHONES"] = "13800138000"

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Comment, Message, News, User, now_str  # noqa: E402
from app.security import create_token  # noqa: E402
from sqlalchemy.orm import Session as OrmSession  # noqa: E402


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _seed_news(nid="n1") -> None:
    with SessionLocal() as db:
        db.add(News(id=nid, title="标题", summary="", source="t"))
        db.commit()


def _seed_comment(cid="c1", news_id="n1", author: int | None = None) -> None:
    _seed_news(news_id)
    with SessionLocal() as db:
        db.add(
            Comment(
                id=cid,
                news_id=news_id,
                content="内容",
                seed="s",
                user_id=author,
                created_at=now_str(),
            )
        )
        db.commit()


def _user(phone="13900000001") -> int:
    with SessionLocal() as db:
        if db.query(User).filter_by(phone=phone).first() is None:
            db.add(User(phone=phone, name="甲", password_hash="x"))
            db.commit()
        return db.query(User).filter_by(phone=phone).first().id


def _auth(phone="13900000001") -> dict:
    return {"Authorization": f"Bearer {create_token(_user(phone))}"}


class TestCommentNotifySameTransaction:
    def test_reply_notifies_author_in_one_commit(self, client):
        """回复他人评论：通知与评论同一次提交。"""
        _seed_comment("c_origin", author=_user("13900000011"))
        h = _auth("13900000002")
        r = client.post(
            "/api/news/n1/comments",
            json={"content": "回复内容", "parentId": "c_origin"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        with SessionLocal() as db:
            cid = r.json()["data"]["id"]
            assert db.query(Comment).filter_by(id=cid).first() is not None
            # 通知必须已经存在：同事务意味着"评论可见时通知也已可见"
            assert db.query(Message).filter_by(type="comment").count() >= 1

    def test_main_comment_notifies_previous_commenters(self, client):
        """主评论：通知同样评论过的其他用户，不含触发者。"""
        _seed_comment("c_other", author=_user("13900000021"))
        h = _auth("13900000022")
        r = client.post("/api/news/n1/comments", json={"content": "主评论"}, headers=h)
        assert r.status_code == 200
        with SessionLocal() as db:
            msgs = db.query(Message).filter_by(type="comment").all()
            assert len(msgs) == 1
            assert msgs[0].user_id == _user("13900000021")

    def test_like_notifies_author_in_one_commit(self, client):
        """点赞：通知与点赞同一次提交。"""
        author = _user("13900000031")
        _seed_comment("c_like", author=author)
        h = _auth("13900000032")
        r = client.patch(
            "/api/news/n1/comments/c_like",
            json={"liked": True},
            headers=h,
        )
        assert r.status_code == 200, r.text
        with SessionLocal() as db:
            assert db.query(Message).filter_by(type="comment").count() == 1

    def test_unlike_does_not_notify(self, client):
        """取消赞不通知。"""
        author = _user("13900000041")
        _seed_comment("c_unlike", author=author)
        h = _auth("13900000042")
        client.patch("/api/news/n1/comments/c_unlike", json={"liked": True}, headers=h)
        r = client.patch(
            "/api/news/n1/comments/c_unlike", json={"liked": False}, headers=h
        )
        assert r.status_code == 200
        with SessionLocal() as db:
            assert db.query(Message).filter_by(type="comment").count() == 1


class TestHotScoreFailureIsAudited:
    def test_failure_writes_audit_and_does_not_raise(self):
        """失败写审计，且绝不向外抛。"""
        from app import ranking
        from app.models import AdminAuditLog

        _seed_news("n_hot")
        with SessionLocal() as db:
            ranking._record_hot_failure(db, "n_hot", "模拟失败")
            row = db.query(AdminAuditLog).filter_by(action="side_effect.failed").first()
        assert row is not None
        assert row.actor_phone == "system"
        assert row.target == "n_hot"
        assert "模拟失败" in row.detail

    def test_bump_failure_is_audited_not_only_logged(self, monkeypatch):
        """commit 失败：不抛给调用方，且落一条审计。"""
        from app import ranking
        from app.models import AdminAuditLog

        _seed_news("n_bump")
        calls = {"n": 0}
        real_commit = OrmSession.commit

        def flaky_commit(self):
            # 第一次炸，之后走真实路径
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("模拟热度写库失败")
            return real_commit(self)

        monkeypatch.setattr(OrmSession, "commit", flaky_commit)
        with SessionLocal() as db:
            ranking.bump_hot_score(db, "n_bump")  # 不得抛
            row = db.query(AdminAuditLog).filter_by(action="side_effect.failed").first()
        assert calls["n"] >= 2  # 说明审计那一次也真的发生了
        assert row is not None
        assert row.target == "n_bump"


class TestNotifyFailureAtomicity:
    """通知失败时评论必须一起回滚（同事务）；否则是"评论留下、通知丢失"。"""

    def test_comment_rolls_back_when_notify_fails(self, monkeypatch):
        # 500 作为响应返回，不从 TestClient 抛出
        client = TestClient(main.app, raise_server_exceptions=False)
        import app.routers.comments as rc

        monkeypatch.setattr(
            rc,
            "notify_comment_on_article",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟通知写库失败")),
        )
        _seed_news("n1")  # 否则 404，走不到通知
        h = _auth("13900000051")
        before = 0
        with SessionLocal() as db:
            before = db.query(Comment).count()
        r = client.post("/api/news/n1/comments", json={"content": "主评论"}, headers=h)
        with SessionLocal() as db:
            after = db.query(Comment).count()
        # 同事务语义：评论没有落库（不能出现"评论在、通知丢"）
        assert after == before
        assert r.status_code >= 400

    def test_like_rolls_back_when_notify_fails(self, monkeypatch):
        client = TestClient(main.app, raise_server_exceptions=False)
        import app.routers.comments as rc

        author = _user("13900000061")
        _seed_comment("c_atomic_like", author=author)
        monkeypatch.setattr(
            rc,
            "notify_comment_liked",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟通知写库失败")),
        )
        h = _auth("13900000062")
        r = client.patch(
            "/api/news/n1/comments/c_atomic_like", json={"liked": True}, headers=h
        )
        with SessionLocal() as db:
            c = db.query(Comment).filter_by(id="c_atomic_like").first()
            # 点赞计数不该留下
            assert (c.likes or 0) in (0, None)
        assert r.status_code >= 400
