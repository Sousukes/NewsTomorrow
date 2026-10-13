/**
 * 管理端 RSS 源接口（后端 P1-9，界面 P1-10）。
 *
 * 与 `api/me.ts` 的关键差别：**没有 mock 短路**。源配置只存在于后端数据库中，
 * mock 模式下并无真实对象可操作——伪造一份"能增能删"的假列表只会误导排障。
 * 入口由 `user.isAdmin`（登录响应下发）控制，mock 模式下该标志恒为 false，
 * 因此正常情况下这些函数不会被调到；直接手敲网址进入时会在页面上看到明确提示。
 */
import { USE_MOCK } from './config';
import { request } from './http';
import { toChannel } from '@/constants/channels';
import type { RssSourceItem, SourceCreateInput, SourceInput } from '@/types';

/** 当前构建是否具备源管理能力（mock 模式未连接后端，恒为 false） */
export const ADMIN_SOURCES_SUPPORTED = !USE_MOCK;

function ensureRealBackend(): void {
  if (USE_MOCK) {
    throw new Error('当前为 mock 模式（未连接后端），RSS 源管理不可用');
  }
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
  };
}

function normalizeList(raw: unknown): RssSourceItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(toSource).filter((s): s is RssSourceItem => s !== null);
}

/** GET /admin/sources —— 源列表（含抓取健康状态），按权重降序 */
export async function fetchSources(): Promise<RssSourceItem[]> {
  ensureRealBackend();
  const data = await request<{ list?: unknown }>('/admin/sources');
  return normalizeList(data?.list);
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
