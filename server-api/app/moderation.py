"""评论内容审核：敏感词匹配引擎（P1-23）。

接入位置
--------
**唯一的写入口** `POST /api/news/{news_id}/comments`（主评论与回复都走它），
在落库**之前**检测。刻意不放在读路径：列表/详情页每秒被读很多次，而评论
一天写不了几条——把成本放在写路径上，读路径零开销。

为什么不在前端校验
------------------
前端校验只能防误触，防不了绕过（直接调 API）。服务端是唯一可信边界。
前端的友好提示只是"少一次往返"，不改变任何安全语义。

匹配策略（对抗最常见的四类绕过）
--------------------------------
1. **大小写**：`casefold()`（比 lower 更彻底，覆盖 ß → ss 等）；
2. **全角/半角与兼容字符**：逐字符 NFKC（`ｆｕｃｋ` → `fuck`、`①` → `1`）；
3. **空格 / 标点 / 零宽字符干扰**：归一化时**只保留字母与数字**
   （Unicode 类别 L*/N*，CJK 与假名谚文都算 L），其余全部丢弃——
   于是「傻 逼」「傻-逼」「傻\u200b逼」归一后与「傻逼」完全相同；
4. **重复与变形**：不做（如「好好好好好」压缩）。这类规则误杀率高，
   且会让"我真的很喜欢"被判成刷屏——交给人工复核，别让机器猜。

归一化**保留原文下标映射**：脱敏要改的是**原文**，不是归一化后的串。
先归一化再按下标去原文里替换，才能做到「改对了位置」。

性能
----
评论正文上限 500 字。单次扫描是 O(n + 命中数) 的 AC 自动机（Aho-Corasick），
不是「逐词 `in` 判断」的 O(词库规模 × n)。词库 2000 词、正文 500 字时：
- 命中检测 ≈ 数十微秒级（纯内存，无 IO）；
- **检测路径不查库**——词库缓存在进程内，只有 TTL 到期才回库查一次
  「指纹」（`MAX(updated_at)` + `COUNT(*)`），指纹没变就继续用旧索引；
- 重建索引（词库变更时）是唯一有成本的动作，2000 词约几十毫秒，
  且被 TTL 与主动失效限制在"改词时"发生，不在请求关键路径上。

热更新
------
- 管理端增删改词 → `engine.invalidate()` → **本进程**下一次检测即重建（保存即生效）；
- 集群下其他实例靠 TTL 兜底（默认 30s 内感知）。刻意不做跨进程广播：
  为一个"晚 30 秒生效"换一个 Redis 依赖不划算；词库的时效性要求是"分钟级"。

误杀控制（这一节的每条都是被真实场景逼出来的）
----------------------------------------------
- **默认 mask 而非 block**：机器的判断是概率性的。"先脱敏 + 让人看一眼"
  的代价远小于"把用户的话直接吞掉"——后者是不可逆的体验伤害；
- **白名单词 `allow`**：短词误杀的专用解法。单字「射」会命中「射击」
  「射手」，把后者加进白名单，其区间即被豁免；
- **最短词长**（默认 2）：1 字词在正常表达里遍地都是，挡在入库这一步；
- **昵称命中一律降级为 mask**：昵称是账号级属性，用户无法在评论框里改它。
  因为昵称命中而拒绝发言 = 让用户带着"我什么都没做错"的困惑被永久禁言；
  脱敏昵称既挡住了展示面的风险，又不阻断表达；
- **提示不回显命中词**：`block` 只说"包含不当内容"，绝不说命中了哪个词——
  回显等于给绕过者一份"哪些词不能写"的对照表，还附带教他怎么变形；
- **命中日志留原文片段**：脱敏不可逆，误杀复盘与申诉只能靠它。
"""
from __future__ import annotations

import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import func, select

from .config import (
    MODERATION_CACHE_TTL_SECONDS,
    MODERATION_CHECK_NAME,
    MODERATION_DEFAULT_ACTION,
    MODERATION_ENABLED,
    MODERATION_MASK_CHAR,
    MODERATION_MAX_WORDS,
)
from .models import SensitiveWord

# 词库动作的三种取值
ACTION_BLOCK = "block"  # 直接拦截，不落库
ACTION_MASK = "mask"  # 脱敏后发布 + 待人工复核
ACTION_ALLOW = "allow"  # 白名单：命中的区间豁免（降误杀）
VALID_ACTIONS = (ACTION_BLOCK, ACTION_MASK, ACTION_ALLOW)

