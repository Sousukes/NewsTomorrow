# -*- coding: utf-8 -*-
"""真实 PG 搜索排序质量验证（一次性脚本，可重复执行）。

对本地 toutiao 库验证：
  1) 召回口径不倒退：正文入召回（E2）后，召回总数只增不减，且旧（标题+摘要）
     能召回的行**一定还在**；
  2) 分层不变式：标题命中 > 摘要命中 > 正文命中，三层严格有序（E2 新增正文层）；
  3) 分数单调不增（排序表达式生效）；
  4) 多字关键词走 0005 的 GIN 索引（EXPLAIN 实测，不退化为全表扫描）；
  5) 大小写不敏感（小写 ascii 查询能召回大写文章）；
  6) **本脚本的 SQL 与 `app/routers/news.py::_search_ranked` 结果一致**。

第 6 条是 P1-70 加的：本脚本原本纯靠"人肉同步"复刻实现，加正文召回时它
已经悄悄过期了（少一个 `content` 项，照样全绿）。与其指望下次还记得同步，
不如让脚本自己跟真实实现对一遍——不一致就直接红。

用法：
    cd server-api
    PYTHONPATH=. <venv python> scripts/verify_search_ranking.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.routers.news import _search_ranked  # noqa: E402

KEYWORDS = ["暴雨", "新能源", "AI", "芯片", "暴", "新能源汽"]

# 与 app/routers/news.py::_search_ranked 保持同一表达式（权重见那里的常量）。
# `layer` 把三档命中摊平成 0/1/2，便于直接断言分层有序。
SCORED_SQL = text("""
    SELECT id, title,
           (CASE WHEN title ILIKE :like ESCAPE '\\' THEN 100 ELSE 0 END
            + CASE WHEN coalesce(summary,'') ILIKE :like ESCAPE '\\' THEN 10 ELSE 0 END
            + CASE WHEN coalesce(content,'') ILIKE :like ESCAPE '\\' THEN 1 ELSE 0 END
            + 2 * similarity(lower(title), lower(:kw))
            + similarity(lower(coalesce(summary, '')), lower(:kw))) AS score,
           (CASE WHEN title ILIKE :like ESCAPE '\\' THEN 0
                 WHEN coalesce(summary,'') ILIKE :like ESCAPE '\\' THEN 1
                 ELSE 2 END) AS layer
    FROM news
    WHERE title ILIKE :like ESCAPE '\\'
       OR summary ILIKE :like ESCAPE '\\'
       OR content ILIKE :like ESCAPE '\\'
    ORDER BY score DESC, coalesce(published_at,'') DESC, id DESC
    LIMIT 5
""")

# 当前口径（含正文）
COUNT_SQL = text("""
    SELECT count(*) FROM news
    WHERE hidden = false
      AND (title ILIKE :like ESCAPE '\\'
        OR summary ILIKE :like ESCAPE '\\'
        OR content ILIKE :like ESCAPE '\\')
""")

# E2 之前的口径（只有标题 + 摘要），用来证明"正文召回确实多带来了东西"
COUNT_SQL_PRE_E2 = text("""
    SELECT count(*) FROM news
    WHERE hidden = false
      AND (title ILIKE :like ESCAPE '\\'
        OR summary ILIKE :like ESCAPE '\\')
""")

# 旧排序（纯时间，无相关度）——只用于对比展示
OLD_SQL = text("""
    SELECT id FROM news
    WHERE hidden = false
      AND (title ILIKE :like ESCAPE '\\'
        OR summary ILIKE :like ESCAPE '\\'
        OR content ILIKE :like ESCAPE '\\')
    ORDER BY coalesce(published_at,'') DESC, id DESC
    LIMIT 5
