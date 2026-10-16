"""用户昵称敏感词校验（P1-24）。

为什么昵称不复用评论的 `moderation.engine`
-----------------------------------------
1. **动作集不同**：评论有 `mask`（脱敏后照常发布）——昵称没有这个语义，
   昵称是账号级属性，"脱敏后发布"意味着改掉别人的名字，而用户此刻改不了。
   昵称的对应动作是 `block`（拒绝设置）与 `review`（放行 + 待人工复核）。
2. **归一化强度不同**：评论刻意**不做**重复压缩（误杀率高——"我真的很喜欢"
   会判成刷屏，见 moderation.py 模块 docstring）；昵称场景要求覆盖"重复字符"
   绕过，且昵称短、无语义（不是句子），压缩的误杀面小得多。
3. **别名只对昵称有意义**：拼音全拼/首字母/谐音是昵称绕过的主力
   （"shabi"、"sb"），评论正文走自然语言不需要它。

所以：新表 `nickname_words`（不复用 `sensitive_words`——不同语义的资源不要
挤进同一张表，否则"这个词是拦评论还是拦昵称"永远说不清），新引擎**复用**
`moderation.AhoCorasick` 自动机与指纹缓存模式（不复制第二份实现）。

匹配能力（用户要求的五类绕过，各自的解法）
----------------------------------------
- **大小写**：`casefold()`（比 lower 彻底，ß → ss）；
- **全角/半角与兼容字符**：逐字符 NFKC（`ｆｕｃｋ` → `fuck`、`①` → `1`）；
- **空格/标点/零宽插入**：只保留 Unicode L*/N*，其余全丢（`傻-逼` ≡ `傻逼`）；
- **重复字符**：`collapsed` 形态压缩连续重复段（`笨~蛋~笨~蛋` 类的变体）。
  注意单次重复插字其实**不**需要它就能命中（"笨蛋"在"笨笨蛋蛋"里本来就在），
  collapsed 真正对付的是逐字重复使子串断裂的场景（"abc" → "aabbcc"）；
- **字母数字混淆**（leetspeak）：`4/@→a、3→e、1/!/|/l→i、0→o、5/$→s、
  7/+→t、8→b、6/9→g、2→z`。模式串与文本走**同一条管道**，所以任意一对一
  归并都是安全的（"1"与"i"互相命中，不会漏放）；
- **数字插字**：`n digits` 形态把数字来源字符整体去掉（"博0彩" → "博彩"）。
  它与 leetspeak 归并互补：leetspeak 把 0 当字母 o 用，本形态把 0 当噪声删掉；
- **繁简体**：内置常用繁→简映射表（ curated 子集，非完整 OpenCC 表——
  本机无 opencc/zhconv 依赖，完整转换靠运营把繁体变体配成别名）；
- **拼音与谐音**：**词条别名**（`nickname_words.aliases`）。同音字替换
  （傻→莎）没有字符级算法可解（需要字→拼音表），由运营发现后补别名。

分级
----
- `block`：拒绝设置（**40006**，提示不回显命中词——回显等于给绕过者一份
  "哪些词不能起"的对照表）；
- `review`：**放行**但置 `users.name_pending_review`，进人工复核队列；
- `allow`：白名单豁免（与 P1-23 同处方：命中区间被 allow 区间完全包裹才豁免），
  降误杀的逃生门；
- `pass`：未命中。

性能与维护
----------
- 进程内缓存 + 指纹 `COUNT(*) + MAX(updated_at)` + TTL（默认 30s）：
  **检测路径不查库**，只有 TTL 到期才回库问一次指纹；
- 管理端改词 → `invalidate()` → 本进程保存即生效；集群其他实例靠 TTL 兜底；
- 多形态 AC 自动机：昵称上限 24 码点，四形态各扫一遍仍是微秒级。

输入边界（`validate_nickname_shape`）
------------------------------------
- 长度 2~24（按 Unicode 码点计，`len()` 即码点数，emoji 记 1）；
- 字符白名单：L*/N*（中英字母、汉字、数字）+ 有限符号 `_ - · . ・` + 内部空格；
- 空值 / 纯空白 → 拒绝；首尾空白先 trim 再校验；
- 代理字符（lone surrogate，异常编码）与控制字符（Cc，含 NUL）不在白名单 → 拒绝；
- emoji/符号（So）→ 拒绝。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import func, select, update

from .config import (
    NICKNAME_ALLOWED_SYMBOLS,
    NICKNAME_CACHE_TTL_SECONDS,
    NICKNAME_COLLAPSE_REPEATS,
    NICKNAME_MAX_LENGTH,
    NICKNAME_MAX_WORDS,
    NICKNAME_MIN_LENGTH,
    NICKNAME_MIN_WORD_LEN,
    NICKNAME_MODERATION_ENABLED,
)
from .models import NicknameHit, NicknameWord, now_str
from .moderation import AhoCorasick

logger = logging.getLogger("toutiao.nickname")

# ---------------------------------------------------------------- 结果与动作

RESULT_PASS = "pass"
RESULT_BLOCK = "block"
RESULT_REVIEW = "review"

ACTION_BLOCK = "block"  # 拒绝设置
ACTION_REVIEW = "review"  # 放行 + 待人工复核
ACTION_ALLOW = "allow"  # 白名单豁免（降误杀）
NICKNAME_ACTIONS = (ACTION_BLOCK, ACTION_REVIEW, ACTION_ALLOW)

# 动作严厉程度：区间重叠时取最严的（宁可拦住也别放过）
_SEVERITY = {ACTION_ALLOW: 0, ACTION_REVIEW: 1, ACTION_BLOCK: 2}

# 库中 action 缺失/未知时的兜底：刻意取 review 而非 block——数据异常时应当
# 降级为"放行 + 让人看一眼"，而不是把用户的昵称直接吞掉（与 P1-23 同思路）。
FALLBACK_ACTION = ACTION_REVIEW

# 归一化形态名
FORM_BASE = "base"
FORM_NODIGITS = "nodigits"
FORM_COLLAPSED = "collapsed"
FORM_COLLAPSED_NODIGITS = "collapsed-nodigits"
# 全部形态（顺序即扫描顺序；collapsed 两形态受 NICKNAME_COLLAPSE_REPEATS 控制，
# 关闭时 normalize_forms 不产出它们，对应的自动机为空）
FORM_NAMES = (FORM_BASE, FORM_NODIGITS, FORM_COLLAPSED, FORM_COLLAPSED_NODIGITS)

# 每个词最多配置几条别名、单条别名最长多少字符（防误导入把索引撑爆）
MAX_ALIASES_PER_WORD = 20
MAX_ALIAS_LENGTH = 50

# 面向用户的合规提示（**刻意不回显命中的词**）
MESSAGE_BLOCK = "昵称包含违规内容，请更换后重试"
MESSAGE_REVIEW = "昵称已保存，将通过人工复核后展示"
MESSAGE_PASS = "昵称可以使用"


# ---------------------------------------------------------------- 混淆字符表

# 字母数字混淆（leetspeak）：外形相近的字符归并到同一字母。
# 只做一对一归并——模式串与文本走同一条管道，任何一对一映射都不会漏放，
# 只会让被归并的字符互相命中（这正是对抗变形想要的）。
_CONFUSABLES = {
    "4": "a", "@": "a",
    "3": "e",
    "1": "i", "!": "i", "|": "i", "l": "i",
    "0": "o",
    "5": "s", "$": "s",
    "7": "t", "+": "t",
    "8": "b",
    "6": "g", "9": "g",
    "2": "z",
}

# 常用繁→简映射（curated 子集，覆盖常见姓氏/名字/脏话/广告用字）。
# 刻意不做完整 OpenCC 表：那需要数千项且要随 Unicode 更新维护，而这里要的是
# 零运行时依赖。未覆盖的繁体变体由运营配成词条别名（见模块 docstring）。
_T2S_PAIRS = """
愛爱 礙碍 骯肮 壩坝 擺摆 敗败 辦办 幫帮 寶宝 報报 備备 貝贝 筆笔 幣币 畢毕 斃毙
邊边 變变 標标 錶表 彆别 濱滨 餅饼 撥拨 補补 佈布 參参 蠶蚕 艙舱 倉仓 蒼苍 測测
層层 纏缠 蟬蝉 讒谗 顫颤 懺忏 長长 場场 嘗尝 償偿 廠厂 暢畅 車车 徹彻 塵尘 陳陈
襯衬 撐撑 稱称 懲惩 癡痴 馳驰 齒齿 熾炽 衝冲 蟲虫 醜丑 齣出 處处 觸触 辭辞 聰聪
叢丛 湊凑 竄窜 錯错 達达 帶带 貸贷 擔担 膽胆 誕诞 彈弹 當当 黨党 導导 燈灯 鄧邓
敵敌 遞递 點点 澱淀 電电 釣钓 調调 疊叠 動动 棟栋 鬥斗 獨独 讀读 斷断 鍛锻 隊队
對对 噸吨 奪夺 墮堕 鵝鹅 額额 惡恶 餓饿 兒儿 爾尔 餌饵 貳贰 發发 罰罚 閥阀 範范
販贩 飯饭 訪访 紡纺 飛飞 誹诽 廢废 費费 紛紛 墳坟 奮奋 憤愤 糞粪 豐丰 風风 鳳凤
膚肤 輻辐 撫抚 輔辅 復复 負负 婦妇 縛缚 該该 鈣钙 幹干 趕赶 剛刚 崗岗 綱纲 鋼钢
寫写 學学 畫画 劃划 壞坏 歡欢 環环 還还 會会 繪绘 擊击 饑饥 雞鸡 積积 績绩 擠挤
濟济 紀纪 繼继 傢家 價价 堅坚 間间 艱艰 檢检 簡简 見见 劍剑 薦荐 賤贱 濺溅 將将
獎奖 講讲 醬酱 嬌娇 腳脚 絞绞 餃饺 繳缴 節节 潔洁 結结 階阶 莖茎 經经 驚惊 競竞
舊旧 駒驹 舉举 據据 懼惧 捲卷 覺觉 絕绝 軍军 開开 凱凯 慨慨 顆颗 殼壳 課课 墾垦
懇恳 誇夸 塊块 寬宽 礦矿 虧亏 擴扩 臘腊 蠟蜡 來来 賴赖 蘭兰 攔拦 覽览 懶懒 爛烂
勞劳 澇涝 樂乐 類类 淚泪 壘垒 稜棱 離离 禮礼 麗丽 歷历 曆历 練练 糧粮 療疗 遼辽
獵猎 臨临 鄰邻 齡龄 劉刘 婁娄 盧卢 虜虏 鹵卤 陸陆 錄录 綠绿 論论 蘿萝 邏逻 駱骆
馬马 罵骂 滿满 貓猫 貿贸 們们 夢梦 廟庙 滅灭 納纳 腦脑 惱恼 內内 擬拟 釀酿 鳥鸟
聶聂 檸柠 農农 歐欧 盤盘 龐庞 賠赔 噴喷 闢辟 貧贫 評评 蘋苹 憑凭 撲扑 鋪铺 樸朴
譜谱 齊齐 啟启 棄弃 簽签 錢钱 鉛铅 喬乔 竅窍 親亲 窮穷 瓊琼 區区 權权 勸劝 確确
讓让 擾扰 熱热 認认 韌韧 榮荣 軟软 潤润 灑洒 傘伞 喪丧 殺杀 曬晒 閃闪 傷伤 賞赏
燒烧 紹绍 捨舍 攝摄 聲声 濕湿 識识 駛驶 勢势 壽寿 獸兽 輸输 贖赎 稅税 順顺 說说
碩硕 絲丝 鬆松 聳耸 蘇苏 隨随 歲岁 孫孙 損损 鎖锁 態态 攤摊 貪贪 談谈 壇坛 嘆叹
燙烫 討讨 騰腾 題题 體体 糴籴 鐵铁 聽听 廳厅 銅铜 頭头 圖图 塗涂 團团 窪洼 襪袜
彎弯 萬万 網网 圍围 違违 維维 偽伪 衛卫 穩稳 務务 霧雾 錫锡 習习 戲戏 峽峡 俠侠
轄辖 鮮鲜 纖纤 現现 線线 鄉乡 響响 項项 蕭萧 銷销 曉晓 嘯啸 協协 脅胁 攜携 瀉泻
謝谢 興兴 擇择 債债 戰战 張张 趙赵 鎮镇 爭争 徵征 證证 織织 職职 執执 紙纸 質质
鐘钟 腫肿 種种 眾众 皺皱 豬猪 諸诸 緒绪 築筑 鑄铸 專专 轉转 賺赚 莊庄 裝装 壯壮
狀状 準准 蹤踪 總总 縱纵 鑽钻 於于 韓韩 馮冯 許许 蔣蒋 賈贾 葉叶 閻阎 譚谭 鄒邹
顧顾 湯汤 龔龚 偉伟 傑杰 穎颖 靜静 詩诗 書书 圓圆 戀恋 陽阳 樹树 鷹鹰 鶴鹤 鯉鲤
魚鱼 蝦虾 裡里 裏里 麵面 隻只 麼么 麽么 臺台 颱台 乾干 髮发 嶽岳 巖岩 嶺岭 甕瓮
賭赌 髒脏 詐诈 騙骗 竊窃 搶抢 贓赃 謊谎
広广 沢泽 浜滨 対对 単单 仏佛 仮假 戸户 巣巢 桜樱 斉齐 効效 駅驿 圧压 醤酱 囲围
暦历 覚觉 気气 芸艺 戯戏 県县 険险 験验 酔醉 浄净 砕碎 図图 悩恼 拝拜 売卖 発发
歩步 黒黑 涙泪 絵绘 営营 栄荣 従从 処处 歯齿 毎每 訳译 薬药 円圆
"""
_T2S: dict[str, str] = {}
for _tok in _T2S_PAIRS.split():
    # 每个 token 是「繁+简」两个字；长度不为 2 的是排版事故，直接跳过
    # （错位的映射会把正常汉字改坏，比没有映射糟得多）
    if len(_tok) == 2 and _tok[0] != _tok[1]:
        _T2S[_tok[0]] = _tok[1]
del _tok

# 符号白名单：默认 `_ - · . ・`，另加空格（配置项只放"非常空格"的符号——
# `_env` 会 strip 整个值，尾随空格在环境变量里活不下来，故空格在代码里补）。
_ALLOWED_SYMBOLS = set(NICKNAME_ALLOWED_SYMBOLS) | {" "}


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class NormalForm:
    """一种归一化形态：串 + 下标映射（`index_map[j]` = 串中第 j 个字符的原文下标）。"""

    name: str
    text: str
    index_map: list[int]


@dataclass
class Hit:
    """一次命中（合并后的原文区间）。

    `word` 是库中**主词**（运营配的那一个），`matched` 是实际命中的模式
    （主词本身或某条别名）——命中日志要能回答"是哪条别名拦住的"。
    """

    word: str
    matched: str
    action: str  # block | review（allow 已被豁免剔除）
    start: int  # 原文下标（含）
    end: int  # 原文下标（含）


@dataclass
class Verdict:
    """一次校验的结果。

    `result`：pass | block | review。`reason`/`message` 面向用户，
    **刻意不含命中的敏感词原文**（见模块 docstring「分级」）。
    `hits` 仅供服务端写日志与测试断言，不下发给客户端。
    """

    result: str
    reason: str = ""
    message: str = ""
    hits: list[Hit] = field(default_factory=list)


# ---------------------------------------------------------------- 归一化


def _fold_char(ch: str) -> str:
    """单字符折叠：NFKC → casefold → 混淆归并 → 繁简。可能返回多字符（ß→ss）。"""
    out: list[str] = []
    for c in unicodedata.normalize("NFKC", ch).casefold():
        c = _CONFUSABLES.get(c, c)
        c = _T2S.get(c, c)
        out.append(c)
    return "".join(out)


def _fold(text: str) -> tuple[str, list[int], list[bool]]:
    """整串折叠：返回 `(串, 原文下标, 是否数字来源)`。

    「是否数字来源」按**混淆归并之前**的类别判定：`0` 归并成 `o` 之后就看不出
    它原来是数字了，而 `nodigits` 形态正需要删掉这些字符（"博0彩"→"博彩"）。
    """
    chars: list[str] = []
    index_map: list[int] = []
    digit_flags: list[bool] = []
    for i, ch in enumerate(text):
        folded = unicodedata.normalize("NFKC", ch).casefold()
        for c in folded:
            is_digit = unicodedata.category(c) == "Nd"
            c = _CONFUSABLES.get(c, c)
            c = _T2S.get(c, c)
            if unicodedata.category(c)[0] in ("L", "N"):
                chars.append(c)
                index_map.append(i)
                digit_flags.append(is_digit)
    return "".join(chars), index_map, digit_flags


def _strip_digits(text: str, index_map: list[int], flags: list[bool]) -> tuple[str, list[int]]:
    keep = [j for j in range(len(text)) if not flags[j]]
    return "".join(text[j] for j in keep), [index_map[j] for j in keep]


def _collapse(text: str, index_map: list[int]) -> tuple[str, list[int]]:
    """压缩连续重复字符（"aabbcc" → "abc"）。

    下标取该字符**首次**出现的位置：命中区间回到原文时要能圈住最早那一段。
    """
    chars: list[str] = []
    imap: list[int] = []
    prev = None
    for j, c in enumerate(text):
        if c == prev:
            continue
        prev = c
        chars.append(c)
        imap.append(index_map[j])
    return "".join(chars), imap


def normalize_forms(text: str) -> list[NormalForm]:
    """把原文折叠成多个归一化形态（顺序固定：base → nodigits → collapsed → 叠加）。

    每个形态各自带 index_map，命中后能回到**原文**定位（写日志、圈区间）。
    """
    base, imap, flags = _fold(text)
    forms = [NormalForm(FORM_BASE, base, imap)]
    nod, nod_imap = _strip_digits(base, imap, flags)
    forms.append(NormalForm(FORM_NODIGITS, nod, nod_imap))
    if NICKNAME_COLLAPSE_REPEATS:
        col, col_imap = _collapse(base, imap)
        forms.append(NormalForm(FORM_COLLAPSED, col, col_imap))
        colnd, colnd_imap = _collapse(nod, nod_imap)
        forms.append(NormalForm(FORM_COLLAPSED_NODIGITS, colnd, colnd_imap))
    return forms


def alias_list(raw) -> list[str]:
    """解析词条别名（JSON 数组）。脏数据（非 JSON / 非数组 / 非字符串项）一律按空处理。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for a in raw:
        if not isinstance(a, str):
            continue
        a = a.strip()
        if not a or a in out:
            continue
        if len(a) > MAX_ALIAS_LENGTH:
            continue
        out.append(a)
        if len(out) >= MAX_ALIASES_PER_WORD:
            break
    return out


