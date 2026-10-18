import { beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick, ref, type Ref } from 'vue';
import { mount } from '@vue/test-utils';

/**
 * `useNewsDetail`：深链安全的加载逻辑——此前 0% 覆盖。
 *
 * 它决定的是一条用户能感知的分支：
 * **内存里有 → 直接显示**；**没有 → 骨架屏 → 要么内容、要么"不存在"**。
 * 三种失败必须区分开，否则用户会被误导：
 * - 查到了但后端说没有 → 「内容不存在或已下架」；
 * - **请求本身失败（网络/5xx）→ 不能也说"已下架"**，那是把临时故障说成事实；
 * - 且**不能留下 unhandled rejection**（`onMounted` 里没人 await 它）。
 */

const getById = vi.fn();
const loadDetail = vi.fn();
vi.mock('@/stores/news', () => ({
  useNewsStore: () => ({ getById, loadDetail }),
}));

import { useNewsDetail } from '@/composables/useNewsDetail';

/** 挂一个最小宿主组件，让 onMounted / watch 真的跑起来 */
function mountUse(idRef: Ref<string>) {
  let api!: ReturnType<typeof useNewsDetail>;
  const Host = defineComponent({
    setup() {
      api = useNewsDetail(() => idRef.value);
      return () => h('div');
    },
  });
  const w = mount(Host);
  return {
    w,
    get api() {
      return api;
    },
  };
}

const flush = async () => {
  await new Promise((r) => setTimeout(r, 0));
  await nextTick();
};

beforeEach(() => {
  vi.clearAllMocks();
  loadDetail.mockResolvedValue(null);
});

describe('useNewsDetail', () => {
  it('内存里已有：直接用，不发请求（避免重复拉取）', async () => {
    // 持久桩：init 与 onMounted 里的 resolve() 各读一次 store
    getById.mockReturnValue({ id: 'n1', title: '已在内存' });
    const { api } = mountUse(ref('n1'));
    await flush();
    expect(api.item.value).toMatchObject({ id: 'n1' });
    expect(loadDetail).not.toHaveBeenCalled();
    expect(api.resolving.value).toBe(false);
    expect(api.failed.value).toBe(false);
  });

  it('内存没有：按 id 拉取，成功后落位', async () => {
    getById.mockReturnValue(null);
    loadDetail.mockResolvedValueOnce({ id: 'n1', title: '拉回来的' });
    const { api } = mountUse(ref('n1'));
    await flush();
    expect(loadDetail).toHaveBeenCalledWith('n1');
    expect(api.item.value).toMatchObject({ id: 'n1' });
    expect(api.failed.value).toBe(false);
  });

  it('后端明确说没有：failed = true（这才是"已下架"）', async () => {
    getById.mockReturnValue(null);
    loadDetail.mockResolvedValueOnce(null);
    const { api } = mountUse(ref('n1'));
    await flush();
    expect(api.item.value).toBeNull();
    expect(api.failed.value).toBe(true);
  });

  it('**请求失败也要置 failed**（否则会被当成"内容不存在"，还把错误吞成 unhandled rejection）', async () => {
    getById.mockReturnValue(null);
    loadDetail.mockRejectedValueOnce(new Error('网络断了'));
    const { api } = mountUse(ref('n1'));
    await flush();
    expect(api.failed.value).toBe(true);
    // 关键：resolving 必须复位，不能永远转圈
    expect(api.resolving.value).toBe(false);
    expect(api.item.value).toBeNull();
  });

  it('id 为空时不发请求（路由尚未就绪的瞬间）', async () => {
    getById.mockReturnValue(null);
    const { api } = mountUse(ref(''));
    await flush();
    expect(loadDetail).not.toHaveBeenCalled();
    expect(api.failed.value).toBe(false);
  });

  it('id 变化时重新解析（深链从 A 跳到 B）', async () => {
    getById.mockReturnValue(null);
    loadDetail.mockResolvedValue({ id: 'n2', title: 'B' });
    const idRef = ref('n1');
    const { api } = mountUse(idRef);
    await flush();
    expect(loadDetail).toHaveBeenCalledWith('n1');

    idRef.value = 'n2';
    await flush();
    expect(loadDetail).toHaveBeenCalledWith('n2');
    expect(api.item.value).toMatchObject({ id: 'n2' });
  });

  it('reload 可手动重试：失败后修好能恢复（错误态不能是死路）', async () => {
    getById.mockReturnValue(null);
    loadDetail.mockRejectedValueOnce(new Error('第一次失败'));
    const { api } = mountUse(ref('n1'));
    await flush();
    expect(api.failed.value).toBe(true);

    loadDetail.mockResolvedValueOnce({ id: 'n1', title: '重试成功' });
    await api.reload();
    expect(api.failed.value).toBe(false);
    expect(api.item.value).toMatchObject({ id: 'n1' });
  });

  it('成功的重试会清掉上一次的 failed（不能留着旧错误态）', async () => {
    getById.mockReturnValue(null);
    loadDetail.mockResolvedValueOnce(null);
    const { api } = mountUse(ref('n1'));
    await flush();
    expect(api.failed.value).toBe(true);

    loadDetail.mockResolvedValueOnce({ id: 'n1', title: '有内容了' });
    await api.reload();
    expect(api.failed.value).toBe(false);
  });
});
