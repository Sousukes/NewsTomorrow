# -*- coding: utf-8 -*-
"""一次性清理：同源重复入库的**存量**数据（E4-a）。

背景：`fetcher._stable_id = sha1(url || title)`，源改了链接（或加了跟踪参数）
就算不出同一个 id，按 id 判重随即失效——同一篇稿件在两个抓取周期里各插一行。
P1-74 起 `upsert_news` 的新增分支加了同源近邻判重，**以后不再产生**；本脚本只
处理存量。设计与实现刻意分成两件事：改历史的风险比"以后不再产生"高得多。

真实库（2026-10 探测，491 条可见资讯）实测：3 组 / 6 条，相似度全为 1.000，
且 6 条都没有任何评论 / 收藏 / 浏览历史 / 消息关联。

用法：
    # 只看会动哪些行（**默认不写库**）
    python -m scripts.prune_duplicate_news

    # 确认无误后执行
    python -m scripts.prune_duplicate_news --apply

安全边界：
- 只按**归一化标题**在同一来源内分组（`ingest.normalize_title`：NFKC + 小写 +
  去标点空白符号控制字符），**不做相似度聚类**——跨源去重已被证伪（见
  `docs/后端/E4多源转载去重设计.md` §一：跨源相似度分布与"不同稿件"重叠，
  不存在安全阈值），同源改写（0.65~0.80）也留给运营判断；
- 每组保留**互动量最高**的一条；并列取发布时间**最新**的一条——同源重复的
  两条通常相差 40~60 分钟，新那条带着源的最新修订，且与 `find_duplicate`
  的排序口径一致（"入库时保留哪条"和"清理时保留哪条"不互相矛盾）；
- 有**依赖行**（评论 / 收藏 / 浏览历史 / 消息）或**互动计数**的行**不删**，
  只报告——删了就是丢数据，交给人工判断更安全；
- 隐藏的行不参与分组（与 `find_duplicate` 同口径：已下线的稿子不该把新稿吸走，
  反过来也不该被算成重复）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.feeds.ingest import normalize_title  # noqa: E402
from app.models import Comment, Message, News, UserFavorite, UserHistory  # noqa: E402

# (展示名, 模型)：按 news_id 关联的子表，删资讯前必须确认没有残留
DEPENDENTS = (
    ("评论", Comment),
    ("收藏", UserFavorite),
    ("浏览历史", UserHistory),
    ("消息", Message),
)


def _groups(db):
    """可见资讯按 (来源标识, 归一化标题) 分组，只返回多于一条的组。"""
    rows = db.execute(
        select(
            News.id,
            News.title,
            News.source_key,
            News.published_at,
            News.comment_count,
            News.play_count,
        )
        .where(News.hidden.is_(False))
        .order_by(News.id)
    ).all()
    buckets: dict[tuple[str, str], list] = {}
    for r in rows:
        norm = normalize_title(r.title)
        if not norm:
            continue  # 标题归一后为空：判不了，也不该判（与 upsert_news 同口径）
        buckets.setdefault((r.source_key or "", norm), []).append(r)
    return [g for g in buckets.values() if len(g) > 1]


def _mass(row) -> int:
    """互动量：评论数 + 播放数。用来决定组内保留哪一条。"""
    return (row.comment_count or 0) + (row.play_count or 0)


def _dependencies(db, news_id: str) -> list[str]:
    out = []
    for label, model in DEPENDENTS:
        n = (
            db.execute(
                select(func.count()).select_from(model).where(model.news_id == news_id)
            ).scalar()
            or 0
        )
        if n:
            out.append(f"{label}×{n}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="清理同源重复入库的历史数据（默认只报告，--apply 才删除）"
    )
    p.add_argument(
        "--apply", action="store_true", help="真正执行删除（不加此参数时只报告，不写库）"
    )
    args = p.parse_args()

    with SessionLocal() as db:
        groups = _groups(db)
        if not groups:
            print("没有发现同源重复（按归一化标题在同一来源内分组）。")
            return 0

        to_delete: list[str] = []
        skipped: list[str] = []
        total = sum(len(g) for g in groups)

        print(f"发现 {len(groups)} 组同源重复（共 {total} 条，将保留 {len(groups)} 条）：")
        for g in groups:
            # 分两步是因为字符串没法直接取负：先按 id 排好（稳定的 max 会留下
            # 并列中最先出现的那个），再按 (互动量, 发布时间) 取最大
            ordered = sorted(g, key=lambda r: r.id)
            primary = max(ordered, key=lambda r: (_mass(r), str(r.published_at or "")))
            print(
                f"  [{primary.source_key or '（无来源标识）'}] {primary.title}"
            )
            for r in g:
                if r.id == primary.id:
                    print(f"      保留  {r.id}  published={r.published_at}")
                    continue
                deps = _dependencies(db, r.id)
                if deps or _mass(r):
                    reason = "、".join(deps) if deps else f"互动计数 comment/play={_mass(r)}"
                    skipped.append(r.id)
                    print(
                        f"      跳过  {r.id}  published={r.published_at}"
                        f"（{reason}，需人工判断）"
                    )
                else:
                    to_delete.append(r.id)
                    print(f"      待删  {r.id}  published={r.published_at}")

        print()
        print(f"可删除 {len(to_delete)} 条，跳过 {len(skipped)} 条（有依赖或互动数据）。")

        if not args.apply:
            print("当前为只读模式，未写库。确认无误后加 --apply 执行。")
            return 0
        if not to_delete:
            print("没有可删除的行。")
            return 0

        # 依赖已在上方逐个确认过为零，直接删不会留下孤儿行
        db.execute(News.__table__.delete().where(News.id.in_(to_delete)))
        db.commit()
        print(f"已删除 {len(to_delete)} 条。")
        return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