""")


def probe_content_only(db):
    """挖一个「只出现在正文里」的关键词：`(词, 该词所在资讯 id, 旧条数, 新条数)`。

    固定关键词表证明不了 E2——那些词恰好都不在正文里，脚本会一直打印
    "正文贡献 +0"，看着像功能没生效，其实是**测错了对象**。所以动态挖一个。
    找不到时返回 None（本库正文太短/太少），那是跳过而不是失败。
    """
    rows = db.execute(
        text("""
        SELECT id, title, coalesce(summary,'') AS summary, content
        FROM news
        WHERE hidden = false AND length(coalesce(content,'')) >= 20
        ORDER BY length(content) DESC LIMIT 40
        """)
    ).all()
    for nid, title, summary, content in rows:
        for size in (3, 2):
            for start in range(0, max(0, len(content) - size), 3):
                cand = content[start : start + size].strip()
                # 跨行片段（含 \n）不是"词"，跳过
                if len(cand) != size or "\n" in cand:
                    continue
                if cand in title or cand in summary:
                    continue
                like = f"%{cand}%"
                old_n = db.execute(COUNT_SQL_PRE_E2, {"like": like}).scalar()
                new_n = db.execute(COUNT_SQL, {"like": like}).scalar()
                if old_n == 0 and new_n > 0:
                    return cand, nid, old_n, new_n
    return None


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
        print("--- EXPLAIN（召回升 title/summary，q=新能源） ---")
        print(plan)
        if "Seq Scan" in plan:
            print("!! 多字关键词召回走了全表扫描，GIN 索引未生效")
            ok = False

        for kw in KEYWORDS:
            like = f"%{kw}%"
            rows = db.execute(SCORED_SQL, {"kw": kw, "like": like}).all()
            total = db.execute(COUNT_SQL, {"like": like}).scalar()
            pre_e2 = db.execute(COUNT_SQL_PRE_E2, {"like": like}).scalar()
            old_ids = [r[0] for r in db.execute(OLD_SQL, {"like": like}).all()]
            new_ids = [r.id for r in rows]
            print(f"\n=== q = {kw} ===  召回 {total} 条（E2 前 {pre_e2} 条，正文贡献 +{total - pre_e2}）")
            for i, r in enumerate(rows):
                mark = ("题", "摘", "正")[int(r.layer)]
                print(f"  {i + 1}. [{mark}] [{r.id}] {r.title[:36]}  score={float(r.score):.4f}")
            print(f"  旧排序（纯时间）: {old_ids}")

            # 1) 召回不倒退：正文只做加法
            if total < pre_e2:
                print(f"  !! 召回倒退：{pre_e2} → {total}")
                ok = False
            if total == 0 and old_ids:
                print("  !! 零召回但旧排序非空——召回回归")
                ok = False
            # 2) 分层不变式：0（标题）→ 1（摘要）→ 2（正文）必须非降
            layers = [int(r.layer) for r in rows]
            if layers != sorted(layers):
                print(f"  !! 分层被破坏：未做到 标题 → 摘要 → 正文 有序 {layers}")
                ok = False
            # 3) 分数单调不增
            scores = [float(r.score) for r in rows]
            if scores != sorted(scores, reverse=True):
                print(f"  !! 分数未按降序返回 {scores}")
                ok = False

            # 6) 与真实实现对一遍：本脚本的 SQL 一旦漏同步（少一项/权重写错），
            #    光看本脚本的输出是看不出来的——它照样"全绿"。
            live, live_total = _search_ranked(db, kw, 1, 5)
            if [n.id for n in live] != new_ids or live_total != total:
                print(
                    f"  !! 与 _search_ranked 不一致：脚本 {new_ids}/{total}"
                    f" vs 实现 {[n.id for n in live]}/{live_total}"
                )
                ok = False
            if new_ids != old_ids:
                print("  → 与旧排序不同（相关度生效）")

        # 7) 正文召回（E2）：动态挖一个只出现在正文里的词，证明它现在能召回
        print("\n=== 正文召回（E2，动态探测） ===")
        probe = probe_content_only(db)
        if probe is None:
            print("  (本库没有「只出现在正文里」的关键词——跳过，不是失败)")
        else:
            kw, nid, old_n, new_n = probe
            items, total = _search_ranked(db, kw, 1, 10)
            ids = [n.id for n in items]
            print(f"  探测词「{kw}」  旧口径 {old_n} 条 → 新口径 {new_n} 条")
            print(f"  它来自 [{nid}]，是否被召回: {nid in ids}")
            if old_n != 0:
                print("  !! 探测词并非只出现在正文里（旧口径就有结果），探测本身失效")
                ok = False
            if nid not in ids:
                print("  !! 正文命中的那条没被 _search_ranked 召回——正文入召回没生效")
                ok = False

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
