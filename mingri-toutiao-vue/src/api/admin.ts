/**
 * 管理端接口：RSS 源配置（后端 P1-9，界面 P1-10）+ 管理员白名单（P1-14）
 * + 运营审计（P1-15）+ 资讯审核与下架（P1-20）+ 评论治理（P1-22）
 * + 敏感词库与命中日志（P1-23）+ 昵称敏感词与复核队列（P1-24）。
 *
 * 与 `api/me.ts` 的关键差别：**没有 mock 短路**。源配置与白名单只存在于
 * 后端数据库中，mock 模式下并无真实对象可操作——伪造一份"能增能删"的假列表
 * 只会误导排障。入口由 `user.isAdmin`（登录响应下发）控制，mock 模式下该标志
 * 恒为 false，因此正常情况下这些函数不会被调到；直接手敲网址进入时会在页面上
 * 看到明确提示。
 */
import { USE_MOCK } from './config';
import { request } from './http';
import { toChannel } from '@/constants/channels';
import type {
  AdminAuditItem,
  AdminAuditPage,
  AdminCommentItem,
  AdminCommentListResult,
  AdminCommentQuery,
  AdminNewsItem,
  AdminNewsListResult,
  AdminNewsQuery,
  AdminUserInput,
  AdminUserItem,
  AdminUserList,
  CommentHideInput,
  HitListResult,
  HitQuery,
  NewsHideInput,
  NewsTopInput,
  NicknameHitItem,
  NicknameHitListResult,
  NicknameHitQuery,
  NicknameReviewItem,
  NicknameReviewListResult,
  NicknameReviewQuery,
  NicknameReviewResult,
  NicknameWordAction,
  NicknameWordInput,
  NicknameWordItem,
  NicknameWordListResult,
  NicknameWordQuery,
  NicknameWordUpdateInput,
  RssSourceItem,
  SensitiveHitItem,
  SensitiveWordItem,
  SourceCreateInput,
  SourceHealth,
  SourceHealthBrief,
  SourceHealthFailure,
  SourceHealthPoint,
  SourceHealthSummary,
  SourceInput,
  SourceListResult,
  WordAction,
  WordInput,
  WordListResult,
  WordQuery,
  WordUpdateInput,
} from '@/types';

/** 当前构建是否具备源管理能力（mock 模式未连接后端，恒为 false） */
export const ADMIN_SOURCES_SUPPORTED = !USE_MOCK;

function ensureRealBackend(): void {
  if (USE_MOCK) {
    throw new Error('当前为 mock 模式（未连接后端），RSS 源管理不可用');
  }
}

function num(raw: unknown): number | null {
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/**
 * 近期健康摘要（P1-19）。
 *
 * 缺失一律回落 `null` 而不是"全零摘要"：**"没抓过"和"抓了全挂"必须能区分**——
 * 前者通常说明源没启用或服务没跑，后者才是要处理的问题。
 */
function toHealthBrief(raw: unknown): SourceHealthBrief | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const attempts = num(r.attempts);
  if (attempts === null) return null;
  const rate = num(r.rate);
  return {
    attempts,
    rate: rate === null ? null : Math.min(1, Math.max(0, rate)),
    avgItems: num(r.avgItems),
  };
}

function toHealthPoint(raw: unknown): SourceHealthPoint | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const date = typeof r.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(r.date) ? r.date : '';
  if (!date) return null; // 没有日期的点无法落位
  const total = num(r.total);
  const rate = num(r.rate);
  return {
    date,
    total: total ?? 0,
    ok: num(r.ok) ?? 0,
    failed: num(r.failed) ?? 0,
    items: num(r.items) ?? 0,
    rate: rate === null ? null : Math.min(1, Math.max(0, rate)),
  };
}

function toHealthFailure(raw: unknown): SourceHealthFailure | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const at = typeof r.at === 'string' && r.at ? r.at : '';
  if (!at) return null; // 没有时间的失败无法排障，丢弃
  return {
    at,
    error: typeof r.error === 'string' && r.error ? r.error : null,
    kind: r.kind === 'permanent' || r.kind === 'transient' ? r.kind : null,
    items: num(r.items) ?? 0,
  };
}

