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
  /** 是否遵守对方 robots.txt（P1-16）；个别源 robots 写得过严却公开提供 RSS 时可关 */
  respectRobots: boolean;
  /** 连续失败次数（P1-18），成功即归零。是"偶发抖一下"与"早就坏了"的唯一区分依据 */
  consecutiveFailures: number;
  /**
   * 最近一次失败的**性质**（P1-18）：
   * `permanent` = 404 / robots 拒绝 / 解析无条目，等不会好，必须人工处理；
   * `transient` = 超时 / 5xx / 429，系统会自动退避重试。
   */
  failureKind: 'permanent' | 'transient' | null;
  /** 熔断到期时间（ISO 串，P1-18）；null = 未熔断 */
  nextRetryAt: string | null;
  /**
   * 近期健康摘要（P1-19）。`null` = 这段时间内没抓过它，
   * 与"抓了但全失败"（`rate === 0`）是两回事——前者通常说明源没启用或服务没跑。
   */
  health: SourceHealthBrief | null;
}

/**
 * 列表页用的近期摘要（P1-19）：只放"值不值得点进去看细节"所需的信息。
 * 趋势图放在单源页，列表上复刻一遍只会让首屏变吵。
 */
export interface SourceHealthBrief {
  /** 窗口内的抓取轮次 */
  attempts: number;
  /** 成功率 0–1；`null` = 窗口内没有尝试 */
  rate: number | null;
  /** **成功**轮次的平均条数；`null` = 没有成功轮。看"源是不是在变空"用这个 */
  avgItems: number | null;
}

/** 单源趋势页的完整汇总 */
export interface SourceHealthSummary extends SourceHealthBrief {
  ok: number;
  failed: number;
  /** 窗口内抓到的总条数 */
  items: number;
  lastOkAt: string | null;
  lastFailAt: string | null;
}

/**
 * 按天分桶的一个点（P1-19）。
 * **缺的天不补零**：空白表示"那天没抓它"，补零会伪装成"抓了但全失败"。
 */
export interface SourceHealthPoint {
  /** `YYYY-MM-DD` */
  date: string;
  total: number;
  ok: number;
  failed: number;
  items: number;
  /** 当天没有尝试时为 null */
  rate: number | null;
}

/** 一次失败的原文（P1-19） */
export interface SourceHealthFailure {
  at: string;
  error: string | null;
  /** 失败性质，与 P1-18 同义 */
  kind: 'permanent' | 'transient' | null;
  items: number;
}

/** GET /api/admin/sources/:key/health 的响应（P1-19） */
export interface SourceHealth {
  key: string;
  /** **实际生效**的窗口天数：请求 90 天而只留 30 天时会被收敛 */
  days: number;
  /** 服务端保留期，用于解释"为什么最多只能看这么久" */
  keepDays: number;
  summary: SourceHealthSummary;
  points: SourceHealthPoint[];
  failures: SourceHealthFailure[];
}

/** GET /api/admin/sources 的响应（P1-19 起带窗口天数） */
export interface SourceListResult {
  list: RssSourceItem[];
  /** 摘要统计的窗口天数：不带它，成功率这个数字就没有时间范围 */
  healthDays: number;
}

/** 新增 / 更新源的入参（更新时全部可选，未传字段保持原值） */
export interface SourceInput {
  name?: string;
  channel?: Channel;
  url?: string;
  weight?: number;
  enabled?: boolean;
  /** 是否遵守 robots.txt（P1-16）；不传则后端保持原值 */
  respectRobots?: boolean;
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

/** 运营审计行（对应 GET /api/admin/audit 的条目） */
export interface AdminAuditItem {
  id: number;
  /** 操作者手机号 */
  actorPhone: string;
  /** 动作：`对象.动作`（如 source.update），前端据此映射中文标签 */
  action: string;
  /** 对象标识（源 key / 手机号）；全局动作（如重置）为空串 */
  target: string;
  /**
   * 变更明细：更新为 `{ changes: { 字段: [旧, 新] } }`，
   * 增删为 `{ snapshot: {...} }`，重置为 `{ seeded: number }`。
   */
  detail: Record<string, unknown>;
  /** ISO 时间串 */
  createdAt: string;
}

/** 审计时间线信封（游标分页，新的在前） */
export interface AdminAuditPage {
  list: AdminAuditItem[];
  hasMore: boolean;
  nextCursor: string | null;
}

/**
 * 管理端资讯行（P1-20，对应 GET /api/admin/news 的条目）。
 * 公开 `NewsItem` 的超集：附下架三件套与排查字段（发布时间 / 热度分）。
 */
export interface AdminNewsItem {
  id: string;
  title: string;
  summary: string | null;
  source: string;
  commentCount: number;
  time: string;
  isTop: boolean;
  channel: Channel;
  /** ISO 时间串；管理端排序与定位的依据 */
  publishedAt: string | null;
  /** 落库热度分（互动加权 × 时间衰减），置顶决策参考 */
  hotScore: number;
  /** 是否已下架（软下架：公开四路实时过滤，可恢复） */
  hidden: boolean;
  hiddenAt: string | null;
  /** 下架理由（必填，≤300 字）；恢复后为 null */
  hiddenReason: string | null;
}

/** GET /api/admin/news 的信封（筛选后的总数 + 游标分页） */
export interface AdminNewsListResult {
  list: AdminNewsItem[];
  /** 当前筛选条件下的总条数（界面显示"共 N 条"） */
  total: number;
  hasMore: boolean;
  nextCursor: string | null;
}

/** 管理端资讯列表查询（不传的项后端用默认值） */
export interface AdminNewsQuery {
  /** all（默认，含已下架）| visible | hidden */
  status?: 'all' | 'visible' | 'hidden';
  /** 标题/摘要关键词（服务端 ILIKE） */
  keyword?: string;
  channel?: Channel;
  limit?: number;
  /** 上一页末条的游标（服务端下发，客户端不解析） */
  cursor?: string | null;
}

/** 下架入参：理由必填（trim 后 ≤300，空则 422） */
export interface NewsHideInput {
  reason: string;
}

/** 置顶开关入参（PATCH 局部更新；多词字段走驼峰，蛇形会被静默丢弃） */
export interface NewsTopInput {
  isTop: boolean;
}
