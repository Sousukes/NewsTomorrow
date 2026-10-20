import type { Channel, ListKey } from '@/constants/channels';

export type NewsVariant = 'triple' | 'left-right' | 'text' | 'video';

/** 资讯频道：单一来源见 `@/constants/channels`，此处透出以保持既有引用路径 */
export type { Channel };

export interface NewsItem {
  id: string;
  title: string;
  /** 摘要，对应 GET /news 的 summary 字段；左文右图 / 纯文字 / 视频 变体展示 */
  summary?: string;
  /**
   * 正文（E1）：仅 GET /news/:id 详情返回，按段落以 \n 分隔。
   * 列表接口刻意不带（几 KB 一条，避免带宽浪费）；null/undefined = 暂无正文。
   */
  content?: string;
  source: string;
  /**
   * 来源稳定标识（E6b，对应后端 `rss_sources.key`）。
   *
   * 关注关系挂在它上面而不是 `source` 名称：运营改名后名称会变，key 不变。
   * **可空**：历史数据回填不上、或旧后端不下发时为 null/undefined——
   * 此时前端按「不可关注」处理（不显示关注按钮），而不是报错。
   */
  sourceKey?: string | null;
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

/**
 * 列表标签项：首页频道栏 + 关注流（E6b）共用这个形状。
 * `key` 是 `ListKey` 而非 `Channel`——「关注」不是频道，只是另一种列表键。
 */
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

/**
 * 管理端评论行（P1-22，对应 GET /api/admin/comments 的条目）。
 * 公开评论输出的超集：附隐藏三件套、所属资讯标题与作者名（治理认内容不认 id）。
 * 与 AdminNewsItem 的关键差别：已软删除的评论**不会出现**在管理端列表里
 * （删除是作者的不可逆行为，没有可恢复的状态）。
 */
export interface AdminCommentItem {
  id: string;
  newsId: string;
  newsTitle: string;
  userId: number | null;
  userName: string;
  content: string;
  /** ISO 时间串；管理端排序依据（created_at DESC, id DESC） */
  createdAt: string;
  likes: number;
  parentId: string | null;
  rootId: string | null;
  /** 主评论的**当前可见**回复数（已删除/已隐藏不计；回复行恒为 0） */
  replyCount: number;
  isDeleted: boolean;
  /** 是否已被运营隐藏（软隐藏：公开四路实时过滤，可恢复） */
  hidden: boolean;
  hiddenAt: string | null;
  /** 隐藏理由（必填，≤300 字）；恢复后为 null */
  hiddenReason: string | null;
  /**
   * 是否**待人工复核**（P1-23）：命中敏感词被脱敏后发布的评论。
   * 与 `hidden` 正交——hidden 是「已处置、不可见」，pending 是「未处置、可见」。
   */
  pendingReview: boolean;
  /** 人工复核通过的时间；未复核为 null */
  reviewedAt: string | null;
}

/** GET /api/admin/comments 的信封（筛选后的总数 + 游标分页） */
export interface AdminCommentListResult {
  list: AdminCommentItem[];
  /** 当前筛选条件下的总条数（界面显示"共 N 条"） */
  total: number;
  hasMore: boolean;
  nextCursor: string | null;
}

/** 管理端评论列表查询（不传的项后端用默认值） */
export interface AdminCommentQuery {
  /** all（默认，含已隐藏）| visible | hidden | pending（脱敏待复核） */
  status?: 'all' | 'visible' | 'hidden' | 'pending';
  /** 评论内容关键词（服务端 ILIKE） */
  keyword?: string;
  /** 按所属资讯过滤 */
  newsId?: string;
  limit?: number;
  /** 上一页末条的 id（服务端下发，客户端不解析） */
  cursor?: string | null;
}

/** 隐藏入参：理由必填（trim 后 ≤300，空则 422）——与 NewsHideInput 同一处方 */
export interface CommentHideInput {
  reason: string;
}

// ---------------------------------------------------------------- 敏感词库（P1-23）

/** 敏感词动作：拦截 / 脱敏待复核 / 白名单豁免 */
export type WordAction = 'block' | 'mask' | 'allow';

/**
 * 敏感词行（对应 GET /api/admin/words 的条目）。
 *
 * `action` 决定命中后怎么处理——三选一而不是开关：
 *   - `block`：直接拒绝提交（高危内容，不给它出现在页面上的机会）；
 *   - `mask`：脱敏后照常发布并置待复核（默认，机器判断是概率性的）；
 *   - `allow`：**白名单**，命中它的区间豁免（降误杀的主要手段）。
 */
export interface SensitiveWordItem {
  id: number;
  word: string;
  action: WordAction;
  /** 分组（广告 / 辱骂 / 违禁…），可为空串 */
  category: string;
  /** 停用而非删除：删错了要能一秒恢复 */
  enabled: boolean;
  /** 累计命中次数：高频词要么是热点、要么是误杀大户 */
  hitCount: number;
  createdBy: string;
  createdAt: string | null;
  updatedAt: string | null;
}

/** GET /api/admin/words 的信封 */
export interface WordListResult {
  list: SensitiveWordItem[];
  total: number;
}

/** 敏感词列表查询（不传的项后端用默认值） */
export interface WordQuery {
  keyword?: string;
  action?: WordAction | '';
  enabled?: boolean;
  limit?: number;
  offset?: number;
}

/** 新增敏感词入参（多词字段走驼峰；词太短 42201、重复 40905） */
export interface WordInput {
  word: string;
  action?: WordAction;
  category?: string;
  enabled?: boolean;
}

/** 局部更新入参（**不允许改词本身**：改词等于删一条加一条） */
export interface WordUpdateInput {
  action?: WordAction;
  category?: string;
  enabled?: boolean;
}

/** 命中日志行（只读：脱敏不可逆，它是误杀复盘与申诉的唯一证据） */
export interface SensitiveHitItem {
  id: number;
  word: string;
  /** block | mask（白名单命中不记日志） */
  action: 'block' | 'mask';
  /** 检测字段：正文 / 昵称 */
  field: 'content' | 'name';
  /** 拦截时没有评论，故可空 */
  commentId: string | null;
  newsId: string | null;
  userId: number | null;
  userName: string;
  /** **原文**片段（带上下文），脱敏后的正文里已经找不回来了 */
  snippet: string;
  createdAt: string;
}

/** GET /api/admin/moderation/hits 的信封（游标分页） */
export interface HitListResult {
  list: SensitiveHitItem[];
  total: number;
  hasMore: boolean;
  nextCursor: number | null;
}

/** 命中日志查询（不传的项后端用默认值） */
export interface HitQuery {
  word?: string;
  action?: 'block' | 'mask' | '';
  field?: 'content' | 'name' | '';
  limit?: number;
  /** 上一页末条的 id（服务端下发，客户端不解析） */
  cursor?: number | null;
}

// ---------------------------------------------------------------- 昵称敏感词（P1-24）

/**
 * 昵称词动作（P1-24）。与评论词库 `WordAction` 的关键差别：**没有 `mask`**——
 * 昵称是账号级属性，"脱敏后发布"等于改掉用户自己起的名字，而用户此刻改不了。
 *   - `block`：拒绝设置（40006，提示不回显命中词）；
 *   - `review`：放行 + 置待人工复核（机器的判断是概率性的，先放行再让人看）；
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
  /** 分组（广告 / 辱骂 / 违禁…），可为空串 */
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
  /** 上一页末条的 id（服务端下发，客户端不解析） */
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