# ---------------------------------------------------------------- 输入边界


def validate_nickname_shape(name) -> tuple[bool, str]:
    """校验昵称的**形状**（长度/字符集/空值），与敏感词无关。

    返回 `(是否合法, 消息)`；不合法时消息即面向用户的提示（42203）。

    顺序有讲究：先判空、再判字符集、最后判长度。字符集先于长度是因为
    「这个字符根本不能用」比「太长了」更根本——一个单字代理字符若先撞上
    长度检查，用户会收到「至少需要 2 个字符」，而他再加多少字都过不了。
    """
    if not isinstance(name, str):
        return False, "昵称格式不正确"
    stripped = name.strip()
    if not stripped:
        return False, "昵称不能为空"
    has_word_char = False
    for ch in stripped:
        # 先过 NFKC：全角空格等"看着是空格"的字符归一后与半角同等对待
        for c in unicodedata.normalize("NFKC", ch):
            if c in _ALLOWED_SYMBOLS:
                continue
            cat = unicodedata.category(c)
            if cat[0] in ("L", "N"):
                has_word_char = True
                continue
            return False, f"昵称仅支持中英文、数字及 {NICKNAME_ALLOWED_SYMBOLS} 等符号"
    if len(stripped) < NICKNAME_MIN_LENGTH:
        return False, f"昵称至少需要 {NICKNAME_MIN_LENGTH} 个字符"
    if len(stripped) > NICKNAME_MAX_LENGTH:
        return False, f"昵称不能超过 {NICKNAME_MAX_LENGTH} 个字符"
    if not has_word_char:
        # 纯符号名（"____"）落在字符白名单里，但它既无法被匹配也没有信息量
        return False, "昵称需至少包含一个字母、数字或汉字"
    return True, ""


