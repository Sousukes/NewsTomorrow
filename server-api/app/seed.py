"""初始化数据库并写入种子数据（幂等：已有数据则跳过）。"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import DEMO_PASSWORD, DEMO_PHONE, SEED_SAMPLE_DATA
from .database import SessionLocal, engine
from .models import Comment, Message, News, User
from .security import hash_password

logger = logging.getLogger("toutiao.seed")

# 说明：`_offset_min` 为相对当前时间的"距今分钟数"，用于生成绝对时间
# published_at / created_at（排序唯一依据）；插入前会从 kwargs 中弹出。
NEWS = [
    dict(id="n1", title="国产大飞机再添新航线，商业运营迈出关键一步",
         summary="今日，国产大型客机在多条新航线上正式投入商业运营，标志着我国民用航空制造能力迈上新台阶。",
         source="新华社", comment_count=320, time="2小时前", images=["", "", ""],
         channel="tech", variant="triple", _offset_min=120),
    dict(id="n2", title="多地出台楼市新政，刚需购房门槛再降低",
         summary="近期多个城市优化住房信贷政策，首套房贷利率与首付比例同步下调，刚需家庭受益明显。",
         source="财经周刊", comment_count=156, time="3小时前", cover="",
         channel="finance", variant="left-right", _offset_min=180),
    dict(id="n3", title="中央气象台发布暴雨蓝色预警，这些地区需注意防范",
         summary="未来24小时，华南、江南南部等地将出现大到暴雨，局地伴有雷暴大风，请提前做好排涝准备。",
         source="中国天气", comment_count=89, time="1小时前", is_top=True,
         channel="recommend", variant="text", _offset_min=60),
    dict(id="n4", title="现场直击：国产航母编队展开远海实战化训练",
         summary="编队连续航行数昼夜，完成舰载机起降、编队机动等多课目训练，体系作战能力得到检验。",
         source="军事频道", play_count=128000, time="5小时前", cover="",
         channel="video", variant="video", _offset_min=300),
    dict(id="n5", title="新能源汽车出口连续三月创新高，海外市场持续扩容",
         summary="前五月新能源汽车出口同比大幅增长，欧洲与东南亚成为主要增量市场。",
         source="新华财经", comment_count=245, time="4小时前", images=["", "", ""],
         channel="tech", variant="triple", _offset_min=240),
    dict(id="n6", title="教育部：推动义务教育阶段课后服务全覆盖",
         summary="各地统筹校内外资源，丰富科普、体育、艺术等课程，缓解家长接送与看护压力。",
         source="人民日报", comment_count=512, time="6小时前", cover="",
         channel="recommend", variant="left-right", _offset_min=360),
]

COMMENTS = [
    dict(id="c1", news_id="n4", user_name="观察者小张", seed="c1",
         content="这篇报道角度很扎实，把关键节点都讲清楚了，期待后续跟进。", time="1小时前", likes=86,
         _offset_min=60),
    dict(id="c2", news_id="n4", user_name="沿海居民", seed="c2",
         content="我们这边昨天就开始排涝了，预警还是挺及时的，点赞气象台。", time="42分钟前", likes=53,
         _offset_min=42),
    dict(id="c3", news_id="n4", user_name="理性看世界", seed="c3",
         content="希望落地时能多听听基层声音，别只停留在文件层面。", time="20分钟前", likes=12,
         _offset_min=20),
]

MESSAGES = [
    dict(id="m1", type="system", title="欢迎使用明日头条",
         summary="关注你感兴趣的频道，第一时间获取重要资讯推送。", news_id=None, time="刚刚"),
    dict(id="m2", type="comment", title="有人评论了你关注的资讯",
         summary="观察者小张：国产航母编队训练报道角度很扎实，值得期待后续。", news_id="n4", time="1小时前"),
    dict(id="m3", type="recommend", title="今日热点速览",
         summary="暴雨蓝色预警、楼市新政、国产大飞机新航线，点击查看详情。", news_id="n3", time="2小时前"),
    dict(id="m4", type="system", title="账号安全提醒",
         summary="建议定期检查登录设备，异常登录将及时通知你。", news_id=None, time="1天前"),
]


def _ago(minutes: int) -> str:
    """距今 N 分钟的绝对 ISO 时间（UTC）。"""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _pop_offset(row: dict) -> int:
    return int(row.pop("_offset_min", 0))


# schema 由 Alembic 单轨管理；这些表必须存在，否则启动即给出明确指引
_REQUIRED_TABLES = ("users", "news", "comments", "messages")


def _assert_schema_ready() -> None:
    """校验库结构已就绪（由 `alembic upgrade head` 建立）。

    此前本函数会调用 `create_all()` 就地建表，与 Alembic 形成双轨管理：
    create_all 补建的物件不会被记入 `alembic_version`，导致后续
    `alembic upgrade head` 因"对象已存在"失败，且环境间结构分叉。
    现在改为**只校验不建表**，缺表时给出可执行的修复指引。
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    missing = [t for t in _REQUIRED_TABLES if not inspector.has_table(t)]
    if not missing:
        return
    raise RuntimeError(
        "[schema] 数据库缺少表：%s。\n"
        "schema 现由 Alembic 单轨管理，请先执行：\n"
        "    cd server-api && alembic upgrade head\n"
        "（测试环境请在 fixture 内自行 Base.metadata.create_all）"
        % "、".join(missing)
    )


