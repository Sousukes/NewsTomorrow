"""Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field, field_validator

from .nickname_moderation import MAX_ALIAS_LENGTH, MAX_ALIASES_PER_WORD
from .feeds.sources import CHANNEL_KEYS


def _valid_channel(value: str | None) -> str | None:
    """频道必须落在既定取值内。

    否则一个拼错的频道（如 `tecn`）会让该源**匹配不到任何频道**，
    而 `sources_for_channel` 在无匹配时会回退到全部源——
    结果是这个源静默污染每一个频道，且极难定位。

    `None` 是「本次不修改该字段」，直接放行。
    """
    if value is None:
        return None
    if value not in CHANNEL_KEYS:
        raise ValueError(f"channel 必须是以下之一：{', '.join(CHANNEL_KEYS)}")
    return value


class RegisterIn(BaseModel):
    phone: str = Field(min_length=11, max_length=11)
    password: str = Field(min_length=6, max_length=64)
    name: str | None = None


class LoginIn(BaseModel):
    phone: str
    password: str


class RefreshIn(BaseModel):
    """刷新令牌入参（C1）。

    - `refreshToken`：长令牌，**只能**用来换 access；走 body 而不是
      `Authorization` 头——头里放的是 access，两者不能互相覆盖；
    - 允许缺省：老客户端只带 `Authorization`（用当前 access 滑动续期），
      契约脚本与既有测试同样走这条路径。缺省时后端回退到 access 续期。
    """

    refreshToken: str = ""


class StepUpIn(BaseModel):
    """二次校验入参（C10）：只带密码，用户身份取自 `Authorization` 里的 access。

    刻意**不**收手机号：那是把二次校验变成登录端点——猜密码的人可以指定
    任意账号，成了账号枚举 + 爆破的二合一入口。已登录的人不需要再报自己是谁。
    """

    password: str = Field(min_length=1)


class PasswordChangeIn(BaseModel):
    """自助改密入参（C4）。

    `oldPassword` 必填：改密本身是「证明你还在」的动作，且接口已被
    `require_step_up` 保护过一轮；两道证明都过仍要旧密码，是为了让
    「令牌被偷 + 弹窗被诱导」也拿不到永久凭证。
    """

    oldPassword: str = Field(min_length=1)
    # 与注册同一条长度口径（6~64），避免出现「注册能过、改密不能过」
    newPassword: str = Field(min_length=6, max_length=64)


class CommentCreateIn(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    # 回复某条评论时传其 id；不传表示发表主评论。
    # 校验（同文章、未删除）在路由层做——需要查库，schema 只把关形态。
    parentId: str | None = Field(None, max_length=64)


class LikeIn(BaseModel):
    liked: bool = True


class MarkIn(BaseModel):
    """收藏 / 历史：单条操作入参。"""

    newsId: str = Field(min_length=1, max_length=32)


class FollowIn(BaseModel):
    """关注来源（E6b）：`sourceKey` 对应 `rss_sources.key`。

    刻意**不校验是否存在**：那是路由的职责（要查库，且错误码是 40410 而非 422）。
    这里只挡长度与空值——`min_length=1` 让空串在 422 阶段就被拒，
    不必等到路由里再判一次。
    """

    sourceKey: str = Field(min_length=1, max_length=64)


class MarkBulkIn(BaseModel):
    """收藏 / 历史：并集合并入参（客户端本地数据并入云端）。

    上限 500 条，避免超大请求体；超出部分由服务端按顺序截断。
    """

    ids: list[str] = Field(default_factory=list, max_length=500)


class UserOut(BaseModel):
    id: int
    name: str
    phone: str


class TokenOut(BaseModel):
    token: str
    # C1 新增两项：**刻意可选**——已部署的旧客户端只认 `token`，
    # 把它们设为必填等于在升级瞬间宣布所有旧客户端不合规。
    refreshToken: str | None = None
    """access 剩余秒数：前端据此**提前**续期，而不是等撞上 401 再补救。"""
    expiresIn: int | None = None
    user: UserOut


class NewsOut(BaseModel):
    id: str
    title: str
    summary: str | None = None
    source: str
    commentCount: int = 0
    time: str
    images: list[str] | None = None
    cover: str | None = None
    videoUrl: str | None = None
    playCount: int | None = None
    isTop: bool = False
    channel: str
    variant: str


class CommentOut(BaseModel):
    id: str
    name: str
    seed: str
    content: str
    time: str
    likes: int
    liked: bool

class SourceIn(BaseModel):
    """RSS 源：新增入参。

    `key` 为内部标识（小写字母/数字/下划线），冲突即 409——
    它是播种与更新对齐的依据，必须唯一且稳定。
    """

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=100)
    channel: str = Field(min_length=1, max_length=30)
    url: str = Field(min_length=1, max_length=500)
    weight: int = Field(default=5, ge=0, le=100)
    enabled: bool = True
    # 是否遵守对方 robots.txt（P1-16）。默认遵守；个别源 robots 写得过严却实际
    # 提供公开 RSS，逐源可关——故是源级配置而非全局开关。
    #
    # ⚠️ **入参的多词字段一律驼峰**（与 `CommentCreateIn.parentId` / `MarkIn.newsId`
    # 同惯例，也与响应侧 `lastStatus` 一致）。写成蛇形 `respect_robots` 时，
    # pydantic 默认 `extra='ignore'` 会**静默丢弃**前端发来的 `respectRobots`，
    # 表现为「开关能点、但永远不生效」——且前后端各自的单测都覆盖不到这个缝。
    respectRobots: bool = True

    _channel = field_validator("channel")(_valid_channel)


class SourceUpdateIn(BaseModel):
    """RSS 源：局部更新（字段均可选）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    channel: str | None = Field(default=None, min_length=1, max_length=30)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    weight: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None
    # 入参多词字段走驼峰（见 `SourceIn.respectRobots` 的说明）
    respectRobots: bool | None = None

    _channel = field_validator("channel")(_valid_channel)