function toSource(raw: unknown): RssSourceItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const key = typeof r.key === 'string' ? r.key : '';
  if (!key) return null; // 没有 key 的行无法定位，宁可丢弃也不要渲染成空壳
  return {
    key,
    name: typeof r.name === 'string' && r.name ? r.name : key,
    // 后端已校验 channel 合法，这里仍过一次 toChannel：接口返回值不可信，
    // 且非法值若原样透出会让频道标签显示成 key 本身
    channel: toChannel(r.channel),
    url: typeof r.url === 'string' ? r.url : '',
    weight: Number.isFinite(Number(r.weight)) ? Number(r.weight) : 0,
    enabled: r.enabled !== false,
    lastStatus: r.lastStatus === 'ok' || r.lastStatus === 'error' ? r.lastStatus : null,
    lastError: typeof r.lastError === 'string' && r.lastError ? r.lastError : null,
    lastItemCount: typeof r.lastItemCount === 'number' ? r.lastItemCount : null,
    lastFetchedAt: typeof r.lastFetchedAt === 'string' && r.lastFetchedAt ? r.lastFetchedAt : null,
    // 缺省视为遵守：后端默认 true，字段意外缺失时按更保守的一方兜底
    respectRobots: r.respectRobots !== false,
    // P1-18：三项都是"旁路信息"，缺字段一律按"没有正在发生的故障"兜底（0 / null），
    // 而不是猜一个失败性质——误标成 permanent 会让运营白跑一趟
    consecutiveFailures: Number.isFinite(Number(r.consecutiveFailures))
      ? Number(r.consecutiveFailures)
      : 0,
    failureKind:
      r.failureKind === 'permanent' || r.failureKind === 'transient' ? r.failureKind : null,
    nextRetryAt: typeof r.nextRetryAt === 'string' && r.nextRetryAt ? r.nextRetryAt : null,
    // P1-19：近期健康摘要。字段缺失时是 `null` 而不是全零——"没抓过"要能看出来
    health: toHealthBrief(r.health),
  };
}

function normalizeList(raw: unknown): RssSourceItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(toSource).filter((s): s is RssSourceItem => s !== null);
}

/**
 * GET /admin/sources —— 源列表（含抓取健康状态与**近期成功率**），按权重降序。
 *
 * 返回 `healthDays`：成功率必须带时间范围，否则"88%"这个数字无法解读。
 */
export async function fetchSources(): Promise<SourceListResult> {
  ensureRealBackend();
  const data = await request<{ list?: unknown; healthDays?: unknown }>('/admin/sources');
  const days = Number(data?.healthDays);
  return {
    list: normalizeList(data?.list),
    healthDays: Number.isFinite(days) && days > 0 ? days : 7,
  };
}

/**
 * GET /admin/sources/:key/health —— 单源健康趋势（P1-19）。
 *
 * 与列表页的分工：列表回答"哪个源值得点进来"，这里回答"**它到底怎么了**"。
 * 三段数据各有分工——总览看"抖动还是持续恶化"、按天序列看"是不是每天固定挂"、
 * 失败原文看"要不要改 URL / 换源"。
 */
export async function fetchSourceHealth(key: string, days?: number): Promise<SourceHealth> {
  ensureRealBackend();
  const query = days && days > 0 ? `?days=${Math.floor(days)}` : '';
  const data = await request<{
    key?: unknown;
    days?: unknown;
    keepDays?: unknown;
    summary?: unknown;
    points?: unknown;
    failures?: unknown;
  }>(`/admin/sources/${encodeURIComponent(key)}/health${query}`);

  const summary = (data?.summary ?? {}) as Record<string, unknown>;
  const ok = num(summary.ok) ?? 0;
  const attempts = num(summary.attempts) ?? 0;
  const rate = num(summary.rate);
  const brief: SourceHealthSummary = {
    attempts,
    ok,
    failed: num(summary.failed) ?? Math.max(0, attempts - ok),
    rate: rate === null ? null : Math.min(1, Math.max(0, rate)),
    items: num(summary.items) ?? 0,
    avgItems: num(summary.avgItems),
    lastOkAt: typeof summary.lastOkAt === 'string' && summary.lastOkAt ? summary.lastOkAt : null,
    lastFailAt:
      typeof summary.lastFailAt === 'string' && summary.lastFailAt ? summary.lastFailAt : null,
  };

  const rawPoints = Array.isArray(data?.points) ? data.points : [];
  const rawFailures = Array.isArray(data?.failures) ? data.failures : [];
  const win = Number(data?.days);
  const keep = Number(data?.keepDays);
  return {
    key: typeof data?.key === 'string' && data.key ? data.key : key,
    days: Number.isFinite(win) && win > 0 ? win : 30,
    keepDays: Number.isFinite(keep) && keep > 0 ? keep : 30,
    summary: brief,
    // 逐点/逐条丢弃坏行，而不是整段失效：一个日期串坏了不该让整个趋势图消失
    points: rawPoints.map(toHealthPoint).filter((p): p is SourceHealthPoint => p !== null),
    failures: rawFailures.map(toHealthFailure).filter((f): f is SourceHealthFailure => f !== null),
  };
}

/** POST /admin/sources —— 新增源（key 冲突 409，非法频道/地址 422/400） */
export async function createSource(input: SourceCreateInput): Promise<RssSourceItem> {
  ensureRealBackend();
  const data = await request<unknown>('/admin/sources', { method: 'POST', body: input });
  const source = toSource(data);
  if (!source) throw new Error('新增成功但响应无法解析');
  return source;
}

