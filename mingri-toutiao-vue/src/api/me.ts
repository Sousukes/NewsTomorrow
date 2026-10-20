/** 收藏/历史/昵称接口。服务端是唯一事实源：写操作返回完整集合，客户端不推导增量。 */
import { USE_MOCK } from './config';
import { mockDelay, request } from './http';
import { normalizeNewsItem } from './news';
import type { NewsItem, NicknameUpdateResult } from '@/types';

/** 标记集合载荷 */
export interface MarksPayload {
  ids: string[];
  list: NewsItem[];
}

/** mock 模式下无后端，store 据此跳过同步请求。 */
export const MARKS_SYNC_SUPPORTED = !USE_MOCK;

const EMPTY: MarksPayload = { ids: [], list: [] };

function normalizePayload(raw: unknown): MarksPayload {
  const r = (raw ?? {}) as { ids?: unknown; list?: unknown };
  return {
    ids: Array.isArray(r.ids) ? r.ids.map(String) : [],
    list: Array.isArray(r.list) ? (r.list as Record<string, unknown>[]).map(normalizeNewsItem) : [],
  };
}

async function call(
  path: string,
  options: { method?: 'GET' | 'POST' | 'PUT' | 'DELETE'; body?: unknown } = {},
): Promise<MarksPayload> {
  if (USE_MOCK) return mockDelay({ ...EMPTY, list: [] });
  return normalizePayload(await request<unknown>(path, options));
}

// ------------------------------ 收藏 ------------------------------

/** GET /me/favorites */
export function fetchFavorites(): Promise<MarksPayload> {
  return call('/me/favorites');
}

/** POST /me/favorites —— 收藏（幂等） */
export function addFavorite(newsId: string): Promise<MarksPayload> {
  return call('/me/favorites', { method: 'POST', body: { newsId } });
}

/** DELETE /me/favorites/:id —— 取消收藏（幂等） */
export function removeFavorite(newsId: string): Promise<MarksPayload> {
  return call(`/me/favorites/${encodeURIComponent(newsId)}`, { method: 'DELETE' });
}

/** PUT /me/favorites —— 并集合并（把本地收藏并入云端，只增不删） */
export function mergeFavorites(ids: string[]): Promise<MarksPayload> {
  return call('/me/favorites', { method: 'PUT', body: { ids } });
}

// ------------------------------ 浏览历史 ------------------------------

/** GET /me/history */
export function fetchHistory(): Promise<MarksPayload> {
  return call('/me/history');
}

/** POST /me/history —— 记录浏览（同一资讯只刷新时间） */
export function recordHistory(newsId: string): Promise<MarksPayload> {
  return call('/me/history', { method: 'POST', body: { newsId } });
}

/** DELETE /me/history —— 清空 */
export function clearHistoryRemote(): Promise<MarksPayload> {
  return call('/me/history', { method: 'DELETE' });
}

/** PUT /me/history —— 并集合并（把本地历史并入云端，只增不删） */
export function mergeHistory(ids: string[]): Promise<MarksPayload> {
  return call('/me/history', { method: 'PUT', body: { ids } });
}

// ------------------------------ 修改昵称（P1-24） ------------------------------

/** 昵称长度边界（与后端 NICKNAME_MIN_LENGTH / NICKNAME_MAX_LENGTH 一致） */
const NICKNAME_MIN_LENGTH = 2;
const NICKNAME_MAX_LENGTH = 24;
/** 字符白名单外的符号（后端 NICKNAME_ALLOWED_SYMBOLS = `_-·.・`，另加内部空格） */
const NICKNAME_SYMBOL_RE = /^[\w\u00b7\u2022\u30fb\-. ]*$/u;
/** 离线 mock 的迷你词库：与 mock-server 种子同口径（接真实后端前演示三级分流） */
const MOCK_BLOCK_WORDS = ['博彩', '加微信'];
const MOCK_REVIEW_WORDS = ['广告'];

/** 与后端 validate_nickname_shape 同规则，仅服务 VITE_USE_MOCK 演示路径。 */
function mockShapeError(clean: string): string {
  if (!NICKNAME_SYMBOL_RE.test(clean)) {
    return '昵称仅支持中英文、数字及 _ - · . ・ 等符号';
  }
  if (clean.length < NICKNAME_MIN_LENGTH) return `昵称至少需要 ${NICKNAME_MIN_LENGTH} 个字符`;
  if (clean.length > NICKNAME_MAX_LENGTH) return `昵称不能超过 ${NICKNAME_MAX_LENGTH} 个字符`;
  if (!/[\p{L}\p{N}]/u.test(clean)) return '昵称需至少包含一个字母、数字或汉字';
  return '';
}

/**
 * 改昵称：形状不合法 42203；命中 block 词 40006（不回显命中词）；review 词放行待复核。
 */
export async function updateNickname(name: string): Promise<NicknameUpdateResult> {
  const clean = name.trim();
  if (USE_MOCK) {
    await mockDelay(null, 200);
    const shapeError = mockShapeError(clean);
    if (shapeError) throw new Error(shapeError);
    const folded = clean.normalize('NFKC').toLowerCase();
    const blocked = MOCK_BLOCK_WORDS.find((w) => folded.includes(w));
    if (blocked) throw new Error('昵称包含违规内容，请更换后重试');
    const review = MOCK_REVIEW_WORDS.find((w) => folded.includes(w));
    return {
      name: clean,
      result: review ? 'review' : 'pass',
      reason: review ? '包含广告内容，需人工复核' : '',
      pendingReview: !!review,
    };
  }
  const data = await request<{
    name?: unknown;
    result?: unknown;
    reason?: unknown;
    pendingReview?: unknown;
  }>('/me/nickname', { method: 'PUT', body: { name: clean } });
  return {
    name: typeof data?.name === 'string' ? data.name : clean,
    result: data?.result === 'review' ? 'review' : 'pass',
    reason: typeof data?.reason === 'string' ? data.reason : '',
    pendingReview: data?.pendingReview === true,
  };
}