# 动作严厉程度：区间重叠时取最严的那个（宁可脱敏也别放过）
_SEVERITY = {ACTION_ALLOW: 0, ACTION_MASK: 1, ACTION_BLOCK: 2}

FIELD_CONTENT = "content"
FIELD_NAME = "name"

# 命中片段的上下文宽度（单侧字符数）：判断误杀需要语境
SNIPPET_CONTEXT = 30


@dataclass
class Hit:
    """一次命中（合并后的**原文区间**）。"""

    word: str  # 命中的词（库中原词，非归一化形式）
    action: str  # block | mask（allow 已被豁免剔除）
    field: str  # content | name
    start: int  # 原文下标（含）
    end: int  # 原文下标（含）


@dataclass
class Inspection:
    """一次检测的结果。

    `blocked` 为真时调用方应拒绝提交；`masked` 为真时应把评论置为待复核。
    两者可以同时为假（没命中）。
    """

    content: str
    name: str
    hits: list[Hit] = field(default_factory=list)
    blocked: bool = False
    masked: bool = False


def normalize(text: str) -> tuple[str, list[int]]:
    """归一化：返回 `(归一化串, 下标映射)`，`下标映射[j]` 为归一化串第 j 个字符
    在**原文**中的下标。

    三步：NFKC（全半角/兼容字符）→ casefold（大小写）→ 只留字母数字
    （丢弃空格、标点、零宽、符号、emoji）。
    """
    chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        # 逐字符 NFKC：整串 normalize 会改变长度（如 ﬁ → fi），
        # 逐字符处理才能保住下标映射
        folded = unicodedata.normalize("NFKC", ch).casefold()
        for c in folded:
            # L* = 字母（含 CJK / 假名 / 谚文），N* = 数字。
            # 其余（Zs 空格、Po 标点、Cf 零宽、So 符号/emoji）一律丢弃——
            # 这一步就是「加空格 / 插零宽 / 用标点隔断」绕过的解法。
            if unicodedata.category(c)[0] in ("L", "N"):
                chars.append(c)
                index_map.append(i)
    return "".join(chars), index_map


def snippet_of(text: str, start: int, end: int) -> str:
    """截取命中位置附近的原文（带上下文），供命中日志复盘误杀。"""
    lo = max(0, start - SNIPPET_CONTEXT)
    hi = min(len(text), end + 1 + SNIPPET_CONTEXT)
    body = text[lo:hi]
    if lo > 0:
        body = "…" + body
    if hi < len(text):
        body = body + "…"
    return body[:300]


class _Automaton:
    """Aho-Corasick 自动机：一次扫描找出全部模式串，复杂度 O(n + 命中数)。

    词库可以上千条，逐词 `word in text` 是 O(词库 × n)——评论正文 500 字
    乘 2000 词就是百万次比较，而 AC 只扫一遍文本。
    """

    __slots__ = ("goto", "fail", "out")

    def __init__(self, patterns: dict[str, str]) -> None:
        # patterns: 归一化词 -> 原词（只用来回查，匹配本身只用归一化词）
        self.goto: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[list[str]] = [[]]
        for word in patterns:
            if word:
                self._insert(word)
        self._build_fail()

    def _insert(self, word: str) -> None:
        node = 0
        for ch in word:
            nxt = self.goto[node].get(ch)
            if nxt is None:
                self.goto.append({})
                self.fail.append(0)
                self.out.append([])
                nxt = len(self.goto) - 1
                self.goto[node][ch] = nxt
            node = nxt
        self.out[node].append(word)

    def _build_fail(self) -> None:
        queue: deque[int] = deque()
        for nxt in self.goto[0].values():
            queue.append(nxt)
        while queue:
            cur = queue.popleft()
            # 继承 fail 链上的输出：AC 的 fail 指向"当前串的最长真后缀"，
            # 走 fail 等价于找到所有以当前位置结尾的模式串（BFS 保证 fail 已处理）
            self.out[cur] = self.out[cur] + self.out[self.fail[cur]]
            for ch, nxt in self.goto[cur].items():
                f = self.fail[cur]
                while f and ch not in self.goto[f]:
                    f = self.fail[f]
                target = self.goto[f].get(ch, 0)
                # 根的直接子节点会指向自己（自环），必须归零
                self.fail[nxt] = 0 if target == nxt else target
                queue.append(nxt)

    def search(self, text: str) -> list[tuple[int, int, str]]:
        """返回 `[(起始下标, 结束下标(含), 模式串)]`，下标为归一化串的下标。"""
        node = 0
        found: list[tuple[int, int, str]] = []
        for i, ch in enumerate(text):
            while node and ch not in self.goto[node]:
                node = self.fail[node]
            node = self.goto[node].get(ch, 0)
            for word in self.out[node]:
                found.append((i - len(word) + 1, i, word))
        return found