/** PATCH /admin/sources/:key —— 局部更新（未传字段保持原值） */
export async function updateSource(key: string, input: SourceInput): Promise<RssSourceItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/sources/${encodeURIComponent(key)}`, {
    method: 'PATCH',
    body: input,
  });
  const source = toSource(data);
  if (!source) throw new Error('更新成功但响应无法解析');
  return source;
}

/** DELETE /admin/sources/:key —— 删除源 */
export async function deleteSource(key: string): Promise<void> {
  ensureRealBackend();
  await request<unknown>(`/admin/sources/${encodeURIComponent(key)}`, { method: 'DELETE' });
}

/** POST /admin/sources/reset —— 重置为内置源，返回播种条数 */
export async function resetSources(): Promise<number> {
  ensureRealBackend();
  const data = await request<{ seeded?: number }>('/admin/sources/reset', { method: 'POST' });
  return typeof data?.seeded === 'number' ? data.seeded : 0;
}

function toAdminUser(raw: unknown): AdminUserItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const phone = typeof r.phone === 'string' ? r.phone : '';
  // 号码形态不对的行无法定位（删除按 phone），宁可丢弃也不要渲染成空壳
  if (!/^1\d{10}$/.test(phone)) return null;
  return {
    phone,
    note: typeof r.note === 'string' ? r.note : '',
    addedBy: typeof r.addedBy === 'string' ? r.addedBy : '',
    createdAt: typeof r.createdAt === 'string' ? r.createdAt : '',
    registered: r.registered === true,
    name: typeof r.name === 'string' ? r.name : '',
  };
}

function toAudit(raw: unknown): AdminAuditItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = Number(r.id);
  const action = typeof r.action === 'string' ? r.action : '';
  // 没有 id（游标锚点）或 action（标签依据）的行无法定位也不好展示，丢弃
  if (!Number.isFinite(id) || !action) return null;
  return {
    id,
    actorPhone: typeof r.actorPhone === 'string' ? r.actorPhone : '',
    action,
    target: typeof r.target === 'string' ? r.target : '',
    detail: r.detail && typeof r.detail === 'object' ? (r.detail as Record<string, unknown>) : {},
    createdAt: typeof r.createdAt === 'string' ? r.createdAt : '',
  };
}

/** GET /admin/users —— 管理员白名单（库内行 + env 来源计数） */
export async function fetchAdminUsers(): Promise<AdminUserList> {
  ensureRealBackend();
  const data = await request<{ list?: unknown; total?: number; envAdminCount?: number }>(
    '/admin/users',
  );
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toAdminUser).filter((a): a is AdminUserItem => a !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
    envAdminCount: typeof data?.envAdminCount === 'number' ? data.envAdminCount : 0,
  };
}

/** POST /admin/users —— 添加管理员（已在任一来源中 40903，形态非法 422） */
export async function addAdminUser(input: AdminUserInput): Promise<AdminUserItem> {
  ensureRealBackend();
  const data = await request<unknown>('/admin/users', { method: 'POST', body: input });
  const admin = toAdminUser(data);
  if (!admin) throw new Error('添加成功但响应无法解析');
  return admin;
}

/** DELETE /admin/users/:phone —— 移除管理员（末位保护 40904，不存在 404） */
export async function removeAdminUser(phone: string): Promise<void> {
  ensureRealBackend();
  await request<unknown>(`/admin/users/${encodeURIComponent(phone)}`, { method: 'DELETE' });
}

/** POST /admin/users/:phone/reset-password —— 管理员代重置密码（C4）。
 *
 * 需要二次校验（服务端 40304 → 全局密码对话框）。返回的 `tempPassword`
 * **只此一次**：服务端不存明文，丢了只能再重置一次。
 */
export async function resetUserPassword(phone: string): Promise<{
  phone: string;
  tempPassword: string;
  mustChangePassword: boolean;
}> {
  ensureRealBackend();
  return request<{ phone: string; tempPassword: string; mustChangePassword: boolean }>(
    `/admin/users/${encodeURIComponent(phone)}/reset-password`,
    { method: 'POST' },
  );
}

/**
 * GET /admin/audit —— 运营审计时间线（游标分页，新的在前）。
 *
 * `cursor` 由服务端下发（自增 id），客户端不解析——与评论/消息分页同款约定。
 */
export async function fetchAuditPage(
  options: {
    limit?: number;
    cursor?: string | null;
    target?: string;
  } = {},
): Promise<AdminAuditPage> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.limit) params.set('limit', String(options.limit));
  if (options.cursor) params.set('cursor', options.cursor);
  if (options.target) params.set('target', options.target);
  const qs = params.toString();
  const data = await request<{ list?: unknown; hasMore?: boolean; nextCursor?: string | null }>(
    `/admin/audit${qs ? `?${qs}` : ''}`,
  );
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toAudit).filter((a): a is AdminAuditItem => a !== null),
    hasMore: data?.hasMore === true,
    nextCursor: typeof data?.nextCursor === 'string' ? data.nextCursor : null,
  };
}

// ---------------------------------------------------------------- 资讯审核与下架（P1-20）

/**
 * 管理端资讯行归一化。
 *
 * `hidden` 缺省视为 false（后端默认可见）；但 `hiddenReason` 为空的已下架行
 * 照样渲染——那是历史数据/异常态，界面要能暴露出来而不是伪装成普通行。
 */
function toAdminNews(raw: unknown): AdminNewsItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = typeof r.id === 'string' ? r.id : '';
  if (!id) return null; // 没有 id 的行无法定位（下架/恢复/置顶都按 id 操作）
  return {
    id,
    title: typeof r.title === 'string' && r.title ? r.title : id,
    summary: typeof r.summary === 'string' && r.summary ? r.summary : null,
    source: typeof r.source === 'string' ? r.source : '',
    commentCount: Number.isFinite(Number(r.commentCount)) ? Number(r.commentCount) : 0,
    time: typeof r.time === 'string' ? r.time : '',
    isTop: r.isTop === true,
    channel: toChannel(r.channel),
    publishedAt: typeof r.publishedAt === 'string' && r.publishedAt ? r.publishedAt : null,
    hotScore: Number.isFinite(Number(r.hotScore)) ? Number(r.hotScore) : 0,
    hidden: r.hidden === true,
    hiddenAt: typeof r.hiddenAt === 'string' && r.hiddenAt ? r.hiddenAt : null,
    hiddenReason: typeof r.hiddenReason === 'string' && r.hiddenReason ? r.hiddenReason : null,
  };
}

/**
 * GET /admin/news —— 管理端资讯列表：筛选 + 搜索 + 游标分页（P1-20）。
 *
 * 默认 `status=all`（含已下架）：运营要能核验自己的下架操作。
 * `total` 是当前筛选的总数；`cursor` 由服务端下发，客户端原样回传。
 */
export async function fetchAdminNews(options: AdminNewsQuery = {}): Promise<AdminNewsListResult> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.status) params.set('status', options.status);
  if (options.keyword?.trim()) params.set('keyword', options.keyword.trim());
  if (options.channel) params.set('channel', options.channel);
  if (options.limit) params.set('limit', String(options.limit));
  if (options.cursor) params.set('cursor', options.cursor);
  const qs = params.toString();
  const data = await request<{
    list?: unknown;
    total?: number;
    hasMore?: boolean;
    nextCursor?: string | null;
  }>(`/admin/news${qs ? `?${qs}` : ''}`);
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toAdminNews).filter((n): n is AdminNewsItem => n !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
    hasMore: data?.hasMore === true,
    nextCursor: typeof data?.nextCursor === 'string' ? data.nextCursor : null,
  };
}

/** POST /admin/news/:id/hide —— 软下架（理由必填，空/纯空白 422；幂等） */
export async function hideNews(id: string, input: NewsHideInput): Promise<AdminNewsItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/news/${encodeURIComponent(id)}/hide`, {
    method: 'POST',
    body: input,
  });
  const item = toAdminNews(data);
  if (!item) throw new Error('下架成功但响应无法解析');
  return item;
}

