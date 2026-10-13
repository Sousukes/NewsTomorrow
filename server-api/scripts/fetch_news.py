"""RSS 抓取 CLI。

用法：
    # 全量抓取（所有启用源）
    python -m scripts.fetch_news

    # 按频道
    python -m scripts.fetch_news --channel finance

    # 按关键词
    python -m scripts.fetch_news --keyword 暴雨

    # 分页：抓第 2 页、每源 50 条（配合 --limit-per-source）
    python -m scripts.fetch_news --limit-per-source 50

    # 抓完顺带清理过期数据
    python -m scripts.fetch_news --prune

    # 只看会抓哪些源，不实际请求
    python -m scripts.fetch_news --list-sources
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.feeds.ingest import prune_old_news, run_ingest  # noqa: E402
from app.feeds.sources import RSS_SOURCES  # noqa: E402
from app.models import News  # noqa: E402
from sqlalchemy import func, select  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="抓取公开 RSS 源并写入数据库")
    p.add_argument("--channel", help="只抓指定频道（recommend/hot/tech/finance/sports/car）")
    p.add_argument("--keyword", help="按关键词过滤标题/摘要")
    p.add_argument("--limit-per-source", type=int, default=30, help="每源最多取几条")
    p.add_argument("--prune", action="store_true", help="抓取后清理过期数据")
    p.add_argument("--keep-days", type=int, default=30, help="保留天数（配合 --prune）")
    p.add_argument("--list-sources", action="store_true", help="仅列出配置的源")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")
    args = parse_args()

    if args.list_sources:
        print(f"{'源':<18}{'频道':<11}{'权重':<6}状态URL")
        for s in RSS_SOURCES:
            state = "启用" if s["enabled"] else "停用"
            print(f"  {s['name']:<16}{s['channel']:<11}{s['weight']:<6}{state}  {s['url']}")
        return 0

    with SessionLocal() as db:
        before = db.execute(select(func.count()).select_from(News)).scalar_one()
        report = run_ingest(
            db,
            channel=args.channel,
            keyword=args.keyword,
            limit_per_source=args.limit_per_source,
        )
        after = db.execute(select(func.count()).select_from(News)).scalar_one()

        pruned = prune_old_news(db, keep_days=args.keep_days) if args.prune else 0

    print()
    print(f"抓取条目  : {report.fetched}")
    print(f"新增      : {report.inserted}")
    print(f"更新      : {report.updated}")
    print(f"库中总数  : {before} → {after}")
    if args.prune:
        print(f"清理过期  : {pruned}")
    return 0 if report.fetched or report.updated else 1


if __name__ == "__main__":
    raise SystemExit(main())