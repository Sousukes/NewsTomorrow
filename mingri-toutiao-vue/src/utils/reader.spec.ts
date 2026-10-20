import { beforeEach, describe, expect, it } from 'vitest';
import {
  READER_CAP,
  READER_ENTRY_MAX_BYTES,
  READER_RETENTION_DAYS,
  clearReader,
  listReaderIds,
  readReader,
  readerKey,
  writeReader,
} from './reader';

/** 三层上限各自对应一个真实故障，越过即把 localStorage 配额或账号隔离破坏掉。 */

const KEY = readerKey(7);
const item = (id: string) => ({ id, title: `标题 ${id}`, source: '源' });

beforeEach(() => {
  localStorage.clear();
});

describe('utils/reader', () => {
  it('按账号作用域分键（换账号后互不可见）', () => {
    expect(readerKey(null)).toBe('mingri-toutiao:reader:anonymous');
    expect(readerKey(7)).toBe('mingri-toutiao:reader:u7');
    expect(readerKey(7)).not.toBe(readerKey(8));

    writeReader(readerKey(7), 'n1', item('n1'));
    expect(readReader(readerKey(8), 'n1')).toBeNull();
    expect(readReader(readerKey(null), 'n1')).toBeNull();
    expect(readReader(readerKey(7), 'n1')).not.toBeNull();
  });

  it('写入后可读回，内容原样', () => {
    const it0 = item('n1');
    expect(writeReader(KEY, 'n1', it0)).toBe(true);
    expect(readReader(KEY, 'n1')).toEqual(it0);
  });

  it('未读过的 id 返回 null（调用方据此走正常加载）', () => {
    expect(readReader(KEY, 'never')).toBeNull();
  });

  it('空 id / 无 id 的条目不落（脏数据不进存储）', () => {
    expect(writeReader(KEY, '', item('x'))).toBe(false);
    expect(writeReader(KEY, 'x', { title: '没有 id' } as Record<string, unknown>)).toBe(false);
    expect(listReaderIds(KEY)).toEqual([]);
  });

  it('超出保留期的条目读不到（过期即允许重新在线拉取）', () => {
    const now = Date.now();
    writeReader(KEY, 'n1', item('n1'), now);
    const later = now + (READER_RETENTION_DAYS + 1) * 86400 * 1000;
    expect(readReader(KEY, 'n1', later)).toBeNull();
    expect(listReaderIds(KEY, later)).toEqual([]);
  });

  it('超长单条整条不落（半个资讯比没有更糟）', () => {
    const huge = { id: 'big', content: 'x'.repeat(READER_ENTRY_MAX_BYTES * 2) };
    expect(writeReader(KEY, 'big', huge)).toBe(false);
    expect(readReader(KEY, 'big')).toBeNull();
    // 不影响其他正常条目的读写
    expect(writeReader(KEY, 'ok', item('ok'))).toBe(true);
  });

  it(`超出条数上限时保留最近的 ${READER_CAP} 条`, () => {
    const now = 1_700_000_000_000;
    for (let i = 0; i < READER_CAP + 5; i++) {
      writeReader(KEY, `n${i}`, item(`n${i}`), now + i * 1000);
    }
    const ids = listReaderIds(KEY, now + READER_CAP * 10000);
    expect(ids).toHaveLength(READER_CAP);
    // 留下的是最新的（savedAt 最大）那批
    expect(ids[0]).toBe(`n${READER_CAP + 4}`);
    expect(ids).not.toContain('n0');
  });

  it('同一 id 重复写只留一份并刷新时间', () => {
    const now = 1_700_000_000_000;
    writeReader(KEY, 'n1', item('n1'), now);
    writeReader(KEY, 'n1', { ...item('n1'), title: '改过的标题' }, now + 60_000);
    const ids = listReaderIds(KEY, now + 120_000);
    expect(ids).toEqual(['n1']);
    expect(readReader(KEY, 'n1', now + 120_000)).toMatchObject({ title: '改过的标题' });
  });

  it('损坏的 JSON 不抛错，按空处理（本地存储随时可能被外部改坏）', () => {
    localStorage.setItem(KEY, '{ 这不是 JSON');
    expect(readReader(KEY, 'n1')).toBeNull();
    expect(listReaderIds(KEY)).toEqual([]);
  });

  it('非对象 / 数组形状按空处理', () => {
    localStorage.setItem(KEY, '[]');
    expect(listReaderIds(KEY)).toEqual([]);
    localStorage.setItem(KEY, '"字符串"');
    expect(listReaderIds(KEY)).toEqual([]);
  });

  it('逐条字段损坏只丢那一条，不影响其他条目', () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({ good: { item: item('good'), savedAt: Date.now() }, bad: { savedAt: 1 } }),
    );
    expect(listReaderIds(KEY)).toEqual(['good']);
  });

  it('clearReader 清空该作用域', () => {
    writeReader(KEY, 'n1', item('n1'));
    clearReader(KEY);
    expect(readReader(KEY, 'n1')).toBeNull();
  });
});
