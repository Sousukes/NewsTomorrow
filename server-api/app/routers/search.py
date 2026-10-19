"""搜索联想路由（E2）。

三源合并，优先级 `hot`（热搜邻居）> `correct`（相似词纠错）> `title`（标题前缀）：

| 源 | 输入 | 解决什么 |
| --- | --- | --- |
| `hot` | `search_logs` 窗口内聚合，前缀匹配 | "很多人搜过、且以我输入开头"的词，最可能是用户想找的 |
| `correct` | 同上候选池，三元组相似度 | 输错一个字（"新能源汽吃" → "新能源汽车"）时的出路 |
| `title` | 资讯标题前缀 | 内容侧的真实说法，热词里还没有的新词也能出现 |

三条刻意设计：

1. **联想不写 `search_logs`**。只有真正提交搜索才计入热搜榜——否则边打字
   就把「新能源汽」「新能源汽⻋」这类中间态写进榜里，热搜会被输入过程污染
   （而且越长的词中间态越多，污染越严重）。
2. **不下发资讯内容**。联想只给词；"选词"和"看结果"是两件事，混在一起会互相
   干扰，也让响应里出现前端用不上的字段。
3. **候选池有界**。相似度在 **Python 侧**算而不是用 SQL `similarity()`：
   `search_logs.term` 没有 trigram 索引，SQL 侧同样要全扫，没有收益；而 SQL
   侧是 PG 专属，会让 SQLite / 无扩展环境整整少一个联想源。候选池取窗口内
   搜索次数 Top-N，代价与库规模脱钩。
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import News
from ..response import ok
from ..search_stats import HOT_WINDOW_DAYS, TERM_MAX, recent_terms
from .news import _escape_like

router = APIRouter(prefix="/api/search", tags=["search"])
logger = logging.getLogger("toutiao.search")

# 默认/上限返回条数：下拉里超过 20 条就没有意义了
SUGGEST_LIMIT_DEFAULT = 8
SUGGEST_LIMIT_MAX = 20

# 候选池大小：hot 与 correct 两源的共同输入。有界才能让联想成本与库规模脱钩，
# 代价是"冷门但正确的纠错词"可能不在池里——可接受，池内已经是搜得最多的词。
SUGGEST_POOL_SIZE = 200

# 纠错的两条判据（取**或**，见 `suggest_terms`）：
# `MIN_SIMILARITY` 抓住"长词里错一个字"；`edit_distance_at_most_one` 抓住
# "短词里错一个字"。0.3 是量出来的：4 字词错一个字只有 0.25，再降就会把
# "中国人民 / 中国银行"（共享"  中"" 中国"，0.25，但并非错字）也当成纠错。
# `LEN_TOLERANCE` 先挡掉长度差过大的组合（"暴雨"不会是"新能源汽车"的错词）。
SUGGEST_CORRECT_LEN_TOLERANCE = 2
SUGGEST_CORRECT_MIN_SIMILARITY = 0.3

# 标题源：长标题不适合当下拉项，且搜整条标题基本只回自己那一篇——
# 那更适合由搜索列表承担，不是联想的职责。
SUGGEST_TITLE_MAX_LEN = 30


def normalize_term(q: str) -> str:
    """联想的入参归一化：去空白 + 截到模型宽度。空串表示「不联想」。

    路由与 `suggest_terms` 共用，避免"响应里的 keyword 与实际检索的词不一致"。
    """
    return (q or "").strip()[:TERM_MAX]


def _trigrams(s: str) -> set[str]:
    """字符三元组集合，复刻 pg_trgm 的**边界补齐**（两个前导空格 + 一个尾随空格）。

    补齐不是细节而是必需：不补齐时两个三字词只要有一个字不同，三元组集合就
    完全不相交，相似度恒为 0——"近期词" 与 "近期祠" 会毫无关系。补齐后它们
    共享 `{"  近", " 近期"}`，才是可比较的邻居。
    """
    if not s:
        return set()
    padded = f"  {s} "
    if len(padded) < 3:
        return {padded}
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def trigram_similarity(a: str, b: str) -> float:
    """字符三元组 Jaccard 相似度（0~1）——与 pg_trgm 的 `similarity()` 同一公式。

    为什么在 Python 侧算：见模块 docstring 第 3 条（无索引可吃 + 方言无关）。

    **它只用来排序，不单独用来判定"是不是错字"**：单个字替换会破坏 3 个三元组，
    短词的相似度因此很低（4 字词错一个字只有 2/8 = 0.25），拿它当门槛会把
    "暴雨篮色 → 暴雨蓝色"这种最常见的中文错字全漏掉。判定交给下面的编辑距离。
    """
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def edit_distance_at_most_one(a: str, b: str) -> bool:
    """编辑距离是否 ≤ 1，即"打错 / 打多 / 打漏一个字"。

    只判 ≤1 而不求精确值：候选池是有界的短串集合，早退一次就能省掉绝大多数
    O(n×m) 展开。等长只可能是替换，长度差 1 只可能是插入或删除——一次扫描即可。

    为什么用它**而不是**只靠相似度：见 `trigram_similarity` 的说明。反过来也
    只靠它会漏掉"长词里错一个字"（相似度很高但距离可能仍是 1，其实两都满足），
    所以下面是**或**的关系，不是替换。
    """
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        diff += 1
        if diff > 1:
            return False
        if la == lb:
            i += 1
            j += 1  # 替换
        elif la > lb:
            i += 1  # a 多一个字
        else:
            j += 1  # b 多一个字
    # 尾部长出来的部分各算一次编辑
    return diff + (la - i) + (lb - j) <= 1


def _title_terms(db: Session, keyword: str, limit: int) -> list[str]:
    """以 `keyword` 开头的资讯标题（新发布的在前），过长的不采纳。"""
    like = f"{_escape_like(keyword)}%"
    rows = (
        db.query(News.title)
        .filter(
            News.title.ilike(like, escape="\\"),
            # P1-20：已下架内容不参与联想（搜得到却点不开是更差的体验）
            News.hidden.is_(False),
        )
        .order_by(func.coalesce(News.published_at, "").desc(), News.id.desc())
        # 多取一些再按长度过滤：不过滤时前 N 条可能全是超长标题，一次就取空
        .limit(limit * 3)
        .all()
    )
    out: list[str] = []
    for (title,) in rows:
        t = (title or "").strip()
        if not t or len(t) > SUGGEST_TITLE_MAX_LEN:
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def suggest_terms(
    db: Session,
    q: str,
    limit: int = SUGGEST_LIMIT_DEFAULT,
    days: int = HOT_WINDOW_DAYS,
    now=None,
) -> list[dict]:
    """三源合并的联想词。纯函数（只吃 session），便于直接单测。

    去重按**小写**比较（同一词的不同大小写只出一次），返回保留原始写法。
    同一 `kind` 内：`hot` 按次数倒序、`correct` 按相似度倒序，两者都以
    次数倒序 + 词字典序兜底（顺序确定，不依赖数据库的返回顺序）。
    """
    keyword = normalize_term(q)
    if not keyword:
        return []
    limit = max(1, min(int(limit or SUGGEST_LIMIT_DEFAULT), SUGGEST_LIMIT_MAX))
    now = now or datetime.now(timezone.utc)
    kw = keyword.lower()

    pool = recent_terms(db, days=days, now=now, limit=SUGGEST_POOL_SIZE)

    hot: list[dict] = []
    correct: list[tuple[float, int, str]] = []
    for term, count in pool:
        low = term.lower()
        if low.startswith(kw):
            hot.append({"term": term, "kind": "hot", "hitCount": count})
            continue
        # 与输入完全相同的词不是"纠错"，是废话
        if low == kw:
            continue
        if abs(len(term) - len(keyword)) > SUGGEST_CORRECT_LEN_TOLERANCE:
            continue
        sim = trigram_similarity(low, kw)
        # 两条判据取**或**：相似度抓住"长词里错一个字"（共享三元组仍很多），
        # 编辑距离抓住"短词里错一个字"（三元组被破坏得比例太大，相似度判不出来）。
        if sim >= SUGGEST_CORRECT_MIN_SIMILARITY or edit_distance_at_most_one(low, kw):
            correct.append((sim, count, term))

    hot.sort(key=lambda x: (-x["hitCount"], x["term"]))
    # 相似度优先（这是纠错唯一有意义的排序键），再用与 hot 相同的兜底规则
    correct.sort(key=lambda x: (-x[0], -x[1], x[2]))

    out: list[dict] = []
    seen: set[str] = set()

    def _push(term: str, kind: str, hit_count: int) -> None:
        key = term.lower()
        if key in seen:
            return
        seen.add(key)
        out.append({"term": term, "kind": kind, "hitCount": hit_count})

    for item in hot:
        _push(item["term"], "hot", item["hitCount"])
    for _sim, count, term in correct:
        _push(term, "correct", count)
    # 标题类没有"被搜过多少次"，给 0；前端按 kind 决定是否展示次数
    for t in _title_terms(db, keyword, limit):
        _push(t, "title", 0)

    return out[:limit]


@router.get("/suggest", response_model=None)
def suggest(
    q: str = Query("", max_length=TERM_MAX),
    limit: int = Query(SUGGEST_LIMIT_DEFAULT, ge=1, le=SUGGEST_LIMIT_MAX),
    days: int = Query(HOT_WINDOW_DAYS, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """搜索联想：热搜邻居 / 相似词纠错 / 标题前缀，三源合并去重。

    - **不需要登录**（热搜词是公开统计，相似词来自公共日志且不含用户字段），
      因此也就不返回任何与当前用户相关的东西——避免把个人行为混进共享结果。
    - **不写 `search_logs`**：联想是输入过程，不是搜索行为（见模块 docstring）。
    - 空 `q` / 纯空格 → 200 + 空列表（不是错误：输入框刚聚焦就该是这个结果）。
    - `keyword` 回显归一化后的词，前端可据此判断"这份建议属于哪次输入"。

    响应：`{ list: [{ term, kind, hitCount }], keyword }`，`kind` 取
    `hot` | `correct` | `title`。
    """
    return ok({"list": suggest_terms(db, q, limit=limit, days=days), "keyword": normalize_term(q)})
