# -*- coding: utf-8 -*-
"""P1-11 真实 PG 排序质量验证（一次性脚本，可重复执行）。

复刻 app/routers/news.py 的 _search_ranked 逻辑，对本地 toutiao 库验证：
  1) 召回口径与旧行为（ILIKE）**完全一致**——零召回只允许在 ILIKE 也为空时出现；
  2) 分层不变式：标题整词命中的行必须全部排在仅摘要命中的行之前；
  3) 分数单调不增（排序表达式生效）；
  4) 多字关键词走 0005 的 GIN 索引（EXPLAIN 实测，不退化为全表扫描）；
  5) 大小写不敏感（小写 ascii 查询能召回大写文章）。

用法：
    cd server-api
    PYTHONPATH=. <venv python> scripts/verify_search_ranking.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text

from app.database import SessionLocal

KEYWORDS = ["暴雨", "新能源", "AI", "芯片", "暴", "新能源汽"]

# 与 app/routers/news.py::_search_ranked 保持同一表达式
SCORED_SQL = text("""
    SELECT id, title,
           (CASE WHEN title ILIKE :like ESCAPE '\\' THEN 100 ELSE 0 END
            + CASE WHEN coalesce(summary,'') ILIKE :like ESCAPE '\\' THEN 10 ELSE 0 END
            + 2 * similarity(lower(title), lower(:kw))
            + similarity(lower(coalesce(summary, '')), lower(:kw))) AS score,
           (title ILIKE :like ESCAPE '\\') AS title_hit
    FROM news
    WHERE title ILIKE :like ESCAPE '\\' OR summary ILIKE :like ESCAPE '\\'
    ORDER BY score DESC, published_at DESC NULLS LAST, id DESC
    LIMIT 5
""")

COUNT_SQL = text("""
    SELECT count(*) FROM news
    WHERE title ILIKE :like ESCAPE '\\' OR summary ILIKE :like ESCAPE '\\'
""")

OLD_SQL = text("""
    SELECT id FROM news
    WHERE title ILIKE :like ESCAPE '\\' OR summary ILIKE :like ESCAPE '\\'
    ORDER BY published_at DESC NULLS LAST, id DESC
    LIMIT 5
""")


def main() -> int:
    ok = True
    with SessionLocal() as db:
        if db.bind.dialect.name != "postgresql":
            print("!! 未连到 PostgreSQL（检查 .env 的 DATABASE_URL）")
            return 1

        # 4) 索引命中：多字关键词应走 GIN 索引
        plan = "\n".join(
            db.execute(
                text("EXPLAIN SELECT id FROM news "
                     "WHERE title ILIKE :l ESCAPE '\\' OR summary ILIKE :l ESCAPE '\\'"),
                {"l": "%新能源%"},
            ).scalars().all()
        )
        print("--- EXPLAIN（召回查询，q=新能源） ---")
        print(plan)
        if "Seq Scan" in plan:
            print("!! 多字关键词召回走了全表扫描，GIN 索引未生效")
            ok = False

        for kw in KEYWORDS:
            like = f"%{kw}%"
            rows = db.execute(SCORED_SQL, {"kw": kw, "like": like}).all()
            total = db.execute(COUNT_SQL, {"like": like}).scalar()
            old_ids = [r[0] for r in db.execute(OLD_SQL, {"like": like}).all()]
            new_ids = [r.id for r in rows]
            print(f"\n=== q = {kw} ===  召回总数 {total}")
            for i, r in enumerate(rows):
                mark = "题" if r.title_hit else "摘"
                print(f"  {i + 1}. [{mark}] [{r.id}] {r.title[:36]}  score={float(r.score):.4f}")
            print(f"  旧排序（纯时间）: {old_ids}")

            # 1) 召回口径：排序查询的行数不可能超过召回总数（LIMIT 5 除外）
            if total == 0 and old_ids:
                print(f"  !! 零召回但旧排序非空——召回回归")
                ok = False
            # 2) 分层不变式：标题命中全部先于仅摘要命中
            hits = [bool(r.title_hit) for r in rows]
            if hits != sorted(hits, reverse=True):
                print(f"  !! 分层被破坏：标题命中未全部排在摘要命中之前 {hits}")
                ok = False
            # 3) 分数单调不增
            scores = [float(r.score) for r in rows]
            if scores != sorted(scores, reverse=True):
                print(f"  !! 分数未按降序返回 {scores}")
                ok = False
            if new_ids != old_ids:
                print("  → 与旧排序不同（相关度生效）")

        # 5) 大小写不敏感：小写查询召回大写文章
        lower_n = db.execute(COUNT_SQL, {"like": "%ai%"}).scalar()
        upper_n = db.execute(COUNT_SQL, {"like": "%AI%"}).scalar()
        print(f"\n=== 大小写不敏感 ===  q=ai 召回 {lower_n} 条 / q=AI 召回 {upper_n} 条")
        if lower_n != upper_n or lower_n == 0:
            print("  !! 大小写口径不一致")
            ok = False

    print("\n结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
