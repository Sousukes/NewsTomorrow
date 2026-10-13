/**
 * 个人标记接口：收藏 / 浏览历史（云端同步）。
 *
 * 服务端为唯一事实源：所有写操作都返回**完整的最新集合** `{ ids, list }`，
 * 客户端拿到即为真相，无需自行推导增量。
 */
import { USE_MOCK } from './config';
import { mockDelay, request } from './http';
import { normalizeNewsItem } from './news';
import type { NewsItem } from '@/types';

/** 标记集合载荷 */
export interface MarksPayload {
  ids: string[];
  list: NewsItem[];
}

/**
 * 当前构建是否支持云同步。
 * mock 模式下没有后端，标记仅存 localStorage；置为 false 可让 store 跳过同步请求。
 */
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
