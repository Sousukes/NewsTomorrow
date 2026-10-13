import type { Channel } from '@/constants/channels';

export type NewsVariant = 'triple' | 'left-right' | 'text' | 'video';

/** 资讯频道：单一来源见 `@/constants/channels`，此处透出以保持既有引用路径 */
export type { Channel };

export interface NewsItem {
  id: string;
  title: string;
  /** 摘要，对应 GET /news 的 summary 字段；左文右图 / 纯文字 / 视频 变体展示 */
  summary?: string;
  source: string;
  commentCount?: number;
  time: string;
  /** 配图 URL 列表，三图变体取前 3 张 */
  images?: string[];
  /** 单图 / 视频封面 */
  cover?: string;
  /** 视频地址，video 变体播放用 */
  videoUrl?: string;
  /** 播放数，video 变体替代评论展示位 */
  playCount?: number;
  /** 置顶，纯文字变体左上角蓝标 */
  isTop?: boolean;
  channel: Channel;
  variant: NewsVariant;
}

export interface Author {
  name: string;
  avatar?: string;
  time?: string;
  followed?: boolean;
}

/** 通用标签项（任意 key 的横向标签栏，如视频页的直播/影视/搞笑子分类） */
export interface Tab {
  label: string;
  key: string;
  active?: boolean;
}

/** 资讯频道标签项：`ChannelTab[]` 可赋给 `Tab[]`（Channel ⊂ string） */
export interface ChannelTab {
  label: string;
  key: Channel;
  active?: boolean;
}

export interface TabItem {
  key: string;
  label: string;
  icon: 'home' | 'video' | 'message' | 'mine';
}

/** 评论（对应 GET/POST/PATCH /news/:id/comments） */
export interface Comment {
  id: string;
  name: string;
  seed: string;
  content: string;
  time: string;
  likes: number;
  liked: boolean;
  /** 作者用户 id；历史/种子评论无归属时为 null */
  userId: number | null;
  /** 是否为当前登录用户所发（未登录恒为 false）——决定能否删除 */
  mine: boolean;
  /** 是否已删除（软删除）；列表接口不会返回已删除项 */
  isDeleted?: boolean;
  /** 两级评论（P1-12）：被回复的评论 id；主评论为 null */
  parentId?: string | null;
  /** 所属主评论 id；主评论自身为 null。回复的回复拍平到同一 root */
  rootId?: string | null;
  /** 主评论下的回复条数（回复项恒 0，不嵌套计数） */
  replyCount?: number;
  /** 主评论附带的回复预览（最早 2 条；完整列表走 replies 接口懒加载） */
  replies?: Comment[];
  /** 回复场景：被回复者的展示名 / 用户 id（主评论为 null） */
  replyToName?: string | null;
  replyToUserId?: number | null;
}

/** 消息中心条目（对应 /api/messages） */
export interface Message {
  id: string;
  /** system 系统通知 | comment 评论互动 | recommend 内容推荐 */
  type: 'system' | 'comment' | 'recommend';
  title: string;
  summary: string;
  /** 关联资讯 id，可为空 */
  newsId?: string;
  time: string;
  /** 当前用户是否已读 */
  read: boolean;
}

/** RSS 源（对应 GET /api/admin/sources 的条目；字段名为后端 _out 的 camelCase） */
export interface RssSourceItem {
  /** 内部标识，也是与内置源对齐的主键 */
  key: string;
  name: string;
  channel: Channel;
  url: string;
  /** 权重：多源转载同一稿件时，权重高者先入库 */
  weight: number;
  enabled: boolean;
  /** 最近一次抓取状态；null = 从未抓过 */
  lastStatus: 'ok' | 'error' | null;
  /** 最近一次失败原因（截断到 300 字） */
  lastError: string | null;
  lastItemCount: number | null;
  /** ISO 时间串；null = 从未抓过 */
  lastFetchedAt: string | null;
}

/** 新增 / 更新源的入参（更新时全部可选，未传字段保持原值） */
export interface SourceInput {
  name?: string;
  channel?: Channel;
  url?: string;
  weight?: number;
  enabled?: boolean;
}

/** 新增源必须自带 key */
export interface SourceCreateInput extends SourceInput {
  key: string;
}

/** 管理员白名单行（对应 GET /api/admin/users 的条目） */
export interface AdminUserItem {
  phone: string;
  /** 备注（如「运营-张三」） */
  note: string;
  /** 添加者手机号（审计用） */
  addedBy: string;
  /** ISO 时间串 */
  createdAt: string;
  /** 该手机号是否已注册账号；false = 预授权（注册后自动生效） */
  registered: boolean;
  /** 已注册时的用户名；未注册为空串 */
  name: string;
}

/** 白名单列表信封：库内行 + env 来源的管理员数量（不下发号码） */
export interface AdminUserList {
  list: AdminUserItem[];
  total: number;
  envAdminCount: number;
}

/** 添加管理员的入参 */
export interface AdminUserInput {
  phone: string;
  note?: string;
}
