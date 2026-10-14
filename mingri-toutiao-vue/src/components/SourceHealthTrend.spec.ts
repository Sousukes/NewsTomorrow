import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import SourceHealthTrend from './SourceHealthTrend.vue';
import type { SourceHealth } from '@/types';

const health = (over: Partial<SourceHealth> = {}): SourceHealth => ({
  key: 'k',
  days: 30,
  keepDays: 30,
  summary: {
    attempts: 10,
    ok: 10,
    failed: 0,
    rate: 1,
    items: 170,
    avgItems: 17,
    lastOkAt: '2026-10-15T09:00:00',
    lastFailAt: null,
  },
  points: [],
  failures: [],
  ...over,
});

const point = (date: string, ok: number, failed: number, items: number) => ({
  date,
  total: ok + failed,
  ok,
  failed,
  items,
  rate: ok + failed === 0 ? null : Math.round((ok / (ok + failed)) * 1e4) / 1e4,
});

/** 取趋势格子的色调类名：none = 没抓，ok / partial / failed */
const tones = (w: ReturnType<typeof mount>) =>
  w.findAll('.mt-health__slot').map((s) => s.classes().find((c) => c.startsWith('is-')) ?? 'is-');

describe('components/SourceHealthTrend', () => {
  it('没有尝试时给结论而不是铺一张 30 格全空的图', () => {
    const w = mount(SourceHealthTrend, {
      props: { health: health({ summary: { ...health().summary, attempts: 0, rate: null } }) },
    });
    expect(w.text()).toContain('没有抓取记录');
    expect(w.find('.mt-health__chart').exists()).toBe(false);
  });

  it('成功率汇总按窗口展示，rate 为 null 时显示"—"而不是 0%', () => {
    const base = health().summary;
    const w = mount(SourceHealthTrend, {
      props: { health: health({ summary: { ...base, attempts: 0, rate: null } }) },
    });
    expect(w.find('.mt-health__empty').exists()).toBe(true);
    expect(w.text()).toContain('—');
  });

  it('满成功率的源显示 100%', () => {
    const w = mount(SourceHealthTrend, { props: { health: health() } });
    expect(w.find('.mt-health__metric b').text()).toBe('100%');
    expect(w.text()).toContain('近 30 天成功率');
  });

  it('部分失败时显示真实百分比', () => {
    const base = health().summary;
    const w = mount(SourceHealthTrend, {
      props: {
        health: health({
          summary: { ...base, attempts: 8, ok: 6, failed: 2, rate: 0.75 },
        }),
      },
    });
    expect(w.find('.mt-health__metric b').text()).toBe('75%');
  });

  describe('按天分桶', () => {
    it('按窗口铺满格子，没有数据的日子是"没抓"而不是"失败"', () => {
      // 后端刻意不补零：空白 = 那天没抓它。前端自己铺格子，否则序列看起来是连续的
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            days: 30,
            points: [point('2000-01-01', 1, 0, 20)], // 日期故意不在窗口内
          }),
        },
      });
      expect(w.findAll('.mt-health__slot')).toHaveLength(30);
      expect(w.findAll('.mt-health__slot.is-none')).toHaveLength(30);
      expect(w.findAll('.mt-health__slot.is-failed')).toHaveLength(0);
    });

    it('有数据的天按成功率分色：全成功 / 部分失败 / 全失败', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            days: 3,
            points: [
              point('2026-10-13', 2, 0, 40),
              point('2026-10-14', 1, 1, 20),
              point('2026-10-15', 0, 2, 0),
            ],
          }),
        },
      });
      // 只有最近三天落在窗口内（今天是窗口最后一天）
      const found = tones(w);
      expect(found).toContain('is-ok');
      expect(found).toContain('is-partial');
      expect(found).toContain('is-failed');
    });

    it('全失败的日子柱子有可见高度，而不是塌成零像素', () => {
      const w = mount(SourceHealthTrend, {
        props: { health: health({ days: 1, points: [point('2026-10-15', 0, 2, 0)] }) },
      });
      const bar = w.find('.mt-health__slot.is-failed .mt-health__bar');
      expect(bar.attributes('style')).toContain('height: 6%');
    });
  });

  describe('只有历史才看得见的两类结论', () => {
    const declining = health({
      points: [
        point('2026-10-11', 1, 0, 30),
        point('2026-10-12', 1, 0, 24),
        point('2026-10-13', 1, 0, 18),
        point('2026-10-14', 1, 0, 9),
        point('2026-10-15', 1, 0, 4),
      ],
      summary: {
        attempts: 5,
        ok: 5,
        failed: 0,
        rate: 1,
        items: 85,
        avgItems: 17,
        lastOkAt: '2026-10-15T09:00:00',
        lastFailAt: null,
      },
    });

    it('成功率满分但条数持续下滑时给出退化结论', () => {
      const w = mount(SourceHealthTrend, { props: { health: declining } });
      expect(w.text()).toContain('条数在下滑');
      expect(w.text()).toContain('27'); // 前期均值
      expect(w.text()).toContain('7'); // 最近均值
      // 关键：成功率仍然是 100%，所以退化不是"看得见的状态"能告诉你的
      expect(w.text()).toContain('成功率仍然 100%');
    });

    it('样本不足（<4 个成功日）时不硬下趋势结论', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            points: [point('2026-10-14', 1, 0, 40), point('2026-10-15', 1, 0, 3)],
          }),
        },
      });
      expect(w.text()).not.toContain('条数在下滑');
    });

    it('小幅波动不报警（阈值是"最近不足前期六成"）', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            points: [
              point('2026-10-11', 1, 0, 30),
              point('2026-10-12', 1, 0, 29),
              point('2026-10-13', 1, 0, 28),
              point('2026-10-14', 1, 0, 27),
              point('2026-10-15', 1, 0, 20),
            ],
          }),
        },
      });
      expect(w.text()).not.toContain('条数在下滑');
    });

    it('失败集中在同一钟点时提示"对方维护窗口，不必改我们的配置"', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            points: [point('2026-10-14', 1, 1, 10), point('2026-10-15', 1, 1, 10)],
            failures: [
              { at: '2026-10-15T03:12:00', error: '连接被重置', kind: 'transient', items: 0 },
              { at: '2026-10-14T03:44:00', error: '连接被重置', kind: 'transient', items: 0 },
              { at: '2026-10-13T03:05:00', error: '连接被重置', kind: 'transient', items: 0 },
            ],
          }),
        },
      });
      expect(w.text()).toContain('维护窗口');
      expect(w.text()).toContain('03');
    });

    it('失败时间分散时不下"规律"结论', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            points: [point('2026-10-15', 1, 3, 10)],
            failures: [
              { at: '2026-10-15T03:12:00', error: 'x', kind: 'transient', items: 0 },
              { at: '2026-10-14T09:44:00', error: 'x', kind: 'transient', items: 0 },
              { at: '2026-10-13T21:05:00', error: 'x', kind: 'transient', items: 0 },
            ],
          }),
        },
      });
      expect(w.text()).not.toContain('维护窗口');
    });
  });

  describe('最近失败', () => {
    it('列出时间、错误原文与性质标签', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            points: [point('2026-10-15', 0, 1, 0)],
            failures: [
              { at: '2026-10-15T03:12:00', error: 'HTTP 404', kind: 'permanent', items: 0 },
            ],
          }),
        },
      });
      expect(w.text()).toContain('最近失败');
      expect(w.text()).toContain('HTTP 404');
      expect(w.text()).toContain('永久性');
    });

    it('可重试失败标"可重试"，与永久性区分处置', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            points: [point('2026-10-15', 0, 1, 0)],
            failures: [
              { at: '2026-10-15T03:12:00', error: 'HTTP 503', kind: 'transient', items: 0 },
            ],
          }),
        },
      });
      expect(w.find('.mt-health__fkind').classes()).toContain('is-transient');
      expect(w.text()).not.toContain('永久性');
    });

    it('没有错误原文时给出占位而不是空白行', () => {
      const w = mount(SourceHealthTrend, {
        props: {
          health: health({
            points: [point('2026-10-15', 0, 1, 0)],
            failures: [{ at: '2026-10-15T03:12:00', error: null, kind: null, items: 0 }],
          }),
        },
      });
      expect(w.text()).toContain('无错误信息');
    });

    it('没有失败时整段不渲染', () => {
      const w = mount(SourceHealthTrend, { props: { health: health() } });
      expect(w.find('.mt-health__fails').exists()).toBe(false);
    });
  });

  it('把保留期写清楚（否则"为什么只能看 30 天"像 bug）', () => {
    const w = mount(SourceHealthTrend, { props: { health: health() } });
    expect(w.text()).toContain('保留 30 天');
  });
});
