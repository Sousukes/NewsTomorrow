import type { Channel, ListKey } from '@/constants/channels';

export type NewsVariant = 'triple' | 'left-right' | 'text' | 'video';

/** 透出频道类型，保持既有引用路径 */
export type { Channel };

export interface NewsItem {
  id: string;
  title: string;
  /** 摘要 */
  summary?: string;
  /** 正文（仅详情接口下发，段落以 \n 分隔） */
  content?: string;
  source: string;
  /** 来源稳定标识（关注挂在它上面，改名不影响）；可空 = 不可关注 */
  sourceKey?: string | null;
  /** 来自本地阅读副本（断网时），仅用于 UI 标注 */
  fromCache?: boolean;
  commentCount?: number;
  time: string;
  /** 三图变体取前 3 张 */
  images?: string[];
  /** 单图 / 视频封面 */
  cover?: string;
  /** video 变体播放用 */
  videoUrl?: string;
  /** video 变体替代评论数展示 */
  playCount?: number;
  /** 置顶标 */
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

/** 通用标签项 */
export interface Tab {
  label: string;
  key: string;
  active?: boolean;
}

/** 频道栏 + 关注流共用；key 是 ListKey（关注不是频道） */
export interface ChannelTab {
  label: string;
  key: ListKey;
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
  /** 无归属（历史/种子数据）时为 null */
  userId: number | null;
  /** 决定能否删除；未登录恒 false */
  mine: boolean;
  /** 软删除标记 */
  isDeleted?: boolean;
  /** 被回复的评论 id；主评论为 null */
  parentId?: string | null;
  /** 所属主评论；回复的回复拍平到同一 root */
  rootId?: string | null;
  /** 回复项恒 0 */
  replyCount?: number;
  /** 回复预览（最早 2 条） */
  replies?: Comment[];
  /** 仅回复有值 */
  replyToName?: string | null;
  replyToUserId?: number | null;
}

/** 消息中心条目（对应 /api/messages） */
export interface Message {
  id: string;
  /** system | comment | recommend */
  type: 'system' | 'comment' | 'recommend';
  title: string;
  summary: string;
  /** 关联资讯，可空 */
  newsId?: string;
  time: string;
  /** 当前用户是否已读 */
  read: boolean;
}

/** RSS 源（GET /api/admin/sources 条目） */
export interface RssSourceItem {
  /** 内部标识，与内置源对齐 */
  key: string;
  name: string;
  channel: Channel;
  url: string;
  /** 多源同稿时权重高者先入库 */
  weight: number;
  enabled: boolean;
  /** null = 从未抓过 */
  lastStatus: 'ok' | 'error' | null;
  /** 截断到 300 字 */
  lastError: string | null;
  lastItemCount: number | null;
  /** null = 从未抓过 */
  lastFetchedAt: string | null;
  /** 个别源 robots 过严但公开提供 RSS 时可关 */
  respectRobots: boolean;
  /** 成功即归零 */
  consecutiveFailures: number;
  /** permanent = 等不会好（404/robots/无条目）；transient = 会自动退避重试 */
  failureKind: 'permanent' | 'transient' | null;
  /** 熔断到期时间；null = 未熔断 */
  nextRetryAt: string | null;
  /** null = 窗口内没抓过（与"抓了全失败"不同） */
  health: SourceHealthBrief | null;
}

/** 列表页用摘要；趋势图只在单源页 */
export interface SourceHealthBrief {
  /** 窗口内轮次 */
  attempts: number;
  /** null = 窗口内无尝试 */
  rate: number | null;
  /** 仅统计成功轮次；null = 无成功轮 */
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

/** 按天分桶；缺的天不补零（补零会伪装成"抓了但全失败"） */
export interface SourceHealthPoint {
  /** `YYYY-MM-DD` */
  date: string;
  total: number;
  ok: number;
  failed: number;
  items: number;
  /** 无尝试时为 null */
  rate: number | null;
}

/** 一次失败的原文 */
export interface SourceHealthFailure {
  at: string;
  error: string | null;
  /** 同 RssSourceItem.failureKind */
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
  /** 不传则后端保持原值 */
  respectRobots?: boolean;
}

/** 新增源必须自带 key */
export interface SourceCreateInput extends SourceInput {
  key: string;
}

/** 管理员白名单行 */
export interface AdminUserItem {
  phone: string;
  /** 如「运营-张三」 */
  note: string;
  /** 审计用 */
  addedBy: string;
  /** ISO 时间串 */
  createdAt: string;
  /** false = 预授权，注册后自动生效 */
  registered: boolean;
  /** 未注册为空串 */
  name: string;
}

/** 库内行 + env 管理员数量（不下发号码） */
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

/** 运营审计行 */
export interface AdminAuditItem {
  id: number;
  /** 操作者手机号 */
  actorPhone: string;
  /** `对象.动作`，前端映射中文标签 */
  action: string;
  /** 全局动作为空串 */
  target: string;
  /** changes / snapshot / seeded，按动作而定 */
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

/** 管理端资讯行：NewsItem 超集，附下架字段与排查字段 */
export interface AdminNewsItem {
  id: string;
  title: string;
  summary: string | null;
  source: string;
  commentCount: number;
  time: string;
  isTop: boolean;
  channel: Channel;
  /** 管理端排序依据 */
  publishedAt: string | null;
  /** 置顶决策参考 */
  hotScore: number;
  /** 软下架，可恢复 */
  hidden: boolean;
  hiddenAt: string | null;
  /** 必填；恢复后为 null */
  hiddenReason: string | null;
}

/** 筛选后总数 + 游标分页 */
export interface AdminNewsListResult {
  list: AdminNewsItem[];
  /** 当前筛选下的总数 */
  total: number;
  hasMore: boolean;
  nextCursor: string | null;
}

/** 不传的项后端用默认值 */
export interface AdminNewsQuery {
  /** all（默认，含已下架）| visible | hidden */
  status?: 'all' | 'visible' | 'hidden';
  /** 服务端 ILIKE */
  keyword?: string;
  channel?: Channel;
  limit?: number;
  /** 客户端不解析 */
  cursor?: string | null;
}

/** 理由必填（trim 后 ≤300，空则 422） */
export interface NewsHideInput {
  reason: string;
}

/** 多词字段走驼峰，蛇形会被静默丢弃 */
export interface NewsTopInput {
  isTop: boolean;
}

/**
 * 管理端评论行：附隐藏字段与所属资讯标题（治理认内容不认 id）。
 * 已软删除的评论不出现——删除是作者的不可逆行为。
 */
export interface AdminCommentItem {
  id: string;
  newsId: string;
  newsTitle: string;
  userId: number | null;
  userName: string;
  content: string;
  /** 排序依据 */
  createdAt: string;
  likes: number;
  parentId: string | null;
  rootId: string | null;
  /** 仅计可见回复；回复行恒 0 */
  replyCount: number;
  isDeleted: boolean;
  /** 软隐藏，可恢复 */
  hidden: boolean;
  hiddenAt: string | null;
  /** 必填；恢复后为 null */
  hiddenReason: string | null;
  /** 与 hidden 正交：pending = 未处置但可见 */
  pendingReview: boolean;
  /** 未复核为 null */
  reviewedAt: string | null;
}

/** 筛选后总数 + 游标分页 */
export interface AdminCommentListResult {
  list: AdminCommentItem[];
  /** 当前筛选下的总数 */
  total: number;
  hasMore: boolean;
  nextCursor: string | null;
}

/** 不传的项后端用默认值 */
export interface AdminCommentQuery {
  /** all（默认，含已隐藏）| visible | hidden | pending */
  status?: 'all' | 'visible' | 'hidden' | 'pending';
  /** 服务端 ILIKE */
  keyword?: string;
  /** 按所属资讯过滤 */
  newsId?: string;
  limit?: number;
  /** 客户端不解析 */
  cursor?: string | null;
}

/** 隐藏入参：理由必填（trim 后 ≤300，空则 422）——与 NewsHideInput 同一处方 */
export interface CommentHideInput {
  reason: string;
}

// ---------------------------------------------------------------- 敏感词库（P1-23）

/** 拦截 / 脱敏待复核 / 白名单豁免 */
export type WordAction = 'block' | 'mask' | 'allow';

/** 敏感词行。action 三选一：block 拒绝提交 / mask 脱敏并置待复核 / allow 白名单豁免。 */
export interface SensitiveWordItem {
  id: number;
  word: string;
  action: WordAction;
  /** 可为空串 */
  category: string;
  /** 停用而非删除 */
  enabled: boolean;
  /** 高频词要么是热点、要么是误杀大户 */
  hitCount: number;
  createdBy: string;
  createdAt: string | null;
  updatedAt: string | null;
}

/** 词库列表 */
export interface WordListResult {
  list: SensitiveWordItem[];
  total: number;
}

/** 不传的项后端用默认值 */
export interface WordQuery {
  keyword?: string;
  action?: WordAction | '';
  enabled?: boolean;
  limit?: number;
  offset?: number;
}

/** 词太短 42201、重复 40905 */
export interface WordInput {
  word: string;
  action?: WordAction;
  category?: string;
  enabled?: boolean;
}

/** 不允许改词本身（等于删一条加一条） */
export interface WordUpdateInput {
  action?: WordAction;
  category?: string;
  enabled?: boolean;
}

/** 命中日志（只读）：误杀复盘与申诉的唯一证据 */
export interface SensitiveHitItem {
  id: number;
  word: string;
  /** 白名单命中不记日志 */
  action: 'block' | 'mask';
  /** 正文 / 昵称 */
  field: 'content' | 'name';
  /** 拦截时没有评论 */
  commentId: string | null;
  newsId: string | null;
  userId: number | null;
  userName: string;
  /** 原文片段（脱敏后正文里找不回来） */
  snippet: string;
  createdAt: string;
}

/** 游标分页 */
export interface HitListResult {
  list: SensitiveHitItem[];
  total: number;
  hasMore: boolean;
  nextCursor: number | null;
}

/** 不传的项后端用默认值 */
export interface HitQuery {
  word?: string;
  action?: 'block' | 'mask' | '';
  field?: 'content' | 'name' | '';
  limit?: number;
  /** 客户端不解析 */
  cursor?: number | null;
}

// ---------------------------------------------------------------- 昵称敏感词（P1-24）

/**
 * 昵称词动作。没有 mask：昵称是账号级属性，脱敏等于改掉用户的名字。
 * block 拒绝设置（40006）；review 放行并置待复核；
 *   - `allow`：白名单豁免（命中区间被 allow 区间完全包裹才豁免，降误杀）。
 */
export type NicknameWordAction = 'block' | 'review' | 'allow';

/**
 * 昵称敏感词行（对应 GET /api/admin/nickname-words 的条目）。
 *
 * `aliases` 是昵称词库独有的字段：拼音全拼 / 首字母 / 谐音 / 变形
 * （「shabi」「sb」）只有配了别名才拦得住——同音汉字替换没有字符级算法
 * 可解，靠运营发现后补别名。别名与主词走同一条归一化管道。
 */
export interface NicknameWordItem {
  id: number;
  word: string;
  action: NicknameWordAction;
  /** 可为空串 */
  category: string;
  aliases: string[];
  enabled: boolean;
  /** 累计命中次数 */
  hitCount: number;
  createdBy: string;
  createdAt: string | null;
  updatedAt: string | null;
}

/** GET /api/admin/nickname-words 的信封（limit+offset，运营要一遍扫完） */
export interface NicknameWordListResult {
  list: NicknameWordItem[];
  total: number;
}

/** 昵称词库列表查询（不传的项后端用默认值） */
export interface NicknameWordQuery {
  /** 只匹配主词（别名在每行 aliases 字段里可见，不搜） */
  keyword?: string;
  action?: NicknameWordAction | '';
  enabled?: boolean;
  limit?: number;
  offset?: number;
}

/** 新增昵称敏感词入参（主词或任一别名归一化后不足 2 个有效字符 → 42204） */
export interface NicknameWordInput {
  word: string;
  action?: NicknameWordAction;
  category?: string;
  aliases?: string[];
  enabled?: boolean;
}

/** 局部更新入参（**不允许改词本身**；`aliases` 传空数组 = 清空，不传 = 不动） */
export interface NicknameWordUpdateInput {
  action?: NicknameWordAction;
  category?: string;
  aliases?: string[];
  enabled?: boolean;
}

/** 昵称命中日志行（只读：误杀复盘与申诉的唯一证据） */
export interface NicknameHitItem {
  id: number;
  /** 库中主词 */
  word: string;
  /** 实际命中的模式（主词本身或某条别名）：回答「是哪条别名拦住的」 */
  matched: string;
  action: 'block' | 'review';
  /** 入口：注册 / 改名 */
  source: 'register' | 'update';
  /** 拦截注册时账号未落库，故可空 */
  userId: number | null;
  userName: string;
  /** 提交的昵称原文：昵称整体就是上下文，判断误杀不需要更多信息 */
  snippet: string;
  createdAt: string;
}

/** GET /api/admin/nickname-hits 的信封（游标分页，新的在前） */
export interface NicknameHitListResult {
  list: NicknameHitItem[];
  total: number;
  hasMore: boolean;
  nextCursor: number | null;
}

/** 昵称命中日志查询（不传的项后端用默认值） */
export interface NicknameHitQuery {
  word?: string;
  action?: 'block' | 'review' | '';
  source?: 'register' | 'update' | '';
  /** 只看某个用户的命中：与复核队列互相印证 */
  userId?: number;
  limit?: number;
  /** 客户端不解析 */
  cursor?: number | null;
}

/**
 * 昵称复核队列行（对应 GET /api/admin/nickname-reviews 的条目）。
 *
 * 与评论复核同构，但对象是**账号属性**。队列回答「哪个提交需要人看」而不是
 * 「他现在叫什么」：用户可能已改名，故 `flaggedName`/`flaggedMatched` 取该
 * 用户最近一条 review 级命中的原文与命中模式。
 */
export interface NicknameReviewItem {
  userId: number;
  /** 当前昵称 */
  name: string;
  phone: string;
  /** 最近一次 review 级命中的时间 */
  flaggedAt: string | null;
  /** 当初提交的昵称 */
  flaggedName: string | null;
  /** 哪条别名拦的 */
  flaggedMatched: string | null;
}

/** GET /api/admin/nickname-reviews 的信封（limit+offset） */
export interface NicknameReviewListResult {
  list: NicknameReviewItem[];
  total: number;
}

/** 复核队列查询（keyword 匹配当前昵称或手机号） */
export interface NicknameReviewQuery {
  keyword?: string;
  limit?: number;
  offset?: number;
}

/** POST /api/admin/nickname-reviews/:user_id 的结果（不改昵称本身，只清标志） */
export interface NicknameReviewResult {
  userId: number;
  name: string;
  phone: string;
  pendingReview: boolean;
  reviewedAt: string | null;
}

/**
 * PUT /api/me/nickname 的结果（P1-24）。
 *
 * 三级：`pass` 原样保存并清除待复核标志；`review` 放行 + 置待复核
 * （消息即面向用户的合规提示）；`block` 走 40006，不到这里。
 * `reason` 是机器侧的判定原因（命中原因），同样**不含命中词原文**。
 */
export interface NicknameUpdateResult {
  name: string;
  result: 'pass' | 'review';
  reason: string;
  pendingReview: boolean;
}

// ------------------------------------------------ 运营大盘（P1-76 / E7）

/** 内容与账号的**存量**规模（不随 days 变） */
export interface DashboardContent {
  news: number;
  /** 已下架（软下架）：与 news 分开数，否则"下架了多少"永远看不出来 */
  newsHidden: number;
  comments: number;
  users: number;
  sources: number;
  sourcesEnabled: number;
}

/** "系统在等人"的队列——大盘上最该被看见的几个数字 */
export interface DashboardTodo {
  /** E5：待复核举报（只数 pending；已处置/驳回不占待办数） */
  reports: number;
  /** P1-23：命中敏感词被脱敏后发布、等人工看一眼的评论 */
  commentReviews: number;
  /** P1-24：被放行但待人工复核的昵称 */
  nicknameReviews: number;
}

/** 窗口内的互动量 */
export interface DashboardEngagement {
  searches: number;
  favorites: number;
  history: number;
  follows: number;
  exposures: number;
  dislikes: number;
}

/** 窗口内最值得关注的源（按失败率升序取 Top N） */
export interface DashboardWorstSource {
  key: string;
  name: string;
  attempts: number;
  ok: number;
  failed: number;
  /** 成功率；无尝试时为 null */
  rate: number | null;
  /** 成功轮次的平均条数；无成功轮次为 null */
  avgItems: number | null;
  /** 最近一次失败的绝对时间（ISO）；无失败为 null */
  lastFailAt: string | null;
}

export interface DashboardSourceHealth {
  windowDays: number;
  /** 有尝试且有失败的源数（空闲源不算） */
  unhealthy: number;
  worst: DashboardWorstSource[];
}

/** 窗口内的治理量 + 命中趋势（按 UTC 天分桶，升序） */
export interface DashboardModeration {
  newsHidden: number;
  commentsHidden: number;
  hits: { sensitive: number; nickname: number };
  hitsTrend: { date: string; sensitive: number; nickname: number }[];
}

/** GET /api/admin/dashboard 的完整响应（单端点，六个板块） */
export interface Dashboard {
  days: number;
  /** 聚合时刻（ISO，UTC） */
  generatedAt: string;
  content: DashboardContent;
  todo: DashboardTodo;
  engagement: DashboardEngagement;
  sourceHealth: DashboardSourceHealth;
  moderation: DashboardModeration;
  search: { hot: { term: string; count: number }[] };
}

/** 大盘查询：days 越界由后端 Query(ge=1, le=365) 拒，超保留期收敛 */
export interface DashboardQuery {
  days?: number;
}

// ------------------------------------------------ 举报复核（P1-77 / E5 前端补齐）

/** 管理端举报行（对应 GET /api/admin/reports 的条目） */
export interface AdminReportItem {
  id: number;
  targetType: 'comment' | 'news';
  targetId: string;
  reason: string;
  /** pending（待处理）| resolved（已处置）| dismissed（已驳回） */
  status: 'pending' | 'resolved' | 'dismissed';
  /** 人工结论：hidden（确认隐藏）| kept（驳回）；未处置为 null */
  resolution: 'hidden' | 'kept' | null;
  /** ISO 时间串 */
  createdAt: string;
  /**
   * ---- 被举报对象的预览（P1-77）----
   * 复核页要回答"这条该不该藏"，只给 id 等于让运营自己去别处对照：
   * 评论尤其致命（它没有独立的公开页面，光看 id 无从判断）。
   */
  /** 资讯标题；评论为 null */
  targetTitle: string | null;
  /** 评论内容片段（截断到 60 字）；资讯为 null */
  targetSnippet: string | null;
  /** 能跳到的资讯 id：资讯是自身 id，评论是其所属资讯 id；对象不存在为 null */
  targetNewsId: string | null;
  /** 被举报对象已被删除（举报刻意不建外键，所以这是正常状态） */
  targetMissing: boolean;
}

/**
 * 复核队列信封。**没有 `hasMore`**：后端只给 `nextCursor`，
 * 前端据此推导（与审计页同款）。
 */
export interface AdminReportListResult {
  list: AdminReportItem[];
  nextCursor: string | null;
}

export interface AdminReportQuery {
  /** all | pending（默认）| resolved | dismissed */
  status?: 'all' | 'pending' | 'resolved' | 'dismissed';
  targetType?: 'comment' | 'news';
  limit?: number;
  /** 上一页末条的 id（客户端不解析） */
  cursor?: number | null;
}

/** 处置入参：hidden 确认隐藏 / kept 驳回并恢复可见 */
export interface ReportResolveInput {
  resolution: 'hidden' | 'kept';
}