class ModerationEngine:
    """词库缓存 + 检测入口。进程内单例（模块底部 `engine`）。

    线程安全：`_rebuild` 在锁内；指纹查询在锁外（最坏多查一次，无所谓）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._auto: _Automaton | None = None
        self._words: dict[str, str] = {}  # 归一化词 -> 原词
        self._actions: dict[str, str] = {}  # 归一化词 -> 动作
        self._fingerprint = ""
        self._checked_at = 0.0
        self._truncated = False  # 是否因超过 MODERATION_MAX_WORDS 被截断

    # ---------------------------------------------------------------- 热更新

    def invalidate(self) -> None:
        """让缓存立刻失效：管理端改词后调用，**本进程保存即生效**。

        只把 `_checked_at` 归零（下次检测强制查指纹），不直接重建——
        重建要查库，而调用方此刻正持有事务，不该在这里叠加 IO。
        """
        with self._lock:
            self._checked_at = 0.0
            # 指纹一并清空：只归零 `_checked_at` 的话，若新指纹恰好等于旧值
            # （条数相同且同一秒内改动，测试里同秒建库必现），就会跳过重建、
            # 继续用一份已经不属于当前数据的索引。清空后"失效"是硬保证。
            self._fingerprint = ""

    def _fingerprint_of(self, db) -> str:
        """词库指纹：`COUNT(*)` + `MAX(updated_at)`。

        比整表拉取便宜太多：一次聚合 vs 全表传输 + 重建自动机。
        删词/停用会改 COUNT，改词会改 MAX(updated_at)，两者都覆盖。
        """
        row = db.execute(
            select(func.count(SensitiveWord.id), func.max(SensitiveWord.updated_at)).where(
                SensitiveWord.enabled.is_(True)
            )
        ).first()
        return f"{row[0] if row else 0}:{row[1] if row and row[1] else ''}"

    def _rebuild(self, db) -> None:
        rows = (
            db.execute(
                select(SensitiveWord.word, SensitiveWord.action)
                .where(SensitiveWord.enabled.is_(True))
                .order_by(SensitiveWord.id)
                .limit(MODERATION_MAX_WORDS + 1)
            )
            .all()
        )
        self._truncated = len(rows) > MODERATION_MAX_WORDS
        rows = rows[:MODERATION_MAX_WORDS]
        words: dict[str, str] = {}
        actions: dict[str, str] = {}
        for raw, action in rows:
            norm, _ = normalize(raw)
            if not norm:
                # 纯符号词（如 "***"）归一化后为空 —— 无法参与匹配，
                # 留着只会让运营以为它生效了。直接跳过（管理端入库时已挡一道）。
                continue
            words[norm] = raw
            actions[norm] = action if action in VALID_ACTIONS else MODERATION_DEFAULT_ACTION
        self._words = words
        self._actions = actions
        self._auto = _Automaton(words) if words else None

    def ensure(self, db) -> None:
        """确保索引是最新的（必要时重建）。TTL 内直接返回——**检测不查库**。"""
        now = time.monotonic()
        if self._auto is not None and (now - self._checked_at) < MODERATION_CACHE_TTL_SECONDS:
            return
        fingerprint = self._fingerprint_of(db)
        with self._lock:
            if self._auto is None or fingerprint != self._fingerprint:
                self._rebuild(db)
                self._fingerprint = fingerprint
            self._checked_at = time.monotonic()

    # ---------------------------------------------------------------- 检测

    def inspect(self, db, *, content: str, name: str = "") -> Inspection:
        """检测评论正文（可选昵称），返回处理结果与命中列表。

        昵称命中**一律降级为 mask**（见模块 docstring「误杀控制」）——
        昵称不是用户此刻能改的东西，为它拒绝发言等于无声禁言。
        """
        check_name = MODERATION_CHECK_NAME and bool(name)
        if not MODERATION_ENABLED:
            return Inspection(content=content, name=name)
        self.ensure(db)
        if self._auto is None:
            return Inspection(content=content, name=name)

        hits: list[Hit] = []
        new_content, content_hits = self._scan(content, FIELD_CONTENT)
        hits.extend(content_hits)
        new_name = name
        if check_name:
            new_name, name_hits = self._scan(name, FIELD_NAME, force_mask=True)
            hits.extend(name_hits)

        blocked = any(h.action == ACTION_BLOCK for h in hits)
        masked = any(h.action == ACTION_MASK for h in hits)
        return Inspection(
            content=new_content,
            name=new_name,
            hits=hits,
            blocked=blocked,
            masked=masked,
        )

    def _scan(
        self, text: str, field_name: str, *, force_mask: bool = False
    ) -> tuple[str, list[Hit]]:
        """扫描单个字段：返回 `(脱敏后文本, 命中列表)`。"""
        norm, index_map = normalize(text)
        if not norm or self._auto is None:
            return text, []
        raw_matches = self._auto.search(norm)
        if not raw_matches:
            return text, []

        # 归一化下标 → 原文下标
        spans: list[tuple[int, int, str, str]] = []
        for start, end, word in raw_matches:
            action = self._actions.get(word, MODERATION_DEFAULT_ACTION)
            spans.append((index_map[start], index_map[end], word, action))

        # 白名单豁免：完全落在 allow 区间内的命中被剔除（降误杀的主要手段）
        allow_spans = [(s, e) for s, e, _w, a in spans if a == ACTION_ALLOW]
        if allow_spans:
            # 命中区间**完全落在**白名单区间内才豁免（注意方向：是 item 被 allow
            # 包裹，不是反过来）。写反了会变成"allow 词的命中反而豁免不了自己"。
            spans = [
                item
                for item in spans
                if not any(item[0] >= s and item[1] <= e for s, e in allow_spans)
            ]
        spans = [item for item in spans if item[3] != ACTION_ALLOW]
        if not spans:
            return text, []

        merged = self._merge(spans, force_mask=force_mask)
        hits = [
            Hit(
                word=self._words.get(word, word),
                action=action,
                field=field_name,
                start=start,
                end=end,
            )
            for start, end, word, action in merged
        ]
        return self._mask(text, merged), hits

    @staticmethod
    def _merge(
        spans: list[tuple[int, int, str, str]], *, force_mask: bool
    ) -> list[tuple[int, int, str, str]]:
        """合并重叠区间：动作取最严，词取最长（日志里更有解释力）。

        重叠很常见：「傻逼」与「傻」同时命中同一段——不合并会把同一段
        替换两次，长度与位置都会错乱。
        """
        spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]), s[1]))
        merged: list[list] = []
        for start, end, word, action in spans:
            if force_mask and action == ACTION_BLOCK:
                action = ACTION_MASK
            if merged and start <= merged[-1][1]:
                last = merged[-1]
                last[1] = max(last[1], end)
                # 最严动作优先；同严时保留更长的词（日志可读性）
                if _SEVERITY[action] > _SEVERITY[last[3]] or (
                    _SEVERITY[action] == _SEVERITY[last[3]] and (end - start) > (last[1] - last[0])
                ):
                    last[2], last[3] = word, action
            else:
                merged.append([start, end, word, action])
        return [(s, e, w, a) for s, e, w, a in merged]

    @staticmethod
    def _mask(text: str, merged: list[tuple[int, int, str, str]]) -> str:
        """把命中的原文区间整体替换为脱敏字符。

        长度取**命中词**的长度而非原文区间长度：同一句「傻逼」无论被写成
        `傻 逼`、`傻-逼` 还是 `f.u.c.k`，输出都是同样多个 `*`。按原文长度替换
        会让脱敏结果随变形方式变长变短——既难看，也等于告诉对方"你这次绕过去了
        几个字符"。
        """
        out: list[str] = []
        prev = 0
        for start, end, word, _action in merged:
            out.append(text[prev:start])
            out.append(MODERATION_MASK_CHAR * len(word))
            prev = min(end + 1, len(text))
        out.append(text[prev:])
        return "".join(out)


engine = ModerationEngine()

# Aho-Corasick 自动机的公开导出：供昵称校验（P1-24，`nickname_moderation`）
# 复用，避免出现第二份实现后各自演化、行为漂移。**新增场景优先用它**，
# 不要复制一份——两份 AC 的实现只要有一处细节不同（如 fail 自环处理），
# 同一个绕过样本就会在两条链路上得到不同结果，而测试几乎发现不了。
AhoCorasick = _Automaton