# ---------------------------------------------------------------- 引擎


class NicknameEngine:
    """词库缓存 + 校验入口。进程内单例（模块底部 `engine`）。

    线程安全模型与 P1-23 一致：`_rebuild` 在锁内，指纹查询在锁外
    （最坏多查一次，无所谓）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._autos: dict[str, AhoCorasick | None] = {}  # 形态名 -> 自动机
        # 归属表按 **(形态, 归一化模式)** 键：同一串可能分属不同词的不同形态
        # （A 的 base 与 B 的 collapsed 都可能是 "abc"），只按串键会让命中
        # 报告张冠李戴。
        self._owners: dict[tuple[str, str], str] = {}  # (形态, 模式) -> 主词原文
        self._actions: dict[tuple[str, str], str] = {}  # (形态, 模式) -> 动作
        self._matched: dict[tuple[str, str], str] = {}  # (形态, 模式) -> 实际模式原文
        self._categories: dict[tuple[str, str], str] = {}  # (形态, 模式) -> 主词分类
        self._fingerprint = ""
        self._checked_at = 0.0
        self._loaded = False
        self._truncated = False  # 是否因超过 NICKNAME_MAX_WORDS 被截断

    @property
    def truncated(self) -> bool:
        """词库是否因超限被截断（供健康检查/测试观测）。"""
        return self._truncated

    @property
    def pattern_count(self) -> int:
        """已加载的模式串总数（含各形态展开）。"""
        return len(self._owners)

    # ------------------------------------------------------------ 热更新

    def invalidate(self) -> None:
        """让缓存立刻失效：管理端改词后调用，**本进程保存即生效**。

        同时清 `_checked_at` 与 `_fingerprint`（与 P1-23 同一处坑：只归零时间
        的话，新指纹恰好等于旧值时会被当成"没变"而跳过重建）。
        """
        with self._lock:
            self._checked_at = 0.0
            self._fingerprint = ""

    def _fingerprint_of(self, db) -> str:
        row = db.execute(
            select(func.count(NicknameWord.id), func.max(NicknameWord.updated_at)).where(
                NicknameWord.enabled.is_(True)
            )
        ).first()
        return f"{row[0] if row else 0}:{row[1] if row and row[1] else ''}"

    def _rebuild(self, db) -> None:
        rows = (
            db.execute(
                select(
                    NicknameWord.word,
                    NicknameWord.action,
                    NicknameWord.category,
                    NicknameWord.aliases,
                )
                .where(NicknameWord.enabled.is_(True))
                .order_by(NicknameWord.id)
                .limit(NICKNAME_MAX_WORDS + 1)
            )
            .all()
        )
        self._truncated = len(rows) > NICKNAME_MAX_WORDS
        rows = rows[:NICKNAME_MAX_WORDS]
        by_form: dict[str, dict[str, str]] = {name: {} for name in FORM_NAMES}
        owners: dict[tuple[str, str], str] = {}
        actions: dict[tuple[str, str], str] = {}
        matched: dict[tuple[str, str], str] = {}
        categories: dict[tuple[str, str], str] = {}
        # 同一对（变体, 已占用主词）的冲突只警告一次：一个变体撞车时四个形态
        # 会各报一次，四行同样的日志只会把后面真正的冲突淹掉
        warned: set[tuple[str, str]] = set()
        for raw, action, category, aliases in rows:
            act = action if action in NICKNAME_ACTIONS else FALLBACK_ACTION
            cat = (category or "").strip()
            # 主词与每条别名都走同一条归一化管道：别名不是"另一个词"，
            # 而是同一个词的另一种写法（拼音/谐音/变形）。
            for variant in (raw, *alias_list(aliases)):
                for form in normalize_forms(variant):
                    norm = form.text
                    # 最短词长对**每个形态**都把关：折叠形态比主词短是正常的
                    # （"约约"压缩成"约"），但放它进去会让任何含"约"的昵称都命中
                    # "约约"——那是运营不想要的误杀面。
                    if len(norm) < NICKNAME_MIN_WORD_LEN:
                        continue
                    key = (form.name, norm)
                    prev = actions.get(key)
                    if prev is not None:
                        # 模式串撞车（两个词/别名在同一形态里归一并到同一串）：
                        # 保留更严的那个并留日志——否则后加载的会静默覆盖先加载的，
                        # 先建的词莫名失效，而这种失效在测试里几乎看不见。
                        if _SEVERITY[act] <= _SEVERITY[prev]:
                            pair = (variant, owners.get(key, norm))
                            if pair not in warned:
                                warned.add(pair)
                                logger.warning(
                                    "昵称词库模式串冲突：%r（%s）与 %r（%s）归一并到同一串"
                                    "（可能跨多个形态），保留更严者",
                                    variant,
                                    act,
                                    owners.get(key, norm),
                                    prev,
                                )
                            continue
                    by_form[form.name][norm] = norm
                    owners[key] = raw
                    actions[key] = act
                    matched[key] = variant
                    categories[key] = cat
        # 按形态各建一台自动机：同一模式串在不同形态里的下标映射不同，
        # 必须用"该形态的文本"扫"该形态的模式"（合并成一台会扫错文本）。
        self._autos = {
            name: (AhoCorasick(pats) if pats else None) for name, pats in by_form.items()
        }
        self._owners = owners
        self._actions = actions
        self._matched = matched
        self._categories = categories
        self._loaded = True

    def ensure(self, db) -> None:
        """确保索引是最新的（必要时重建）。TTL 内直接返回——**校验不查库**。"""
        now = time.monotonic()
        if self._loaded and (now - self._checked_at) < NICKNAME_CACHE_TTL_SECONDS:
            return
        fingerprint = self._fingerprint_of(db)
        with self._lock:
            if not self._loaded or fingerprint != self._fingerprint:
                self._rebuild(db)
                self._fingerprint = fingerprint
            self._checked_at = time.monotonic()

    # ------------------------------------------------------------ 校验

    def check(self, db, name: str) -> Verdict:
        """校验昵称，返回分级结果。**不写库**（命中日志走本模块 `record_hits`，
        注册与改昵称两条路径共用同一份「什么算一次事件」的判定）。"""
        if not NICKNAME_MODERATION_ENABLED:
            return Verdict(result=RESULT_PASS, message=MESSAGE_PASS)
        self.ensure(db)
        if not self._owners:
            return Verdict(result=RESULT_PASS, message=MESSAGE_PASS)

        forms = {f.name: f for f in normalize_forms(name)}
        spans: list[tuple[int, int, tuple[str, str], str]] = []
        for form_name, auto in self._autos.items():
            if auto is None:
                continue
            form = forms.get(form_name)
            if form is None or not form.text:
                continue
            for start, end, pattern in auto.search(form.text):
                key = (form_name, pattern)
                action = self._actions.get(key, FALLBACK_ACTION)
                # 归一化下标 → 原文下标（同一段被多个形态同时命中时区间一致，
                # 合并阶段会去重）
                spans.append((form.index_map[start], form.index_map[end], key, action))

        merged = self._merge(self._exempt(spans))
        if not merged:
            return Verdict(result=RESULT_PASS, message=MESSAGE_PASS)

        worst_action = max((a for _s, _e, _k, a in merged), key=lambda a: _SEVERITY[a])
        hits = [
            Hit(
                word=self._owners.get(key, key[1]),
                matched=self._matched.get(key, key[1]),
                action=action,
                start=start,
                end=end,
            )
            for start, end, key, action in merged
        ]
        # 命中原因取最严那一条的分类（多个词命中时，最严的才是"为什么被拒"的答案）
        worst_key = next(k for _s, _e, k, a in merged if a == worst_action)
        category = self._categories.get(worst_key, "")
        if worst_action == ACTION_BLOCK:
            reason = f"包含{category}违规内容" if category else "包含违规内容"
            return Verdict(result=RESULT_BLOCK, reason=reason, message=MESSAGE_BLOCK, hits=hits)
        reason = f"包含{category}内容，需人工复核" if category else "包含需人工复核的内容"
        return Verdict(result=RESULT_REVIEW, reason=reason, message=MESSAGE_REVIEW, hits=hits)

    # ------------------------------------------------------------ 内部

    def _exempt(
        self, spans: list[tuple[int, int, tuple[str, str], str]]
    ) -> list[tuple[int, int, tuple[str, str], str]]:
        """白名单豁免：完全落在 allow 区间内的命中被剔除（与 P1-23 同处方）。"""
        allow_spans = [(s, e) for s, e, _k, a in spans if a == ACTION_ALLOW]
        if allow_spans:
            spans = [
                item
                for item in spans
                if not any(item[0] >= s and item[1] <= e for s, e in allow_spans)
            ]
        return [item for item in spans if item[3] != ACTION_ALLOW]

    @staticmethod
    def _merge(
        spans: list[tuple[int, int, tuple[str, str], str]]
    ) -> list[tuple[int, int, tuple[str, str], str]]:
        """合并重叠区间：动作取最严，词取最长（与 P1-23 `_merge` 同规则）。

        多形态扫描会让同一段被命中多次（base 与 collapsed 都中"笨蛋"），
        不合并会把同一段算成多个命中，日志与原因都会虚高。
        """
        spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]), s[1]))
        merged: list[list] = []
        for start, end, key, action in spans:
            if merged and start <= merged[-1][1]:
                last = merged[-1]
                last[1] = max(last[1], end)
                if _SEVERITY[action] > _SEVERITY[last[3]] or (
                    _SEVERITY[action] == _SEVERITY[last[3]] and (end - start) > (last[1] - last[0])
                ):
                    last[2], last[3] = key, action
            else:
                merged.append([start, end, key, action])
        return [(s, e, k, a) for s, e, k, a in merged]


engine = NicknameEngine()


# ---------------------------------------------------------------- 命中日志


def record_hits(db, hits, *, user_id: int | None, user_name: str, source: str, name: str) -> None:
    """写昵称命中日志 + 累加词的命中次数（**不提交**，由调用方与业务一起 commit）。

    与 `comments._record_hits` 同一处方（同 `(词, 命中模式)` 一次提交只记一行、
    `hit_count` 走 SQL 表达式自增防并发丢更新），但放在这里而不是某个路由里：
    **注册（auth.py）与改昵称（me.py）两条路径都要记**，放路由层必然复制两份，
    两份「什么算一次事件」的判定一旦漂移，同一个绕过样本在一条链路上有日志、
    另一条没有——而这种差异在单测里几乎看不见。

    `block` 同样留痕（与 P1-23 同理由）：否则「拦了多少次改名/注册」是黑箱，
    也识别不出「反复尝试绕过」的行为。注册被拦时账号尚未落库，`user_id` 记 NULL
    （昵称原文在 `snippet` 里，证据不丢）。
    """
    ts = now_str()
    seen: set[tuple[str, str]] = set()
    for h in hits:
        key = (h.word, h.matched)
        if key in seen:
            continue
        seen.add(key)
        db.add(
            NicknameHit(
                word=h.word,
                matched=h.matched,
                action=h.action,
                source=source,
                user_id=user_id,
                # 记账号当前昵称：运营要按人定位，被拦下的新名字在 snippet 里
                user_name=user_name,
                # 昵称整体就是上下文（≤24 字符），无需截取
                snippet=name[:300],
                created_at=ts,
            )
        )
        # 自增走 SQL 表达式而不是 Python 侧的 `+=`：并发提交下先读后写会丢更新
        db.execute(
            update(NicknameWord)
            .where(NicknameWord.word == h.word)
            .values(hit_count=NicknameWord.hit_count + 1)
        )