class AdminIn(BaseModel):
    """管理员白名单：添加入参。

    手机号形态与注册一致（`1` 开头 11 位）——注册入口的校验规则是
    「什么号能成为账号」的唯一定义，这里必须同源，否则会出现
    「加得进白名单但永远注册不了」的幽灵账号。
    """

    phone: str = Field(min_length=11, max_length=11, pattern=r"^1\d{10}$")
    note: str = Field(default="", max_length=100)


class NewsHideIn(BaseModel):
    """资讯下架入参（P1-20）：理由**必填**。

    「留痕是准入条件」——与 P1-18 自动禁用必须能解释同理。空理由（含纯空白）
    一律 422：运营当时省十秒钟，事后排查就要靠猜。`min_length=1` 挡不住
    单个空格，故再用 validator strip 后判空。
    """

    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("下架理由不能为空")
        return v


class NewsTopIn(BaseModel):
    """资讯置顶开关（PATCH 局部更新，字段可选——对齐 SourceUpdateIn 的「不传即不改」）。

    入参多词字段走驼峰（`isTop`）：写成蛇形会被 pydantic `extra='ignore'`
    静默丢弃，表现为「开关能点、但永远不生效」（见 SourceIn.respectRobots 注释）。
    """

    isTop: bool | None = None


class WordIn(BaseModel):
    """敏感词新增入参（P1-23）。

    `action` 三选一：`block`（直接拦截）/ `mask`（脱敏 + 待复核）/ `allow`
    （白名单豁免，专治误杀）。默认 `mask`——机器的判断是概率性的，默认动作
    应当是"先兜住再让人看"，而不是"直接吞掉用户的话"。

    `word` 的最短长度在**路由层**校验（需要归一化后按字符数计，且要读配置），
    这里只把关非空与长度上限。
    """

    word: str = Field(min_length=1, max_length=100)
    action: str = Field(default="mask", pattern="^(block|mask|allow)$")
    category: str = Field(default="", max_length=30)
    enabled: bool = True

    @field_validator("word")
    @classmethod
    def _word_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("敏感词不能为空")
        return v


class WordUpdateIn(BaseModel):
    """敏感词局部更新（P1-23）：**不允许改词本身**。

    改词等于换一个词（唯一约束、归一化结果、命中日志里的词全部随之漂移），
    语义上是"删一条 + 加一条"——走增删两条审计比一条 `update` 更容易追溯。
    """

    action: str | None = Field(default=None, pattern="^(block|mask|allow)$")
    category: str | None = Field(default=None, max_length=30)
    enabled: bool | None = None