def init_db() -> None:
    """补齐**缺失**的种子数据（幂等，可反复执行）。

    注意：不是"表空才插"——抓取任务的时效清理可能删掉部分种子资讯，
    若只在表空时插入，会导致引用这些资讯的评论/消息因外键失败而启动报错。
    因此这里按主键逐条补齐，并先清理孤立引用。

    建表不在本函数内：schema 由 Alembic 负责，见 `_assert_schema_ready()`。
    """
    _assert_schema_ready()
    db = SessionLocal()
    try:
        # 先修外键：清理指向已不存在资讯的评论/消息
        _sync_orphans(db)

        # 示例数据播种开关（生产默认关闭；测试显式关闭以保证确定性）
        if not SEED_SAMPLE_DATA:
            return

        if db.query(User).filter(User.phone == DEMO_PHONE).first() is None:
            db.add(
                User(
                    phone=DEMO_PHONE,
                    password_hash=hash_password(DEMO_PASSWORD),
                    name="明日头条用户",
                )
            )

        # 补齐缺失的种子资讯（已存在则跳过，不覆盖抓取来的真实数据）
        have_news = {row[0] for row in db.execute(select(News.id)).all()}
        restored = 0
        for row in NEWS:
            row = dict(row)
            offset = _pop_offset(row)
            if row["id"] not in have_news:
                db.add(News(published_at=_ago(offset), **row))
                restored += 1

        # 补齐种子评论/消息（仅当其关联的资讯存在时）
        # 种子评论一律是主评论（parent_id/root_id 为 NULL）；likes 仅作展示计数，
        # 不造 comment_likes 行（无人点赞，liked 恒为 false）。
        have_comments = {row[0] for row in db.execute(select(Comment.id)).all()}
        for row in COMMENTS:
            row = dict(row)
            offset = _pop_offset(row)
            if row["id"] not in have_comments and db.get(News, row["news_id"]) is not None:
                db.add(Comment(created_at=_ago(offset), **row))

        have_msgs = {row[0] for row in db.execute(select(Message.id)).all()}
        for row in MESSAGES:
            if row["id"] not in have_msgs and (
                row["news_id"] is None or db.get(News, row["news_id"]) is not None
            ):
                db.add(Message(**row))

        db.commit()
        if restored:
            logger.info("补齐种子资讯 %d 条", restored)
    finally:
        db.close()


def _sync_orphans(db) -> None:
    """清理指向已不存在资讯的评论/消息（外键为RESTRICT 时需显式处理）。

    抓取任务的时效清理可能删除资讯，但评论/消息可能仍引用它；
    本函数在启动时兜底，保证外键完整性。
    """
    from .models import Comment, Message

    news_ids = {row[0] for row in db.execute(select(News.id)).all()}

    stale_comments = [c for c in db.query(Comment).all() if c.news_id not in news_ids]
    for c in stale_comments:
        db.delete(c)
    if stale_comments:
        logger.info("清理孤立评论 %d 条", len(stale_comments))

    # 消息的 news_id 是 ON DELETE SET NULL，这里做二次兜底
    for m in db.query(Message).all():
        if m.news_id is not None and m.news_id not in news_ids:
            m.news_id = None
    db.commit()
