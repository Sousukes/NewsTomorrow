import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import NewsCard from './NewsCard.vue';
import type { NewsItem } from '@/types';

const base = (over: Partial<NewsItem> = {}): NewsItem => ({
  id: 'n1',
  title: '标题',
  source: '来源',
  time: '刚刚',
  channel: 'recommend',
  variant: 'left-right',
  ...over,
});

describe('components/NewsCard', () => {
  it('有真实封面时渲染 <img>，带 lazy / async / alt（M3 无障碍与懒加载）', () => {
    const w = mount(NewsCard, {
      props: { item: base({ cover: 'https://cdn.x.com/a.jpg' }) },
    });
    const img = w.find('img');
    expect(img.exists()).toBe(true);
    expect(img.attributes('src')).toBe('https://cdn.x.com/a.jpg');
    expect(img.attributes('loading')).toBe('lazy');
    expect(img.attributes('decoding')).toBe('async');
    expect(img.attributes('alt')).toBe('标题');
  });

  it('无封面时回退渐变占位（不渲染 img）', () => {
    const w = mount(NewsCard, { props: { item: base({ cover: '' }) } });
    expect(w.find('img').exists()).toBe(false);
    expect(w.find('.mt-card__cover').exists()).toBe(true);
  });

  it('危险协议封面被拒，回退占位', () => {
    const w = mount(NewsCard, {
      props: { item: base({ cover: 'javascript:alert(1)' }) },
    });
    expect(w.find('img').exists()).toBe(false);
  });

  it('三图变体渲染 3 张图', () => {
    const w = mount(NewsCard, {
      props: {
        item: base({ variant: 'triple', images: ['a.jpg', 'b.jpg', 'c.jpg'] }),
      },
    });
    expect(w.findAll('.mt-card__triple-img')).toHaveLength(3);
    expect(w.findAll('img')).toHaveLength(3);
  });

  it('置顶标记仅在 text 变体展示', () => {
    const w = mount(NewsCard, { props: { item: base({ variant: 'text', isTop: true }) } });
    expect(w.find('.mt-card__top').text()).toBe('置顶');
  });

  it('点击卡片派发 click(id)', async () => {
    const w = mount(NewsCard, { props: { item: base() } });
    await w.find('article').trigger('click');
    expect(w.emitted('click')?.[0]).toEqual(['n1']);
  });

  it('播放数 >= 1 万时格式化为「万」', () => {
    const w = mount(NewsCard, {
      props: { item: base({ variant: 'video', playCount: 128000 }) },
    });
    expect(w.text()).toContain('12.8万播放');
  });
});
