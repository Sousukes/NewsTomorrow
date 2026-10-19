import { USE_MOCK } from './config';
import { mockDelay, request } from './http';
import { normalizeNewsItem } from './news';
import type { NewsItem } from '@/types';

/**
 * 负反馈与曝光（E3）API 层。
 *
 * 两条数据路径（与 `api/follows.ts` 同一处方）：
 * - **真后端 / mock 后端联调**：`/me/exposures`、`/me/dislikes`；
 * - **内置 mock（离线演示，不起后端）**：同一套形状的 localStorage 实现。
 *
 * 三条刻意设计：
 * 1. **曝光只上报 id 数组**，不回读——回读会让"看一眼就发两个请求"，
 *    而曝光是最高频的写路径（一屏 20 条）。
 * 2. **空批直接返回，不发请求**：把"用户划过什么都没看到"变成一次 422
 *    是自找麻烦，也是无谓的往返。
 * 3. **负反馈与举报严格分离**：举报走 `api/reports.ts`。把"我不爱看"和
 *    "这内容违规"混成一个字段，运营侧就无法区分两者。
 */

/** 曝光上报的单次上限（与后端 `EXPOSURE_BATCH_MAX` 对齐） */
const EXPOSURE_MAX = 50;

export interface ExposureReport {
  /** 服务端接受条数（去重前） */
  accepted: number;
  /** 实际新增条数（去重后） */
  written: number;
}

export interface DislikeSource {
  key: string;
  name: string;
  channel: string;
}

export interface DislikePayload {
  newsIds: string[];
  /** 已踩的单条资讯（供「已减少的推荐」列表展示） */
  list: NewsItem[];
  sourceKeys: string[];
  sources: DislikeSource[];
}

/** 负反馈目标：二选一，由调用方保证（两者都给/都不给 → 后端 42207） */
export type DislikeTarget = { newsId: string } | { sourceKey: string };

export type DislikeKind = 'news' | 'sources';

/* ---------- 离线（内置 mock）分支 ---------- */

const LS_KEY = 'mingri-toutiao:feedback';

interface LocalShape {
  exposures: Record<string, number>;
  dislikes: { news: string[]; sources: string[] };
}

function readLocal(): LocalShape {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_KEY) ?? '{}') as Partial<LocalShape>;
    const d = (raw.dislikes ?? {}) as Partial<LocalShape['dislikes']>;
    return {
      exposures: raw.exposures && typeof raw.exposures === 'object' ? raw.exposures : {},
      dislikes: {
        news: Array.isArray(d.news) ? d.news.map(String) : [],
        sources: Array.isArray(d.sources) ? d.sources.map(String) : [],
      },
    };
  } catch {
    return { exposures: {}, dislikes: { news: [], sources: [] } };
  }
}

function writeLocal(next: LocalShape): void {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(next));
  } catch {
    /* 隐私模式 / 容量已满：静默失败（曝光丢失的代价只是"这条可能重复出现一次"） */
  }
}

/* ---------- 对外接口 ---------- */

/** 上报一批曝光（幂等：重复上报只刷新时间）。空数组不发请求。 */
export async function reportExposures(ids: string[]): Promise<ExposureReport> {
  const list = (ids ?? []).map(String).filter(Boolean);
  if (!list.length) return { accepted: 0, written: 0 };

  if (USE_MOCK) {
    const local = readLocal();
    let written = 0;
    for (const id of list) {
      if (!(id in local.exposures)) written += 1;
      local.exposures[id] = Date.now();
    }
    writeLocal(local);
    return mockDelay({ accepted: list.length, written });
  }

  // 超过单次上限时分片：服务端会 422，而丢掉后半段的代价是"那几条永远不计曝光"
  let accepted = 0;
  let written = 0;
  for (let i = 0; i < list.length; i += EXPOSURE_MAX) {
    const chunk = list.slice(i, i + EXPOSURE_MAX);
    const data = await request<ExposureReport>('/me/exposures', {
      method: 'POST',
      body: { ids: chunk },
    });
    accepted += Number(data?.accepted ?? 0);
    written += Number(data?.written ?? 0);
  }
  return { accepted, written };
}

/** 我的负反馈集合（单条 + 来源两档） */
export async function fetchDislikes(): Promise<DislikePayload> {
  if (USE_MOCK) {
    const local = readLocal();
    return mockDelay({
      newsIds: local.dislikes.news,
      list: [],
      sourceKeys: local.dislikes.sources,
      sources: [],
    });
  }
  const data = await request<Record<string, unknown>>('/me/dislikes');
  return normalizeDislikes(data);
}

/** 踩一下（幂等）。返回更新后的完整集合。 */
export async function addDislike(target: DislikeTarget): Promise<DislikePayload> {
  if (USE_MOCK) {
    const local = readLocal();
    if ('newsId' in target) {
      const id = String(target.newsId ?? '').trim();
      if (!id) throw new Error('内容不存在');
      if (!local.dislikes.news.includes(id)) local.dislikes.news.push(id);
    } else {
      const key = String(target.sourceKey ?? '').trim();
      if (!key) throw new Error('来源不存在或已停用');
      if (!local.dislikes.sources.includes(key)) local.dislikes.sources.push(key);
    }
    writeLocal(local);
    return mockDelay({
      newsIds: local.dislikes.news,
      list: [],
      sourceKeys: local.dislikes.sources,
      sources: [],
    });
  }
  const data = await request<Record<string, unknown>>('/me/dislikes', {
    method: 'POST',
    body: 'newsId' in target ? { newsId: target.newsId } : { sourceKey: target.sourceKey },
  });
  return normalizeDislikes(data);
}

/** 取消负反馈（幂等）。`kind` 决定删的是单条还是来源档。 */
export async function removeDislike(kind: DislikeKind, id: string): Promise<DislikePayload> {
  if (USE_MOCK) {
    const local = readLocal();
    const bucket = kind === 'news' ? 'news' : 'sources';
    local.dislikes[bucket] = local.dislikes[bucket].filter((x) => x !== id);
    writeLocal(local);
    return mockDelay({
      newsIds: local.dislikes.news,
      list: [],
      sourceKeys: local.dislikes.sources,
      sources: [],
    });
  }
  const data = await request<Record<string, unknown>>(
    `/me/dislikes/${kind}/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
  return normalizeDislikes(data);
}

function normalizeDislikes(
  data: Partial<Record<string, unknown>> | null | undefined,
): DislikePayload {
  const raw = (data ?? {}) as Record<string, unknown>;
  const newsIds = Array.isArray(raw.newsIds) ? raw.newsIds.map(String) : [];
  const list = Array.isArray(raw.list)
    ? (raw.list as Record<string, unknown>[]).map((r) => normalizeNewsItem(r))
    : [];
  const sourceKeys = Array.isArray(raw.sourceKeys) ? raw.sourceKeys.map(String) : [];
  const sources = Array.isArray(raw.sources)
    ? (raw.sources as Record<string, unknown>[]).map((r) => ({
        key: String(r.key ?? ''),
        name: String(r.name ?? ''),
        channel: String(r.channel ?? ''),
      }))
    : [];
  // 后端返回的是完整资讯对象，id 以 list 为准更可靠（newsIds 缺省时也不至于全丢）
  const ids = newsIds.length ? newsIds : list.map((n) => n.id);
  return { newsIds: ids, list, sourceKeys, sources };
}
