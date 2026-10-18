import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

/**
 * 关注 store（E6b）：乐观更新与失败回滚。
 *
 * 重点验三件事：
 * 1. 关注/取关**立即**生效（不等接口），否则按钮点了没反应；
 * 2. 成功后用服务端返回的**完整集合**覆盖——服务端即事实源（与收藏同处方）；
 * 3. 失败**回滚**到调用前的 keys：只动本地会让界面永久停留在一个假状态，
 *    这是比"慢一点"严重得多的 bug。
 */

const followSource = vi.fn(async (_key: string) => ({
  keys: [_key],
  list: [{ key: _key, name: '新华社', channel: 'tech', enabled: true, newsCount: 2 }],
}));
const unfollowSource = vi.fn(async (_key: string) => ({ keys: [], list: [] }));
const fetchFollows = vi.fn(async () => ({ keys: [] as string[], list: [] }));
const fetchSourceDirectory = vi.fn(async () => [
  { key: 'src_1', name: '新华社', channel: 'tech', enabled: true, newsCount: 2 },
  { key: 'src_2', name: '体育台', channel: 'sports', enabled: true, newsCount: 5 },
]);

vi.mock('@/api/follows', () => ({
  followSource: (k: string) => followSource(k),
  unfollowSource: (k: string) => unfollowSource(k),
  fetchFollows: () => fetchFollows(),
  fetchSourceDirectory: () => fetchSourceDirectory(),
}));

import { useFollowsStore } from './follows';

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe('stores/follows', () => {
  it('目录加载后可按 key 查询、判断可关注性', async () => {
    const s = useFollowsStore();
    await s.loadDirectory();
    expect(s.loaded).toBe(true);
    expect(s.sourceOf('src_1')?.name).toBe('新华社');
    expect(s.canFollow('src_1')).toBe(true);
    // 不在目录里的源不可关注（停用/已删除在目录阶段就被过滤掉了）
    expect(s.canFollow('src_nope')).toBe(false);
  });

  it('目录加载失败只记 error，不影响已有关注状态', async () => {
    fetchSourceDirectory.mockRejectedValueOnce(new Error('网络错误'));
    const s = useFollowsStore();
    await s.loadDirectory();
    expect(s.error).toBe('网络错误');
    expect(s.sources).toEqual([]);
  });

  it('关注：先乐观入列，再用服务端集合覆盖', async () => {
    const s = useFollowsStore();
    await s.loadDirectory();

    const ok = await s.follow('src_1');
    expect(ok).toBe(true);
    expect(followSource).toHaveBeenCalledWith('src_1');
    expect(s.isFollowing('src_1')).toBe(true);
    // 服务端返回的列表信息被合并进目录，不会变成只有 key 的空壳
    expect(s.sourceOf('src_1')?.name).toBe('新华社');
  });

  it('关注失败回滚：keys 回到调用前，并返回 false 供视图提示', async () => {
    followSource.mockRejectedValueOnce(new Error('上限'));
    const s = useFollowsStore();
    await s.loadDirectory();

    expect(await s.follow('src_1')).toBe(false);
    expect(s.isFollowing('src_1')).toBe(false);
    expect(s.error).toBe('上限');
  });

  it('取关失败同样回滚（界面不能停在"已取关"的假状态）', async () => {
    const s = useFollowsStore();
    await s.loadDirectory();
    await s.follow('src_1');
    unfollowSource.mockRejectedValueOnce(new Error('网络错误'));

    expect(await s.unfollow('src_1')).toBe(false);
    expect(s.isFollowing('src_1')).toBe(true);
  });

  it('重复关注不重复请求（幂等在 store 层也生效）', async () => {
    const s = useFollowsStore();
    await s.loadDirectory();
    await s.follow('src_1');
    await s.follow('src_1');
    expect(followSource).toHaveBeenCalledTimes(1);
  });

  it('refresh 失败时保持现状（不清空——清空会让界面闪成未关注）', async () => {
    const s = useFollowsStore();
    await s.loadDirectory();
    await s.follow('src_1');
    fetchFollows.mockRejectedValueOnce(new Error('网络错误'));
    await s.refresh();
    expect(s.isFollowing('src_1')).toBe(true);
  });

  it('refresh 成功时以服务端为准', async () => {
    const s = useFollowsStore();
    await s.loadDirectory();
    fetchFollows.mockResolvedValueOnce({ keys: ['src_2'], list: [] });
    await s.refresh();
    expect(s.keys).toEqual(['src_2']);
    expect(s.isFollowing('src_1')).toBe(false);
  });
});
