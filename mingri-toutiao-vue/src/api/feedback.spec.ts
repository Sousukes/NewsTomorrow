import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 负反馈与曝光 API 层（E3）。
 *
 * 重点验四件事：
 * 1. **离线分支与真后端同契约**：曝光幂等（重复上报只刷新时间）、负反馈幂等、
 *    返回完整集合——离线不是"随便存一下"，store 层不判断模式；
 * 2. **空批次不发请求**：把"什么都没看到"变成一次 422 是无谓的往返；
 * 3. **超过单次上限要分片**：后端会 422，而不分片的代价是"后半段永远不计曝光"；
 * 4. **归一化要兜住字段缺失**：`newsIds` 缺省时以 `list` 的 id 为准，
 *    否则"后端少返回一个字段"会让已减少的内容全部复活。
 */

const request = vi.fn();
const mockDelay = async (v: unknown) => v;
/** 可变开关：同文件内覆盖「离线」与「真后端」两条分支 */
const configState = { mock: true };

vi.mock('@/api/config', () => ({
  get USE_MOCK() {
    return configState.mock;
  },
  API_BASE: '/api',
}));
vi.mock('@/api/http', () => ({
  request: (...args: unknown[]) => request(...args),
  mockDelay: (v: unknown) => mockDelay(v),
}));

import {
  addDislike,
  fetchDislikes,
  removeDislike,
  reportExposures,
  type DislikePayload,
} from './feedback';

function payload(over: Partial<DislikePayload> = {}): Partial<DislikePayload> {
  return { newsIds: [], list: [], sourceKeys: [], sources: [], ...over };
}

beforeEach(() => {
  request.mockReset();
  configState.mock = true;
  localStorage.clear();
});

describe('离线分支（内置 mock）', () => {
  it('曝光幂等：重复上报只刷新时间，不重复计 written', async () => {
    const first = await reportExposures(['a', 'b']);
    expect(first).toEqual({ accepted: 2, written: 2 });
    const second = await reportExposures(['a', 'b', 'c']);
    expect(second).toEqual({ accepted: 3, written: 1 });
  });

  it('空批次不写本地也不产副作用', async () => {
    expect(await reportExposures([])).toEqual({ accepted: 0, written: 0 });
    expect(localStorage.getItem('mingri-toutiao:feedback')).toBeNull();
  });

  it('负反馈单条与来源分档存放，且幂等', async () => {
    await addDislike({ newsId: 'n1' });
    await addDislike({ newsId: 'n1' });
    await addDislike({ sourceKey: 'src-1' });
    const got = await fetchDislikes();
    expect(got.newsIds).toEqual(['n1']);
    expect(got.sourceKeys).toEqual(['src-1']);
  });

  it('取消负反馈幂等（取消不存在的项不报错）', async () => {
    await addDislike({ newsId: 'n1' });
    await removeDislike('news', 'n1');
    await removeDislike('news', 'n1');
    expect((await fetchDislikes()).newsIds).toEqual([]);
  });

  it('空标识直接拒绝，不落脏数据', async () => {
    await expect(addDislike({ newsId: '  ' })).rejects.toThrow('内容不存在');
    await expect(addDislike({ sourceKey: '' })).rejects.toThrow('来源不存在或已停用');
  });
});

describe('真后端分支', () => {
  beforeEach(() => {
    configState.mock = false;
  });

  it('曝光走 POST /me/exposures', async () => {
    request.mockResolvedValue({ accepted: 2, written: 2 });
    const got = await reportExposures(['a', 'b']);
    expect(request).toHaveBeenCalledWith('/me/exposures', {
      method: 'POST',
      body: { ids: ['a', 'b'] },
    });
    expect(got).toEqual({ accepted: 2, written: 2 });
  });

  it('空批次不发请求', async () => {
    await reportExposures([]);
    expect(request).not.toHaveBeenCalled();
  });

  it('超过 50 条分片上报，accepted/written 累加', async () => {
    request.mockResolvedValue({ accepted: 50, written: 50 });
    const ids = Array.from({ length: 51 }, (_, i) => `n${i}`);
    const got = await reportExposures(ids);
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[0][1].body.ids).toHaveLength(50);
    expect(request.mock.calls[1][1].body.ids).toHaveLength(1);
    expect(got).toEqual({ accepted: 100, written: 100 });
  });

  it('负反馈集合归一化：newsIds 缺省时以 list 的 id 为准', async () => {
    request.mockResolvedValue(
      payload({ list: [{ id: 'n9', title: '标题' } as never], sourceKeys: ['s1'] }),
    );
    const got = await fetchDislikes();
    expect(request).toHaveBeenCalledWith('/me/dislikes');
    expect(got.newsIds).toEqual(['n9']);
  });

  it('字段全缺时归一到安全默认值，不抛错', async () => {
    request.mockResolvedValue({});
    const got = await fetchDislikes();
    expect(got).toEqual({ newsIds: [], list: [], sourceKeys: [], sources: [] });
  });

  it('踩单条走 newsId，踩来源走 sourceKey（互斥由后端校验）', async () => {
    request.mockResolvedValue(payload());
    await addDislike({ newsId: 'n1' });
    expect(request).toHaveBeenLastCalledWith('/me/dislikes', {
      method: 'POST',
      body: { newsId: 'n1' },
    });
    await addDislike({ sourceKey: 's1' });
    expect(request).toHaveBeenLastCalledWith('/me/dislikes', {
      method: 'POST',
      body: { sourceKey: 's1' },
    });
  });

  it('取消按档位拼路径，并做 URL 编码', async () => {
    request.mockResolvedValue(payload());
    await removeDislike('news', 'n/1');
    expect(request).toHaveBeenLastCalledWith('/me/dislikes/news/n%2F1', { method: 'DELETE' });
    await removeDislike('sources', 's1');
    expect(request).toHaveBeenLastCalledWith('/me/dislikes/sources/s1', { method: 'DELETE' });
  });
});