class CommentHideIn(BaseModel):
    """评论隐藏入参（P1-22）：理由**必填**——与 NewsHideIn 同一处方。

    「留痕是准入条件」：隐藏是运营对他人内容的处置动作，事后排查
    （申诉/复盘）时「谁、为什么、什么时候」必须能从审计里读出来。
    空理由（含纯空白）一律 42200；strip 后的值才落库。
    """

    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("隐藏理由不能为空")
        return v


def _clean_aliases(value: list[str] | None) -> list[str] | None:
    """别名清洗：去空白、去重、限长。

    限长常量直接取自引擎（`MAX_ALIAS_LENGTH` / `MAX_ALIASES_PER_WORD`）——
    引擎加载时也会砍掉越界项，但入库前就拦住才能给运营**可解释的错误**，
    而不是「配了却静默没生效」那一类问题。
    `None` 原样返回（PATCH 的「不传 = 不动」）。
    """
    if value is None:
        return None
    out: list[str] = []
    for a in value:
        a = a.strip()
        if not a or a in out:
            continue
        if len(a) > MAX_ALIAS_LENGTH:
            raise ValueError(f"单条别名不能超过 {MAX_ALIAS_LENGTH} 个字符")
        out.append(a)
    return out


class NicknameUpdateIn(BaseModel):
    """修改昵称入参（P1-24）。

    不设 `min_length`：空值/纯空白/超长/字符越界全部走
    `validate_nickname_shape` 一道闸，统一以 **42203** 返回面向用户的提示
    （「昵称不能为空」比 pydantic 的 「at least 1 character」可执行得多）。
    `max_length=100` 只是解析层的硬闸——真实规则（24）由形状校验给出，
    正常输入（输入框有 maxlength）永远碰不到这个值。
    """

    name: str = Field(max_length=100)


class NicknameWordIn(BaseModel):
    """昵称敏感词新增入参（P1-24）。

    与 `WordIn`（P1-23 评论词库）的两处差异：
    1. 动作集是 `block`/`review`/`allow`——**没有 `mask`**，昵称没有
       「脱敏后照常发布」这个语义（那等于改掉用户自己起的名字）；
    2. 多一个 `aliases`（拼音全拼/首字母/谐音/变形）。字母写法的绕过
       （「shabi」「sb」）只有配了别名才拦得住——同音汉字替换没有字符级
       算法可解，靠运营补别名。

    主词与别名的最短长度在**路由层**校验（要归一化后按字符数计、要读配置），
    这里只把关非空与上限。
    """

    word: str = Field(min_length=1, max_length=100)
    action: str = Field(default="review", pattern="^(block|review|allow)$")
    category: str = Field(default="", max_length=30)
    aliases: list[str] = Field(default_factory=list, max_length=MAX_ALIASES_PER_WORD)
    enabled: bool = True

    @field_validator("word")
    @classmethod
    def _word_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("昵称敏感词不能为空")
        return v

    _aliases_clean = field_validator("aliases")(_clean_aliases)


class NicknameWordUpdateIn(BaseModel):
    """昵称敏感词局部更新（P1-24）：**不允许改词本身**——同 WordUpdateIn 处方。

    改词等于换一个词（唯一约束、归一化结果、命中日志里的词全部随之漂移），
    语义上是「删一条 + 加一条」——走增删两条审计比一条 `update` 更容易追溯。

    `aliases` 用 `None` 而非 `default_factory=list`：**「不传」与「明确清空」**
    必须可区分——传空数组是清空全部别名，不传是不动。
    """

    action: str | None = Field(default=None, pattern="^(block|review|allow)$")
    category: str | None = Field(default=None, max_length=30)
    aliases: list[str] | None = None
    enabled: bool | None = None

    _aliases_clean = field_validator("aliases")(_clean_aliases)


class ReportResolveIn(BaseModel):
    """举报处置入参（E5）：`hidden` 确认隐藏 / `kept` 驳回并恢复。"""

    resolution: str = Field(pattern="^(hidden|kept)$")
