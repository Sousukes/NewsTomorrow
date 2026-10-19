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

/**
 * E3 负反馈入口：卡片只表达**意图**，不直接调接口（由父级决定举报弹窗怎么开、
 * 踩完要不要原地移除）。这里钉的是"选项正确 + 不误触卡片跳转"。
 */
describe('components/NewsCard · 负反馈菜单（E3）', () => {
  it('默认收起，点「×」才展开', async () => {
    const w = mount(NewsCard, { props: { item: base({ sourceKey: 's1' }) } });
    expect(w.find('.mt-card__menu').exists()).toBe(false);
    await w.find('.mt-card__more').trigger('click');
    expect(w.find('.mt-card__menu').exists()).toBe(true);
  });

  it('点「×」不冒泡成卡片跳转（负向操作不该顺带进详情）', async () => {
    const w = mount(NewsCard, { props: { item: base({ sourceKey: 's1' }) } });
    await w.find('.mt-card__more').trigger('click');
    expect(w.emitted('click')).toBeUndefined();
  });

  it('「不感兴趣」派发 news 并收起菜单', async () => {
    const w = mount(NewsCard, { props: { item: base({ sourceKey: 's1' }) } });
    await w.find('.mt-card__more').trigger('click');
    const items = w.findAll('.mt-card__menu-item');
    await items[0].trigger('click');
    expect(w.emitted('feedback')?.[0]).toEqual([{ action: 'news', id: 'n1', sourceKey: 's1' }]);
    expect(w.find('.mt-card__menu').exists()).toBe(false);
  });

  it('「减少该来源」带上 sourceKey', async () => {
    const w = mount(NewsCard, { props: { item: base({ source: '中新网', sourceKey: 's1' }) } });
    await w.find('.mt-card__more').trigger('click');
    const items = w.findAll('.mt-card__menu-item');
    expect(items[1].text()).toContain('中新网');
    await items[1].trigger('click');
    expect(w.emitted('feedback')?.[0]).toEqual([{ action: 'source', id: 'n1', sourceKey: 's1' }]);
  });

  it('没有来源标识时不给「减少该来源」（避免点了必失败的选项）', async () => {
    const w = mount(NewsCard, { props: { item: base({ sourceKey: undefined }) } });
    await w.find('.mt-card__more').trigger('click');
    const labels = w.findAll('.mt-card__menu-item').map((b) => b.text());
    expect(labels).toEqual(['不感兴趣', '举报']);
  });

  it('「举报」只是表达意图，不在这里提交（提交归 ReportDialog）', async () => {
    const w = mount(NewsCard, { props: { item: base({ sourceKey: 's1' }) } });
    await w.find('.mt-card__more').trigger('click');
    const items = w.findAll('.mt-card__menu-item');
    await items[items.length - 1].trigger('click');
    expect(w.emitted('feedback')?.[0]).toEqual([{ action: 'report', id: 'n1', sourceKey: 's1' }]);
  });

  it('点空白处收起菜单（浮层留在屏幕上会挡住后面的卡片）', async () => {
    const w = mount(NewsCard, {
      props: { item: base({ sourceKey: 's1' }) },
      attachTo: document.body,
    });
    await w.find('.mt-card__more').trigger('click');
    expect(w.find('.mt-card__menu').exists()).toBe(true);
    document.dispatchEvent(new Event('click'));
    await w.vm.$nextTick();
    expect(w.find('.mt-card__menu').exists()).toBe(false);
    w.unmount();
  });
});
