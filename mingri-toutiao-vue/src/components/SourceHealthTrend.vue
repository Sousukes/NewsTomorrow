<script setup lang="ts">
/**
 * 源健康趋势（P1-19）：把一段时间内的抓取历史讲成一句话。
 *
 * 只罗列数字是不够的——运营要在几秒内决定"要不要现在动它"。本组件额外算两条
 * **当前健康字段结构性看不见**的结论：
 *
 * 1. **条数下滑**：成功率一直是 100%，但每天抓到的条数从 30 掉到 3。
 *    这种源在列表上永远是绿的，其实已经废了；
 * 2. **规律抖动**：失败集中在同一钟点，多半是对方站点的维护窗口——
 *    看出规律就不会去瞎改自己的配置。
 */
import { computed } from 'vue';
import type { SourceHealth } from '@/types';

const props = defineProps<{ health: SourceHealth }>();

/** 成功率文案。`null` = 窗口内没抓过，显示"—"而不是 0% */
const rateText = computed(() => {
  const r = props.health.summary.rate;
  return r === null ? '—' : `${Math.round(r * 100)}%`;
});

const avgText = computed(() => {
  const v = props.health.summary.avgItems;
  return v === null ? '—' : String(v);
});

const hasData = computed(() => props.health.summary.attempts > 0);

/**
 * 趋势格子：**按窗口铺满 `days` 个位置**，再把后端返回的点按日期落进去。
 *
 * 后端刻意不补零，所以这里必须自己铺格子——否则"没抓的那天"会直接消失，
 * 序列看起来是连续的，反而误导。
 */
const slots = computed(() => {
  const pad = (n: number) => String(n).padStart(2, '0');
  const keyOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const byDate = new Map(props.health.points.map((p) => [p.date, p]));
  const total = props.health.days;
  const max = Math.max(1, ...props.health.points.map((p) => p.items));
  const out: {
    date: string;
    label: string;
    tone: 'none' | 'ok' | 'partial' | 'failed';
    height: number;
    title: string;
  }[] = [];
  for (let i = 0; i < total; i += 1) {
    const d = new Date();
    d.setDate(d.getDate() - (total - 1 - i));
    const date = keyOf(d);
    const p = byDate.get(date);
    if (!p || !p.total) {
      out.push({
        date,
        label: `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
        tone: 'none',
        height: 0,
        title: `${date}：没有抓取记录`,
      });
      continue;
    }
    const tone = p.rate === 1 ? 'ok' : p.rate === 0 ? 'failed' : 'partial';
    out.push({
      date,
      label: `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
      tone,
      // 有尝试但一条没抓到也要留 6% 的可见高度：全红的格子若高度为 0 等于没显示
      height: p.items > 0 ? Math.max(8, Math.round((p.items / max) * 100)) : 6,
      title: `${date}：${p.total} 次（成功 ${p.ok} / 失败 ${p.failed}），共 ${p.items} 条`,
    });
  }
  return out;
});

/**
 * 条数下滑（第二类隐形故障）：拿**成功轮次**的平均条数前后对比。
 *
 * 用成功轮次而非总条数：失败轮天然 0 条，混进来会让"退化"被失败率掩盖。
 * 阈值取"最近一期不足前期六成"——小幅波动不该报警。
 */
const decline = computed(() => {
  const rows = props.health.points.filter((p) => p.ok > 0);
  if (rows.length < 4) return null; // 样本太少，任何"趋势"都可能是噪声
  const half = Math.floor(rows.length / 2);
  const avg = (xs: typeof rows) => xs.reduce((s, p) => s + p.items / p.ok, 0) / xs.length;
  const head = avg(rows.slice(0, half));
  const tail = avg(rows.slice(-half));
  if (head <= 0 || tail >= head * 0.6) return null;
  return { head: Math.round(head), tail: Math.round(tail) };
});

/**
 * 规律抖动（第一类隐形故障）：失败集中在同一钟点 = 对方维护窗口。
 * 门槛：至少 3 次失败，且其中六成以上落在同一个小时。
 */
const pattern = computed(() => {
  const list = props.health.failures;
  if (list.length < 3) return null;
  const hours = new Map<string, number>();
  for (const f of list) {
    const h = f.at.slice(11, 13);
    if (/^\d{2}$/.test(h)) hours.set(h, (hours.get(h) ?? 0) + 1);
  }
  const top = [...hours.entries()].sort((a, b) => b[1] - a[1])[0];
  if (!top || top[1] < 3 || top[1] / list.length < 0.6) return null;
  return { hour: top[0], count: top[1], total: list.length };
});

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
</script>

<template>
  <div class="mt-health">
    <div class="mt-health__summary">
      <span class="mt-health__metric">
        <b>{{ rateText }}</b>
        <i>近 {{ health.days }} 天成功率</i>
      </span>
      <span class="mt-health__metric">
        <b>{{ avgText }}</b>
        <i>成功轮均条数</i>
      </span>
      <span class="mt-health__metric">
        <b>{{ health.summary.attempts }}</b>
        <i>抓取轮次</i>
      </span>
      <span class="mt-health__metric">
        <b>{{ health.summary.failed }}</b>
        <i>失败次数</i>
      </span>
    </div>

    <!-- 没有尝试：不要画一张 30 格全空的图，那是噪音 -->
    <p v-if="!hasData" class="mt-health__empty">
      近 {{ health.days }} 天没有抓取记录——可能是源被停用、服务未运行，或每轮都被礼仪/熔断跳过。
    </p>

    <template v-else>
      <!-- 两条"只有历史看得见"的结论，放在图前面：先给判断，再给证据 -->
      <p v-if="decline" class="mt-health__note is-warn">
        条数在下滑：前期平均 {{ decline.head }} 条 → 最近 {{ decline.tail }} 条。 成功率仍然
        {{ rateText }}，但这个源正在变空。
      </p>
      <p v-if="pattern" class="mt-health__note">
        失败集中在每天 {{ pattern.hour }} 点左右（{{ pattern.count }}/{{ pattern.total }} 次）：
        像是对方站点的维护窗口，通常不必改我们的配置。
      </p>

      <div class="mt-health__chart">
        <span
          v-for="s in slots"
          :key="s.date"
          class="mt-health__slot"
          :class="`is-${s.tone}`"
          :title="s.title"
          :aria-label="s.title"
        >
          <i class="mt-health__bar" :style="{ height: `${s.height}%` }" />
        </span>
      </div>
      <div class="mt-health__axis">
        <span>{{ slots[0]?.label }}</span>
        <span>{{ slots[slots.length - 1]?.label }}</span>
      </div>
      <p class="mt-health__legend">
        <span class="mt-health__key is-ok" />成功 <span class="mt-health__key is-partial" />部分失败
        <span class="mt-health__key is-failed" />全部失败
        <span class="mt-health__key is-none" />没抓（不是失败）
      </p>
    </template>

    <div v-if="health.failures.length" class="mt-health__fails">
      <p class="mt-health__subtitle">最近失败</p>
      <ul class="mt-health__faillist">
        <li v-for="(f, i) in health.failures" :key="`${f.at}-${i}`" class="mt-health__failrow">
          <span class="mt-health__ftime">{{ formatTime(f.at) }}</span>
          <span class="mt-health__ferr" :title="f.error ?? ''">{{
            f.error || '（无错误信息）'
          }}</span>
          <span
            v-if="f.kind"
            class="mt-health__fkind"
            :class="`is-${f.kind}`"
            :title="
              f.kind === 'permanent'
                ? '永久性失败：需改配置或换源'
                : '可重试失败：系统会自动退避重试'
            "
          >
            {{ f.kind === 'permanent' ? '永久性' : '可重试' }}
          </span>
        </li>
      </ul>
    </div>

    <!-- 保留期说清楚：否则"为什么只能看 30 天"会显得像 bug -->
    <p class="mt-health__foot">健康记录保留 {{ health.keepDays }} 天，过期自动清理。</p>
  </div>
</template>

<style scoped>
.mt-health {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mt-health__summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.mt-health__metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--color-page-bg);
}

