import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import BannerCarousel from '@/components/BannerCarousel.vue';
import type { NewsItem } from '@/types';

function item(id: string, extra: Partial<NewsItem> = {}): NewsItem {
  return {
    id,
    title: `标题-${id}`,
    source: '测试源',
    time: '1小时前',
    channel: 'recommend',
    variant: 'text',
    ...extra,
  };
}

const THREE = [item('a'), item('b'), item('c')];

/** 捕获 Vue 的渲染告警（重复 key 等） */
function spyWarn() {
  return vi.spyOn(console, 'warn').mockImplementation(() => {});
}

let wrapper: ReturnType<typeof mount> | null = null;

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.restoreAllMocks();
});

describe('BannerCarousel', () => {
  it('渲染 N+2 帧（无限回环的首尾克隆帧）', () => {
    wrapper = mount(BannerCarousel, { props: { items: THREE, interval: 0 } });
    const slides = wrapper.findAll('.banner__slide');
    expect(slides).toHaveLength(5); // [c, a, b, c, a]
  });

  it('克隆帧不产生重复 key 告警（回归用例）', async () => {
    const warn = spyWarn();
    wrapper = mount(BannerCarousel, { props: { items: THREE, interval: 0 } });
    await nextTick();
    // 强制一次更新，确保 patch 阶段也走通（重复 key 会在此暴露）
    await wrapper.setProps({ items: [...THREE] });
    await nextTick();

    const dup = warn.mock.calls.filter(
      (c) => typeof c[0] === 'string' && c[0].includes('Duplicate keys'),
    );
    expect(dup).toHaveLength(0);
  });

  it('帧数变化时仍无重复 key（切换列表长度）', async () => {
    const warn = spyWarn();
    wrapper = mount(BannerCarousel, { props: { items: THREE, interval: 0 } });
    await wrapper.setProps({ items: [item('x'), item('y')] });
    await nextTick();

    const dup = warn.mock.calls.filter(
      (c) => typeof c[0] === 'string' && c[0].includes('Duplicate keys'),
    );
    expect(dup).toHaveLength(0);
    expect(wrapper.findAll('.banner__slide')).toHaveLength(4);
  });

  it('配图与标题按 id 正确对应（index 建表，非逐帧 find）', () => {
    wrapper = mount(BannerCarousel, {
      props: {
        items: [
          item('a', { cover: 'https://cdn.test/a.jpg' }),
          item('b', { cover: '' }),
          item('c', { cover: 'https://cdn.test/c.jpg' }),
        ],
        interval: 0,
      },
    });
    const imgs = wrapper.findAll('.banner__img');
    // 5 帧中：a 出现 2 次、c 出现 2 次、b 无图 → 共 4 张真实配图
    expect(imgs).toHaveLength(4);
    const srcs = imgs.map((i) => i.attributes('src'));
    expect(srcs.filter((s) => s === 'https://cdn.test/a.jpg')).toHaveLength(2);
    expect(srcs.filter((s) => s === 'https://cdn.test/c.jpg')).toHaveLength(2);
    // 轨道顺序为 [c(克隆), a, b, c, a(克隆)]，alt 须与各自 id 对应
    const alts = imgs.map((i) => i.attributes('alt'));
    expect(alts).toEqual(['标题-c', '标题-a', '标题-c', '标题-a']);
  });

  it('危险协议的封面被拒绝，不渲染 <img>', () => {
    wrapper = mount(BannerCarousel, {
      props: { items: [item('a', { cover: 'javascript:alert(1)' })], interval: 0 },
    });
    expect(wrapper.findAll('.banner__img')).toHaveLength(0);
  });

  it('点击幻灯片派发 select 事件', async () => {
    wrapper = mount(BannerCarousel, { props: { items: THREE, interval: 0 } });
    await wrapper.findAll('.banner__slide')[1].trigger('click');
    expect(wrapper.emitted('select')).toBeTruthy();
    expect(wrapper.emitted('select')![0]).toEqual(['a']);
  });

  it('单帧时不渲染（无回环意义）', () => {
    wrapper = mount(BannerCarousel, { props: { items: [item('only')], interval: 0 } });
    expect(wrapper.findAll('.banner__slide')).toHaveLength(1);
  });

  it('卸载后清理自动播放定时器', () => {
    vi.useFakeTimers();
    const clearSpy = vi.spyOn(globalThis, 'clearInterval');
    wrapper = mount(BannerCarousel, { props: { items: THREE, interval: 1000 } });
    wrapper.unmount();
    wrapper = null;
    expect(clearSpy).toHaveBeenCalled();
    vi.useRealTimers();
  });
});