/** POST /admin/news/:id/restore —— 恢复已下架资讯（幂等；公开查询实时生效） */
export async function restoreNews(id: string): Promise<AdminNewsItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/news/${encodeURIComponent(id)}/restore`, {
    method: 'POST',
  });
  const item = toAdminNews(data);
  if (!item) throw new Error('恢复成功但响应无法解析');
  return item;
}

/** PATCH /admin/news/:id —— 局部更新（当前仅置顶开关；字段级 diff 审计） */
export async function updateNewsTop(id: string, input: NewsTopInput): Promise<AdminNewsItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/news/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: input,
  });
  const item = toAdminNews(data);
  if (!item) throw new Error('更新成功但响应无法解析');
  return item;
}

// ---------------------------------------------------------------- 评论治理（P1-22）

/**
 * 管理端评论行归一化。
 *
 * 与 `toAdminNews` 同款思路：缺字段给保守缺省而不是丢弃整行——一条字段
 * 不全的评论照样要能展示与处置（运营认内容不认 id）。`hidden` 缺省视为
 * false（后端默认可见）。
 */
function toAdminComment(raw: unknown): AdminCommentItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = typeof r.id === 'string' ? r.id : '';
  if (!id) return null; // 没有 id 的行无法定位（隐藏/恢复都按 id 操作）
  return {
    id,
    newsId: typeof r.newsId === 'string' ? r.newsId : '',
    newsTitle: typeof r.newsTitle === 'string' && r.newsTitle ? r.newsTitle : '',
    userId: typeof r.userId === 'number' ? r.userId : null,
    userName: typeof r.userName === 'string' && r.userName ? r.userName : '匿名',
    content: typeof r.content === 'string' ? r.content : '',
    createdAt: typeof r.createdAt === 'string' ? r.createdAt : '',
    likes: Number.isFinite(Number(r.likes)) ? Number(r.likes) : 0,
    parentId: typeof r.parentId === 'string' ? r.parentId : null,
    rootId: typeof r.rootId === 'string' ? r.rootId : null,
    replyCount: Number.isFinite(Number(r.replyCount)) ? Number(r.replyCount) : 0,
    isDeleted: r.isDeleted === true,
    hidden: r.hidden === true,
    hiddenAt: typeof r.hiddenAt === 'string' && r.hiddenAt ? r.hiddenAt : null,
    hiddenReason: typeof r.hiddenReason === 'string' && r.hiddenReason ? r.hiddenReason : null,
    // P1-23：待复核（命中敏感词被脱敏、等人工看一眼）。缺省 false（后端默认）
    pendingReview: r.pendingReview === true,
    reviewedAt: typeof r.reviewedAt === 'string' && r.reviewedAt ? r.reviewedAt : null,
  };
}

/**
 * GET /admin/comments —— 管理端评论列表：筛选 + 搜索 + 游标分页（P1-22）。
 *
 * 默认 `status=all`（含已隐藏）：运营要能核验自己的处置。已软删除的评论
 * 一概不返回（后端口径）。游标为上一页末条 id，客户端原样回传。
 */
export async function fetchAdminComments(
  options: AdminCommentQuery = {},
): Promise<AdminCommentListResult> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.status) params.set('status', options.status);
  if (options.keyword?.trim()) params.set('keyword', options.keyword.trim());
  if (options.newsId) params.set('newsId', options.newsId);
  if (options.limit) params.set('limit', String(options.limit));
  if (options.cursor) params.set('cursor', options.cursor);
  const qs = params.toString();
  const data = await request<{
    list?: unknown;
    total?: number;
    hasMore?: boolean;
    nextCursor?: string | null;
  }>(`/admin/comments${qs ? `?${qs}` : ''}`);
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toAdminComment).filter((c): c is AdminCommentItem => c !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
    hasMore: data?.hasMore === true,
    nextCursor: typeof data?.nextCursor === 'string' ? data.nextCursor : null,
  };
}

/** POST /admin/comments/:id/hide —— 软隐藏（理由必填，空/纯空白 422；幂等） */
export async function hideComment(id: string, input: CommentHideInput): Promise<AdminCommentItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/comments/${encodeURIComponent(id)}/hide`, {
    method: 'POST',
    body: input,
  });
  const item = toAdminComment(data);
  if (!item) throw new Error('隐藏成功但响应无法解析');
  return item;
}