.mt-health__metric b {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.mt-health__metric i {
  font-style: normal;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.mt-health__empty,
.mt-health__note,
.mt-health__legend,
.mt-health__foot {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.mt-health__note {
  padding: 8px 10px;
  border-radius: 8px;
  background: #eef2f8;
  color: #4a5b78;
}

.mt-health__note.is-warn {
  background: #fdf3e6;
  color: #8a5a13;
}

.mt-health__chart {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 64px;
  padding: 4px 0;
  border-bottom: 1px solid var(--color-divider);
}

.mt-health__slot {
  position: relative;
  display: flex;
  flex: 1 1 0;
  align-items: flex-end;
  height: 100%;
  min-width: 3px;
}

.mt-health__bar {
  display: block;
  width: 100%;
  border-radius: 2px 2px 0 0;
  background: var(--color-divider);
}

/* 没抓的日子留一条极浅的底：既表示"这一格存在"，又不伪装成失败 */
.mt-health__slot.is-none::before {
  content: '';
  position: absolute;
  inset: auto 0 0 0;
  height: 2px;
  background: var(--color-divider);
}

.is-ok .mt-health__bar {
  background: #3aa76d;
}

.is-partial .mt-health__bar {
  background: #e0a03a;
}

.is-failed .mt-health__bar {
  background: var(--color-error);
}

.mt-health__axis {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}

.mt-health__legend {
  display: flex;
  align-items: center;
  gap: 4px;
}

.mt-health__key {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  margin-left: 8px;
  background: var(--color-divider);
}

.mt-health__key.is-ok {
  background: #3aa76d;
}

.mt-health__key.is-partial {
  background: #e0a03a;
}

.mt-health__key.is-failed {
  background: var(--color-error);
}

.mt-health__subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.mt-health__faillist {
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mt-health__failrow {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-primary);
}

.mt-health__ftime {
  flex: none;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}

.mt-health__ferr {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mt-health__fkind {
  flex: none;
  padding: 1px 6px;
  border-radius: 6px;
  background: #eef2f8;
  color: #4a5b78;
  font-size: 11px;
}

.mt-health__fkind.is-permanent {
  background: var(--color-error-bg);
  color: var(--color-error);
}
</style>
