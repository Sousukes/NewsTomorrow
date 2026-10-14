"""Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field, field_validator

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