/** POST /admin/comments/:id/restore —— 恢复已隐藏评论（幂等；公开查询实时生效） */
export async function restoreComment(id: string): Promise<AdminCommentItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/comments/${encodeURIComponent(id)}/restore`, {
    method: 'POST',
  });
  const item = toAdminComment(data);
  if (!item) throw new Error('恢复成功但响应无法解析');
  return item;
}

/** POST /admin/comments/:id/review —— 通过人工复核（幂等；只清待复核标记） */
export async function reviewComment(id: string): Promise<AdminCommentItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/comments/${encodeURIComponent(id)}/review`, {
    method: 'POST',
  });
  const item = toAdminComment(data);
  if (!item) throw new Error('复核成功但响应无法解析');
  return item;
}

// ---------------------------------------------------------------- 敏感词库（P1-23）

const WORD_ACTIONS: WordAction[] = ['block', 'mask', 'allow'];

function toWordAction(raw: unknown): WordAction {
  // 未知动作一律按 mask 兜底：数据异常时降级为"先脱敏留给人看"，
  // 而不是把用户的发言直接吞掉（与后端 MODERATION_DEFAULT_ACTION 同口径）
  return WORD_ACTIONS.includes(raw as WordAction) ? (raw as WordAction) : 'mask';
}

function toWord(raw: unknown): SensitiveWordItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = Number(r.id);
  const word = typeof r.word === 'string' ? r.word : '';
  // 没有 id（更新/删除的锚点）或词本身的行无法操作，丢弃而不是渲染成空壳
  if (!Number.isFinite(id) || !word) return null;
  return {
    id,
    word,
    action: toWordAction(r.action),
    category: typeof r.category === 'string' ? r.category : '',
    enabled: r.enabled !== false,
    hitCount: Number.isFinite(Number(r.hitCount)) ? Number(r.hitCount) : 0,
    createdBy: typeof r.createdBy === 'string' ? r.createdBy : '',
    createdAt: typeof r.createdAt === 'string' ? r.createdAt : null,
    updatedAt: typeof r.updatedAt === 'string' ? r.updatedAt : null,
  };
}

