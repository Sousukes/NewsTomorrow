"""P1-82：旁路副作用的事务边界。

修的是三处「两次 commit」留下的中间态：
1. 评论与通知——分开提交会「评论成功、通知永久丢失」且只有一条 warning 日志；
2. 点赞与通知——同上；
3. 举报写入与阈值处置——分开提交会「举报成功、处置没生效」且无自愈路径。

附带一条可观测性：热度更新失败必须落审计（side_effect.failed），
而不是只写日志——日志在容器里没人看。

运行：
    cd server-api
    pytest tests/test_side_effects.py -q
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
        """回复他人评论 → 通知与评论同一次提交，接口返回即可见。"""
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
        """主评论 → 通知同样评论过该资讯的其他用户（不含触发者自己）。"""
        _seed_comment("c_other", author=_user("13900000021"))
        h = _auth("13900000022")
        r = client.post("/api/news/n1/comments", json={"content": "主评论"}, headers=h)
        assert r.status_code == 200
        with SessionLocal() as db:
            msgs = db.query(Message).filter_by(type="comment").all()
            assert len(msgs) == 1
            assert msgs[0].user_id == _user("13900000021")

    def test_like_notifies_author_in_one_commit(self, client):
        """点赞 → 通知与点赞同一提交。"""
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
        """取消赞不通知（避免打扰）。"""
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
        """热度失败 → 落 `side_effect.failed` 审计，且绝不向外抛（兜底中的兜底）。"""
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
        """`bump_hot_score` 的 commit 失败：不抛给调用方，且落一条审计。"""
        from app import ranking
        from app.models import AdminAuditLog

        _seed_news("n_bump")
        calls = {"n": 0}
        real_commit = OrmSession.commit

        def flaky_commit(self):
            # 第一次（热度写库）炸，之后（审计写库）走真实路径
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
    """通知失败时的行为差异，才是「同事务」与「两次提交」的分水岭。

    只看成功路径的话，两种写法的最终状态一模一样——上面的正向用例因此
    **无法**区分它们。必须让通知失败：
    - 同事务：评论/点赞**也回滚**（接口报错，不留孤儿）；
    - 两次提交：评论留下、通知丢失，且只有一条日志。
    """

    def test_comment_rolls_back_when_notify_fails(self, monkeypatch):
        # 让异常作为 500 响应返回，而不是从 TestClient 抛出
        client = TestClient(main.app, raise_server_exceptions=False)
        import app.routers.comments as rc

        monkeypatch.setattr(
            rc,
            "notify_comment_on_article",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟通知写库失败")),
        )
        _seed_news("n1")  # 没这条会 404，根本走不到通知（那样这条永远绿）
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
            # 点赞与通知同事务：点赞计数与点赞行都不该留下
            assert (c.likes or 0) in (0, None)
        assert r.status_code >= 400
