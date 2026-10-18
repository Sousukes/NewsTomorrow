import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

/**
 * 图集页（F3'）。
 *
 * 核心是那条**凑帧规则**：封面 + 最多 3 张图 + 补帧，凑足 6 张以对齐
 * 「图集 · 1/6」的版面。规则错了不会报错，只会让某几张图的说明文字错位
 * ——所以这里直接断言帧数与顺序。
 */

const detail = { item: { value: null as null | Record<string, unknown> } };
vi.mock('@/composables/useNewsDetail', () => ({
  useNewsDetail: () => ({ item: detail.item }),
}));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ params: {} }),
}));

import Gallery from '@/views/Gallery.vue';

const mountPage = () => mount(Gallery, { global: { stubs: { Button: true } } });
const vm = (w: ReturnType<typeof mountPage>) =>
  w.vm as unknown as { frames: string[]; frameBg: (s: string) => string };

beforeEach(() => {
  vi.clearAllMocks();
  detail.item.value = null;
});

describe('Gallery', () => {
  it('无资讯时不产帧（不渲染 6 个空图）', () => {
    const w = mountPage();
    expect(vm(w).frames).toEqual([]);
  });

  it('有封面 + 3 张图时正好 6 帧，且封面排第一', () => {
    detail.item.value = { id: 'n1', cover: 'c.jpg', images: ['a.jpg', 'b.jpg', 'c.jpg'] };
    const w = mountPage();
    const frames = vm(w).frames;
    expect(frames).toHaveLength(6);
    expect(frames[0]).toBe('n1cover');
  });

  it('图超过 3 张时只取前 3 张（版面固定 6 帧）', () => {
    detail.item.value = { id: 'n1', cover: '', images: ['1', '2', '3', '4', '5'] };
    const w = mountPage();
    const frames = vm(w).frames;
    expect(frames).toHaveLength(6);
    expect(frames.filter((f) => f.includes('img'))).toHaveLength(3);
  });

  it('封面缺失时用补帧凑满，不留空位', () => {
    detail.item.value = { id: 'n1', cover: '', images: [] };
    const w = mountPage();
    const frames = vm(w).frames;
    expect(frames).toHaveLength(6);
    expect(frames.every((f) => typeof f === 'string' && f.length > 0)).toBe(true);
  });

  it('帧背景由种子决定且稳定（同一帧两次取值一致，避免闪烁）', () => {
    detail.item.value = { id: 'n1', cover: 'c.jpg', images: [] };
    const w = mountPage();
    const bg1 = vm(w).frameBg('n1cover');
    const bg2 = vm(w).frameBg('n1cover');
    expect(bg1).toBe(bg2);
  });
});