function toHit(raw: unknown): SensitiveHitItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = Number(r.id);
  const word = typeof r.word === 'string' ? r.word : '';
  if (!Number.isFinite(id) || !word) return null;
  return {
    id,
    word,
    action: r.action === 'block' ? 'block' : 'mask',
    field: r.field === 'name' ? 'name' : 'content',
    commentId: typeof r.commentId === 'string' ? r.commentId : null,
    newsId: typeof r.newsId === 'string' ? r.newsId : null,
    userId: typeof r.userId === 'number' ? r.userId : null,
    userName: typeof r.userName === 'string' ? r.userName : '',
    // 原文片段是命中日志的唯一价值：脱敏后的正文里已经找不回来
    snippet: typeof r.snippet === 'string' ? r.snippet : '',
    createdAt: typeof r.createdAt === 'string' ? r.createdAt : '',
  };
}

/**
 * GET /admin/words —— 敏感词库列表（P1-23）。
 *
 * 按命中次数降序：运营最先该看见的是"高频词"——它要么是热点，要么是误杀大户。
 */
export async function fetchWords(options: WordQuery = {}): Promise<WordListResult> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.keyword?.trim()) params.set('keyword', options.keyword.trim());
  if (options.action) params.set('action', options.action);
  if (options.enabled !== undefined) params.set('enabled', String(options.enabled));
  if (options.limit) params.set('limit', String(options.limit));
  if (options.offset) params.set('offset', String(options.offset));
  const qs = params.toString();
  const data = await request<{ list?: unknown; total?: number }>(
    `/admin/words${qs ? `?${qs}` : ''}`,
  );
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toWord).filter((w): w is SensitiveWordItem => w !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
  };
}

/** POST /admin/words —— 新增敏感词（太短 42201，重复 40905） */
export async function createWord(input: WordInput): Promise<SensitiveWordItem> {
  ensureRealBackend();
  const data = await request<unknown>('/admin/words', { method: 'POST', body: input });
  const word = toWord(data);
  if (!word) throw new Error('新增成功但响应无法解析');
  return word;
}

