/**
 * 阅读副本：已读/收藏详情落 localStorage，断网可读。
 * 独立于 marks（那里只有 id，没有正文）与内存 `detailCache`（刷新即失）。
 * 上限与作用域规则见 docs/后端/E8-PWA与离线阅读设计.md。
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

/** 资讯本体 + 落库时间（保留期淘汰用） */
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
    // Blob 不可用（极端环境）时退化为字符数近似
    return JSON.stringify(shape).length;
  }
}

/** 淘汰：过期 → 总量超限 → 条数超限，都从最旧的开始删 */
function prune(shape: ReaderShape, now = Date.now()): ReaderShape {
  const cutoff = now - READER_RETENTION_DAYS * 86400 * 1000;
  let out: ReaderShape = {};
  for (const [id, e] of Object.entries(shape)) {
    if (e.savedAt >= cutoff) out[id] = e;
  }
  // 新的在前，超限从尾部丢
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
    /* 隐私模式 / 配额已满：静默失败 */
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
  // 超限者整条不落（落半个资讯比没有更糟）
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

/** 当前作用域的副本 id（最近优先） */
export function listReaderIds(key: string, now = Date.now()): string[] {
  const cutoff = now - READER_RETENTION_DAYS * 86400 * 1000;
  return Object.entries(readRaw(key))
    .filter(([, e]) => e.savedAt >= cutoff)
    .sort((a, b) => b[1].savedAt - a[1].savedAt)
    .map(([id]) => id);
}
