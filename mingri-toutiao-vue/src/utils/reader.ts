/**
 * 阅读副本（E8 / P1-79）：把**已读/收藏详情**落到 localStorage，断网时可打开。
 *
 * 为什么要独立一份存储（而不是塞进 marks 或 news store 的内存缓存）：
 * 1. **`detailCache` 是内存 ref**，刷新即失——断网重启后没有任何残留；
 * 2. marks 只存 id，不给正文；离线读收藏需要的是**内容本身**（`content` 仅详情下发）；
 * 3. 频次与形态都不同：marks 由用户显式动作触发，这里是"看过一篇就落一份"，
 *    并进 marks 会让每次收藏/浏览都重写几十 KB 的正文。
 *
 * 三条硬约束（前两条是 localStorage 的物理限制，第三条是隐私）：
 * - **条数上限**：只保留最近的 N 条（默认 30）。它服务的是"刚看的 + 收藏的"，
 *   不是本地档案馆——真要看全部内容，联网时有服务端；
 * - **单条字节上限**：后端单条正文可达 8000 字（E1），超限的条目直接不落，
 *   否则一条异常长文就能把配额吃光；
 * - **按账号作用域隔离**：与 marks / exposures 同一套后缀规则（`anonymous` / `u<id>`），
 *   换账号后看不到上一个账号的阅读副本。
 */

/** 本地保留天数。过期内容允许"再在线拉一次"，不做永久本地档案 */
export const READER_RETENTION_DAYS = 7;
/** 最多保留多少条副本（最近优先） */
export const READER_CAP = 30;
/** 单条序列化后的字节上限（约 8 KB）：超出者不落，避免一条长文吃光配额 */
export const READER_ENTRY_MAX_BYTES = 8 * 1024;
/** 整份存储的字节上限（localStorage 常见配额 5 MB，留足余量给 marks / exposures） */
export const READER_TOTAL_MAX_BYTES = 512 * 1024;

const READER_PREFIX = 'mingri-toutiao:reader:';
const ANON_SCOPE = 'anonymous';

export const readerKey = (userId: number | null) =>
  `${READER_PREFIX}${userId === null ? ANON_SCOPE : `u${userId}`}`;

/** 一条阅读副本：资讯本体 + 落库时间（用于保留期淘汰） */
export interface ReaderEntry {
  item: Record<string, unknown>;
  savedAt: number;
}

type ReaderShape = Record<string, ReaderEntry>;

const isEntry = (v: unknown): v is ReaderEntry => {
  const o = v as ReaderEntry | null;
  return !!o && typeof o === 'object' && !!o.item && typeof o.savedAt === 'number';
};

function readRaw(key: string): ReaderShape {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: ReaderShape = {};
    for (const [id, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (isEntry(v)) out[id] = v;
    }
    return out;
  } catch {
    return {};
  }
}

function sizeOf(shape: ReaderShape): number {
  try {
    return new Blob([JSON.stringify(shape)]).size;
  } catch {
    // Blob 不可用（极端环境）时退化为字符数近似：宁可是近似，也别让写入崩
    return JSON.stringify(shape).length;
  }
}

/** 淘汰：先按保留期删过期，再按 total 上限从最旧的开始删，最后按条数上限截断 */
function prune(shape: ReaderShape, now = Date.now()): ReaderShape {
  const cutoff = now - READER_RETENTION_DAYS * 86400 * 1000;
  let out: ReaderShape = {};
  for (const [id, e] of Object.entries(shape)) {
    if (e.savedAt >= cutoff) out[id] = e;
  }
  // 保存时间倒序（新的在前），total 超限时从尾部开始丢
  const sorted = Object.entries(out).sort((a, b) => b[1].savedAt - a[1].savedAt);
  out = Object.fromEntries(sorted);
  while (Object.keys(out).length > READER_CAP) {
    const last = Object.keys(out).pop();
    if (!last) break;
    delete out[last];
  }
  while (sizeOf(out) > READER_TOTAL_MAX_BYTES && Object.keys(out).length > 1) {
    const last = Object.keys(out).pop();
    if (!last) break;
    delete out[last];
  }
  return out;
}

function persist(key: string, shape: ReaderShape): void {
  try {
    localStorage.setItem(key, JSON.stringify(shape));
  } catch {
    /* 隐私模式 / 配额已满：静默失败（阅读副本是增强，不是功能前提） */
  }
}

/** 读一份副本；过期或不存在返回 null */
export function readReader(
  key: string,
  id: string,
  now = Date.now(),
): Record<string, unknown> | null {
  const entry = readRaw(key)[id];
  if (!entry) return null;
  if (entry.savedAt < now - READER_RETENTION_DAYS * 86400 * 1000) return null;
  return entry.item;
}

/** 落一份副本（写穿）。单条超限或为空的内容不落。返回是否真的写入了 */
export function writeReader(
  key: string,
  id: string,
  item: Record<string, unknown>,
  now = Date.now(),
): boolean {
  if (!id || !item || !item.id) return false;
  const shape = prune(readRaw(key), now);
  const candidate: ReaderEntry = { item, savedAt: now };
  // 单条体积先量一次：超限者整条不落（而不是落一半——半个资讯比没有更糟）
  const bytes = sizeOf({ [id]: candidate });
  if (bytes > READER_ENTRY_MAX_BYTES) return false;
  shape[id] = candidate;
  persist(key, prune(shape, now));
  return true;
}

/** 清空某作用域的阅读副本（账号切换 / 用户主动清理） */
export function clearReader(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* 同 persist：静默失败 */
  }
}

/** 当前作用域有哪些 id（最近优先）——供收藏/历史页离线排序用 */
export function listReaderIds(key: string, now = Date.now()): string[] {
  const cutoff = now - READER_RETENTION_DAYS * 86400 * 1000;
  return Object.entries(readRaw(key))
    .filter(([, e]) => e.savedAt >= cutoff)
    .sort((a, b) => b[1].savedAt - a[1].savedAt)
    .map(([id]) => id);
}