/** PATCH /admin/words/:id —— 局部更新（动作 / 分组 / 启用；不传即不改） */
export async function updateWord(id: number, input: WordUpdateInput): Promise<SensitiveWordItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/words/${id}`, { method: 'PATCH', body: input });
  const word = toWord(data);
  if (!word) throw new Error('更新成功但响应无法解析');
  return word;
}

/** DELETE /admin/words/:id —— 删除敏感词（停用通常比删除更合适） */
export async function deleteWord(id: number): Promise<void> {
  ensureRealBackend();
  await request<unknown>(`/admin/words/${id}`, { method: 'DELETE' });
}

/** POST /admin/words/reload —— 强制重建本进程词库索引（集群下手动同步） */
export async function reloadWords(): Promise<number> {
  ensureRealBackend();
  const data = await request<{ count?: number }>('/admin/words/reload', { method: 'POST' });
  return typeof data?.count === 'number' ? data.count : 0;
}

/**
 * GET /admin/moderation/hits —— 敏感词命中日志（P1-23，只读）。
 *
 * 它的用途不是"看出事了"，而是**回答误杀**：谁、哪句话、命中哪个词、上下文
 * 是什么。游标为自增 id，客户端原样回传。
 */
export async function fetchModerationHits(options: HitQuery = {}): Promise<HitListResult> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.word?.trim()) params.set('word', options.word.trim());
  if (options.action) params.set('action', options.action);
  if (options.field) params.set('field', options.field);
  if (options.limit) params.set('limit', String(options.limit));
  if (options.cursor) params.set('cursor', String(options.cursor));
  const qs = params.toString();
  const data = await request<{
    list?: unknown;
    total?: number;
    hasMore?: boolean;
    nextCursor?: number | null;
  }>(`/admin/moderation/hits${qs ? `?${qs}` : ''}`);
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toHit).filter((h): h is SensitiveHitItem => h !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
    hasMore: data?.hasMore === true,
    nextCursor: typeof data?.nextCursor === 'number' ? data.nextCursor : null,
  };
}

// ---------------------------------------------------------------- 昵称敏感词（P1-24）

const NICKNAME_ACTIONS: NicknameWordAction[] = ['block', 'review', 'allow'];
/** 每条词最多几条别名、单条最长多少字符（与后端 MAX_ALIASES_PER_WORD / MAX_ALIAS_LENGTH 一致） */
const MAX_ALIASES = 20;
const MAX_ALIAS_LENGTH = 50;

/**
 * 昵称动作归一化：未知动作一律按 `review` 兜底——与后端 FALLBACK_ACTION
 * 同口径。数据异常时降级为"放行 + 让人看一眼"，而不是把用户的名字直接吞掉。
 */
function toNicknameAction(raw: unknown): NicknameWordAction {
  return NICKNAME_ACTIONS.includes(raw as NicknameWordAction)
    ? (raw as NicknameWordAction)
    : 'review';
}

/** 命中动作：库里只会记 block / review（allow 不记日志），未知值按 review 兜底 */
function toNicknameHitAction(raw: unknown): 'block' | 'review' {
  return raw === 'block' ? 'block' : 'review';
}

function toNicknameSource(raw: unknown): 'register' | 'update' {
  return raw === 'register' ? 'register' : 'update';
}

/**
 * 别名列表归一化：脏数据（非数组 / 非字符串项 / 超长 / 重复）逐个剔除——
 * 与后端 `alias_list` 同规则。前端永远拿到干净数组，模板不做二次防御。
 */
function toAliases(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const a of raw) {
    if (typeof a !== 'string') continue;
    const t = a.trim();
    if (!t || t.length > MAX_ALIAS_LENGTH || out.includes(t)) continue;
    out.push(t);
    if (out.length >= MAX_ALIASES) break;
  }
  return out;
}

function toNicknameWord(raw: unknown): NicknameWordItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = Number(r.id);
  const word = typeof r.word === 'string' ? r.word : '';
  // 没有 id（更新/删除的锚点）或词本身的行无法操作，丢弃而不是渲染成空壳
  if (!Number.isFinite(id) || !word) return null;
  return {
    id,
    word,
    action: toNicknameAction(r.action),
    category: typeof r.category === 'string' ? r.category : '',
    aliases: toAliases(r.aliases),
    enabled: r.enabled !== false,
    hitCount: Number.isFinite(Number(r.hitCount)) ? Number(r.hitCount) : 0,
    createdBy: typeof r.createdBy === 'string' ? r.createdBy : '',
    createdAt: typeof r.createdAt === 'string' && r.createdAt ? r.createdAt : null,
    updatedAt: typeof r.updatedAt === 'string' && r.updatedAt ? r.updatedAt : null,
  };
}

function toNicknameHit(raw: unknown): NicknameHitItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = Number(r.id);
  const word = typeof r.word === 'string' ? r.word : '';
  if (!Number.isFinite(id) || !word) return null;
  return {
    id,
    word,
    // matched 缺失时回落主词：老数据/异常行也要能显示"拦它的是什么"
    matched: typeof r.matched === 'string' && r.matched ? r.matched : word,
    action: toNicknameHitAction(r.action),
    source: toNicknameSource(r.source),
    userId: typeof r.userId === 'number' ? r.userId : null,
    userName: typeof r.userName === 'string' ? r.userName : '',
    snippet: typeof r.snippet === 'string' ? r.snippet : '',
    createdAt: typeof r.createdAt === 'string' ? r.createdAt : '',
  };
}

function toNicknameReview(raw: unknown): NicknameReviewItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const userId = Number(r.userId);
  // 没有 userId 的行无法定位（复核按 user_id 操作），丢弃
  if (!Number.isFinite(userId)) return null;
  return {
    userId,
    name: typeof r.name === 'string' ? r.name : '',
    phone: typeof r.phone === 'string' ? r.phone : '',
    flaggedAt: typeof r.flaggedAt === 'string' && r.flaggedAt ? r.flaggedAt : null,
    flaggedName: typeof r.flaggedName === 'string' && r.flaggedName ? r.flaggedName : null,
    flaggedMatched:
      typeof r.flaggedMatched === 'string' && r.flaggedMatched ? r.flaggedMatched : null,
  };
}

/**
 * GET /admin/nickname-words —— 昵称敏感词库列表（P1-24）。
 *
 * 与 P1-23 词库同构：按命中次数降序 + `limit/offset`（运营要一遍扫完 + 跳页核对）。
 * `keyword` 只匹配主词——别名在每行的 `aliases` 字段里可见。
 */
export async function fetchNicknameWords(
  options: NicknameWordQuery = {},
): Promise<NicknameWordListResult> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.keyword?.trim()) params.set('keyword', options.keyword.trim());
  if (options.action) params.set('action', options.action);
  if (options.enabled !== undefined) params.set('enabled', String(options.enabled));
  if (options.limit) params.set('limit', String(options.limit));
  if (options.offset) params.set('offset', String(options.offset));
  const qs = params.toString();
  const data = await request<{ list?: unknown; total?: number }>(
    `/admin/nickname-words${qs ? `?${qs}` : ''}`,
  );
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toNicknameWord).filter((w): w is NicknameWordItem => w !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
  };
}

/** POST /admin/nickname-words —— 新增（主词/别名太短 42204，重复 40906；保存即生效） */
export async function createNicknameWord(input: NicknameWordInput): Promise<NicknameWordItem> {
  ensureRealBackend();
  const data = await request<unknown>('/admin/nickname-words', { method: 'POST', body: input });
  const word = toNicknameWord(data);
  if (!word) throw new Error('新增成功但响应无法解析');
  return word;
}

/** PATCH /admin/nickname-words/:id —— 局部更新（动作/分组/别名/启用；不传即不改） */
export async function updateNicknameWord(
  id: number,
  input: NicknameWordUpdateInput,
): Promise<NicknameWordItem> {
  ensureRealBackend();
  const data = await request<unknown>(`/admin/nickname-words/${id}`, {
    method: 'PATCH',
    body: input,
  });
  const word = toNicknameWord(data);
  if (!word) throw new Error('更新成功但响应无法解析');
  return word;
}

/** DELETE /admin/nickname-words/:id —— 删除昵称敏感词（不存在 40407） */
export async function deleteNicknameWord(id: number): Promise<void> {
  ensureRealBackend();
  await request<unknown>(`/admin/nickname-words/${id}`, { method: 'DELETE' });
}

/** POST /admin/nickname-words/reload —— 强制重建本进程昵称词库索引，返回生效条数 */
export async function reloadNicknameWords(): Promise<number> {
  ensureRealBackend();
  const data = await request<{ count?: number }>('/admin/nickname-words/reload', {
    method: 'POST',
  });
  return typeof data?.count === 'number' ? data.count : 0;
}

/**
 * GET /admin/nickname-hits —— 昵称命中日志（P1-24，只读）。
 *
 * 价值不在「看出事了」，而在**回答误杀与受理申诉**：哪个人、哪个昵称、
 * 命中哪个词（含哪条别名）、从注册还是改名入口来的。游标为自增 id。
 */
export async function fetchNicknameHits(
  options: NicknameHitQuery = {},
): Promise<NicknameHitListResult> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.word?.trim()) params.set('word', options.word.trim());
  if (options.action) params.set('action', options.action);
  if (options.source) params.set('source', options.source);
  if (options.userId !== undefined) params.set('userId', String(options.userId));
  if (options.limit) params.set('limit', String(options.limit));
  if (options.cursor !== undefined && options.cursor !== null) {
    params.set('cursor', String(options.cursor));
  }
  const qs = params.toString();
  const data = await request<{
    list?: unknown;
    total?: number;
    hasMore?: boolean;
    nextCursor?: number | null;
  }>(`/admin/nickname-hits${qs ? `?${qs}` : ''}`);
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toNicknameHit).filter((h): h is NicknameHitItem => h !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
    hasMore: data?.hasMore === true,
    nextCursor: typeof data?.nextCursor === 'number' ? data.nextCursor : null,
  };
}

/**
 * GET /admin/nickname-reviews —— 昵称待人工复核队列（P1-24）。
 *
 * 与评论复核同构，但对象是账号属性：通过只清标志，**不改昵称本身**
 * （名字是用户自己起的，复核是「人看过了」这个事实）。
 */
export async function fetchNicknameReviews(
  options: NicknameReviewQuery = {},
): Promise<NicknameReviewListResult> {
  ensureRealBackend();
  const params = new URLSearchParams();
  if (options.keyword?.trim()) params.set('keyword', options.keyword.trim());
  if (options.limit) params.set('limit', String(options.limit));
  if (options.offset) params.set('offset', String(options.offset));
  const qs = params.toString();
  const data = await request<{ list?: unknown; total?: number }>(
    `/admin/nickname-reviews${qs ? `?${qs}` : ''}`,
  );
  const rawList = Array.isArray(data?.list) ? data.list : [];
  return {
    list: rawList.map(toNicknameReview).filter((r): r is NicknameReviewItem => r !== null),
    total: typeof data?.total === 'number' ? data.total : 0,
  };
}

/** POST /admin/nickname-reviews/:user_id —— 通过复核（幂等；用户不存在 40408） */
export async function reviewNickname(userId: number): Promise<NicknameReviewResult> {
  ensureRealBackend();
  const data = await request<Record<string, unknown>>(
    `/admin/nickname-reviews/${encodeURIComponent(String(userId))}`,
    { method: 'POST' },
  );
  const id = Number(data?.userId);
  return {
    userId: Number.isFinite(id) ? id : userId,
    name: typeof data?.name === 'string' ? data.name : '',
    phone: typeof data?.phone === 'string' ? data.phone : '',
    pendingReview: data?.pendingReview === true,
    reviewedAt: typeof data?.reviewedAt === 'string' && data.reviewedAt ? data.reviewedAt : null,
  };
}
