import { mockNews } from '@/data/mockNews';
import { USE_MOCK } from './config';
import { mockDelay, request } from './http';
import { mockSourceKey } from '@/utils/source';

/**
 * 关注来源（E6b）API 层。
 *
 * 两条数据路径：
 * - **真后端 / mock 后端联调**：`GET /api/sources`、`GET|POST|DELETE /api/me/follows`；
 * - **内置 mock（离线演示，不起后端）**：目录由种子资讯去重合成，关注关系存 localStorage。
 *
 * 离线分支不是"假数据"而是**同一套契约的本地实现**：接口签名、返回形状
 * （`{ keys, list }`）与真后端一致，store 层因此不需要判断当前是哪种模式。
 */

/** 目录项：只含展示与决策所需字段（后端同口径，不下发 url / 健康状态） */
export interface SourceItem {
  key: string;
  name: string;
  channel: string;
  /** 是否启用：停用源的关注流必然是空的，界面要能解释 */
  enabled: boolean;
  /** 该源当前可见资讯数（不含已下架） */
  newsCount: number;
}

/** 关注集合载荷：与 `/me/favorites` 同形（`ids` → `keys`，因为主键是源 key） */
export interface FollowsPayload {
  keys: string[];
  list: SourceItem[];
}

/** 关注上限（与后端 `me.FOLLOW_MAX` 对齐） */
const FOLLOW_MAX = 200;

/* ---------- 离线（内置 mock）分支 ---------- */

const FOLLOWS_LS_KEY = 'mingri-toutiao:follows';

function readLocal(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(FOLLOWS_LS_KEY) ?? '[]');
    return Array.isArray(raw) ? raw.map(String) : [];
  } catch {
    return [];
  }
}

function writeLocal(keys: string[]): void {
  try {
    if (keys.length) localStorage.setItem(FOLLOWS_LS_KEY, JSON.stringify(keys));
    else localStorage.removeItem(FOLLOWS_LS_KEY);
  } catch {
    /* 隐私模式 / 容量已满：静默失败（离线演示不因缓存报错中断） */
  }
}

/** 本地已关注的 key 集合。`api/news.ts` 的 mock 关注流用它过滤——跨模块同源，避免两份真相 */
export function mockFollowedKeys(): string[] {
  return USE_MOCK ? readLocal() : [];
}

/** 离线来源目录：由种子资讯按名称去重合成，key 由名称哈希派生（确定不变）
 *
 *  `newsCount` 不剔除已下架项：内置 mock 的种子数据不含 `hidden` 字段，
 *  剔了也永远筛不掉任何东西——不如如实计数，并在注释里说明边界。
 */
export function mockSourceDirectory(): SourceItem[] {
  const map = new Map<string, { key: string; name: string; channel: string; count: number }>();
  for (const n of mockNews) {
    const key = mockSourceKey(n.source);
    const hit = map.get(key);
    if (hit) {
      hit.count += 1;
      continue;
    }
    map.set(key, { key, name: n.source, channel: n.channel, count: 1 });
  }
  return [...map.values()].map((s) => ({
    key: s.key,
    name: s.name,
    channel: s.channel,
    enabled: true,
    newsCount: s.count,
  }));
}

function localPayload(keys: string[] = readLocal()): FollowsPayload {
  const dir = mockSourceDirectory();
  const alive = keys.filter((k) => dir.some((s) => s.key === k));
  return { keys: alive, list: alive.map((k) => dir.find((s) => s.key === k)!) };
}

/* ---------- 对外接口 ---------- */

/** 来源目录（公开；真后端 `/api/sources`） */
export async function fetchSourceDirectory(channel?: string): Promise<SourceItem[]> {
  if (USE_MOCK) {
    const list = mockSourceDirectory();
    return mockDelay(channel ? list.filter((s) => s.channel === channel) : list);
  }
  const data = await request<{ list?: unknown[] }>('/sources', { query: { channel } });
  const arr = Array.isArray(data) ? data : (data?.list ?? []);
  return (arr as Record<string, unknown>[]).map((raw) => ({
    key: String(raw.key ?? ''),
    name: String(raw.name ?? ''),
    channel: String(raw.channel ?? ''),
    enabled: raw.enabled === undefined ? true : Boolean(raw.enabled),
    newsCount: Number(raw.newsCount ?? 0),
  }));
}

/** 我的关注（返回完整集合，服务端即事实源） */
export async function fetchFollows(): Promise<FollowsPayload> {
  if (USE_MOCK) return mockDelay(localPayload());
  // 统一走 normalizePayload：后端字段缺失/形状异常时归一到安全默认值，
  // 不在这里逐条手写映射（两处实现必然漂移）
  return normalizePayload(await request<FollowsPayload>('/me/follows'));
}

/** 关注一个来源（幂等）。返回更新后的完整集合。 */
export async function followSource(key: string): Promise<FollowsPayload> {
  if (!key.trim()) throw new Error('来源标识不能为空');
  if (USE_MOCK) {
    if (!mockSourceDirectory().some((s) => s.key === key && s.enabled)) {
      throw new Error('来源不存在或已停用');
    }
    const keys = readLocal();
    if (!keys.includes(key)) {
      if (keys.length >= FOLLOW_MAX) throw new Error(`最多关注 ${FOLLOW_MAX} 个来源`);
      keys.unshift(key);
      writeLocal(keys);
    }
    return mockDelay(localPayload(keys));
  }
  const data = await request<FollowsPayload>('/me/follows', {
    method: 'POST',
    body: { sourceKey: key },
  });
  return normalizePayload(data);
} /** 取消关注（幂等）。返回更新后的完整集合。 */
export async function unfollowSource(key: string): Promise<FollowsPayload> {
  if (USE_MOCK) {
    writeLocal(readLocal().filter((k) => k !== key));
    return mockDelay(localPayload());
  }
  const data = await request<FollowsPayload>(`/me/follows/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  });
  return normalizePayload(data);
}

function normalizeSource(raw: Record<string, unknown>): SourceItem {
  return {
    key: String(raw.key ?? ''),
    name: String(raw.name ?? ''),
    channel: String(raw.channel ?? ''),
    enabled: raw.enabled === undefined ? true : Boolean(raw.enabled),
    newsCount: Number(raw.newsCount ?? 0),
  };
}

function normalizePayload(data: Partial<FollowsPayload> | null | undefined): FollowsPayload {
  return {
    keys: Array.isArray(data?.keys) ? data!.keys.map(String) : [],
    list: (Array.isArray(data?.list) ? data!.list : []).map((raw) =>
      normalizeSource(raw as unknown as Record<string, unknown>),
    ),
  };
}
