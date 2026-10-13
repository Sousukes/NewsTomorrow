/**
 * 管理端接口：RSS 源配置（后端 P1-9，界面 P1-10）+ 管理员白名单（P1-14）。
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
  AdminUserInput,
  AdminUserItem,
  AdminUserList,
  RssSourceItem,
  SourceCreateInput,
  SourceInput,
} from '@/types';

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
