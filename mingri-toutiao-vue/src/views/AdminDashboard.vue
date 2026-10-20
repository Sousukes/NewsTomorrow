<script setup lang="ts">
/**
 * 运营数据大盘（E7 / P1-77）。
 *
 * 这一页回答运营每天真正会问的三个问题，顺序就是版块顺序：
 * 1. **现在有什么事等人处理**（待办）——放最上面，因为它是"要不要现在动手"的唯一依据；
 * 2. **平台在正常运转吗**（内容存量 / 窗口互动 / 源健康）；
 * 3. **最近有没有异常信号**（审核与命中趋势 / 搜索热词）。
 *
 * 三条刻意设计：
 * 1. **单请求拉全部板块**（`fetchDashboard`）：拆成六个请求就会有六个 loading 态，
 *    而且无法保证同一时间口径——页面出现"总量按 7 天、趋势按 30 天"的割裂时，
 *    运营只会得出"数字对不上"这个结论。
 * 2. **不引图表库**：趋势用行内条形（宽度按窗口内峰值归一），
 *    引一个图表库的收益不抵它的体积与暗色模式适配成本（设计 §七）。
 * 3. **每个待办数字都能点进去**：大盘的价值在于"发现 → 处理"这条链路，
 *    只显示数字不给出口，等于把运营挡在半路。
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { fetchDashboard } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import { useAdminGuard } from '@/composables/useAdminPage';
import type { Dashboard } from '@/types';

const router = useRouter();
const user = useUserStore();
const { noAccess, handleError, resetDenied } = useAdminGuard();

/** 只给预设档：自由日期选择会引入"起止 + 时区"一整类问题（设计 §七） */
const RANGES = [
  { days: 7, label: '近 7 天' },
  { days: 30, label: '近 30 天' },
] as const;

const days = ref<number>(7);
const data = ref<Dashboard | null>(null);
const loading = ref(false);
const error = ref('');

async function load() {
  if (!user.isAdmin) return;
  resetDenied();
  loading.value = true;
  error.value = '';
  try {
    data.value = await fetchDashboard({ days: days.value });
  } catch (e) {
    // 权限问题交给 handleError：它置 denied 并返回空串，避免同时显示"无权限"和错误文案
    const msg = handleError(e, '大盘加载失败，请稍后重试');
    if (msg) error.value = msg;
  } finally {
    loading.value = false;
  }
}

function pick(day: number) {
  if (day === days.value) return;
  days.value = day;
  void load();
}

onMounted(load);

/* ------------------------------ 展示换算 ------------------------------ */

/** 成功率百分比；`rate` 为 null（无尝试）时显示 —，不要伪装成 0% */
function rateText(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}

/** 绝对时间 → 月-日 时:分（大盘只看"多久以前"，不看秒） */
function shortTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 趋势行内条形的宽度：按窗口内**峰值**归一，否则小数字在长条旁边看不出变化 */
const trendMax = computed(() => {
  const rows = data.value?.moderation.hitsTrend ?? [];
  let max = 0;
  for (const r of rows) max = Math.max(max, r.sensitive, r.nickname);
  return max || 1;
});

function barWidth(n: number): string {
  return `${Math.max(n > 0 ? 4 : 0, Math.round((n / trendMax.value) * 100))}%`;
}

/** 待办三条队列：命中即高亮，全部为 0 时明确说"没有待处理"（而不是留三个 0 让人猜） */
const todoItems = computed(() => {
  const t = data.value?.todo;
  if (!t) return [];
  return [
    { key: 'reports', label: '待复核举报', count: t.reports, to: '/admin/reports' },
    {
      key: 'comments',
      label: '待复核评论',
      count: t.commentReviews,
      to: '/admin/comments?status=pending',
    },
    {
      key: 'nicknames',
      label: '待复核昵称',
      count: t.nicknameReviews,
      to: '/admin/nickname-reviews',
    },
  ];
});

const todoTotal = computed(() => todoItems.value.reduce((n, it) => n + it.count, 0));
</script>

<template>
  <div class="dash">
    <AppNav variant="title" title="运营大盘" show-back @back="router.back()" />

    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限查看运营大盘"
      description="该功能仅对管理员开放。如需开通，请联系现有管理员把你的手机号加入白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <div class="dash__bar">
        <div class="dash__ranges">
          <button
            v-for="r in RANGES"
            :key="r.days"
            type="button"
            class="dash__range"
            :class="{ 'dash__range--on': days === r.days }"
            @click="pick(r.days)"
          >
            {{ r.label }}
          </button>
        </div>
        <button type="button" class="dash__refresh" :disabled="loading" @click="load">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>

      <p v-if="error" class="dash__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading && !data" class="dash__hint">加载中…</p>

      <template v-else-if="data">
        <!-- 1. 待办：最该被看见的一块 -->
        <section class="dash__block">
          <h2 class="dash__title">待处理</h2>
          <p v-if="!todoTotal" class="dash__ok">当前没有需要人工处理的事项。</p>
          <ul v-else class="dash__todo">
            <li v-for="it in todoItems" :key="it.key">
              <button
                type="button"
                class="dash__todocard"
                :class="{ 'dash__todocard--hot': it.count > 0 }"
                @click="router.push(it.to)"
              >
                <span class="dash__todocount">{{ it.count }}</span>
                <span class="dash__todolabel">{{ it.label }}</span>
                <span class="dash__todoarrow" aria-hidden="true">›</span>
              </button>
            </li>
          </ul>
        </section>

        <!-- 2. 内容存量 -->
        <section class="dash__block">
          <h2 class="dash__title">内容与账号</h2>
          <dl class="dash__grid">
            <div class="dash__cell">
              <dt>可见资讯</dt>
              <dd>{{ data.content.news }}</dd>
            </div>
            <div class="dash__cell">
              <dt>已下架</dt>
              <dd :class="{ dash__warn: data.content.newsHidden > 0 }">
                {{ data.content.newsHidden }}
              </dd>
            </div>
            <div class="dash__cell">
              <dt>评论</dt>
              <dd>{{ data.content.comments }}</dd>
            </div>
            <div class="dash__cell">
              <dt>用户</dt>
              <dd>{{ data.content.users }}</dd>
            </div>
            <div class="dash__cell">
              <dt>启用源</dt>
              <dd>
                {{ data.content.sourcesEnabled
                }}<span class="dash__sub">/{{ data.content.sources }}</span>
              </dd>
            </div>
          </dl>
        </section>

        <!-- 3. 窗口内互动 -->
        <section class="dash__block">
          <h2 class="dash__title">近 {{ data.days }} 天互动</h2>
          <dl class="dash__grid">
            <div class="dash__cell">
              <dt>搜索</dt>
              <dd>{{ data.engagement.searches }}</dd>
            </div>
            <div class="dash__cell">
              <dt>收藏</dt>
              <dd>{{ data.engagement.favorites }}</dd>
            </div>
            <div class="dash__cell">
              <dt>浏览</dt>
              <dd>{{ data.engagement.history }}</dd>
            </div>
            <div class="dash__cell">
              <dt>关注</dt>
              <dd>{{ data.engagement.follows }}</dd>
            </div>
            <div class="dash__cell">
              <dt>曝光</dt>
              <dd>{{ data.engagement.exposures }}</dd>
            </div>
            <div class="dash__cell">
              <dt>不感兴趣</dt>
              <dd>{{ data.engagement.dislikes }}</dd>
            </div>
          </dl>
        </section>

        <!-- 4. 源健康 -->
        <section class="dash__block">
          <h2 class="dash__title">
            源健康
            <span
              class="dash__badge"
              :class="{ 'dash__badge--warn': data.sourceHealth.unhealthy > 0 }"
            >
              {{ data.sourceHealth.unhealthy }} 个异常
            </span>
          </h2>
          <p v-if="!data.sourceHealth.worst.length" class="dash__ok">
            近 {{ data.sourceHealth.windowDays }} 天没有失败的源。
          </p>
          <ul v-else class="dash__rows">
            <li v-for="w in data.sourceHealth.worst" :key="w.key" class="dash__row">
              <div class="dash__rowhead">
                <span class="dash__rowname">{{ w.name }}</span>
                <span class="dash__rowrate">{{ rateText(w.rate) }}</span>
              </div>
              <div class="dash__meter" aria-hidden="true">
                <span
                  class="dash__meterfill"
                  :style="{ width: `${Math.max(2, Math.round((w.rate ?? 0) * 100))}%` }"
                />
              </div>
              <p class="dash__rowmeta">
                {{ w.attempts }} 次尝试 / {{ w.failed }} 次失败 · 最近失败
                {{ shortTime(w.lastFailAt) }}
              </p>
            </li>
          </ul>
        </section>

        <!-- 5. 审核与命中 -->
        <section class="dash__block">
          <h2 class="dash__title">审核与命中</h2>
          <dl class="dash__grid">
            <div class="dash__cell">
              <dt>下架资讯</dt>
              <dd>{{ data.moderation.newsHidden }}</dd>
            </div>
            <div class="dash__cell">
              <dt>隐藏评论</dt>
              <dd>{{ data.moderation.commentsHidden }}</dd>
            </div>
            <div class="dash__cell">
              <dt>敏感词命中</dt>
              <dd>{{ data.moderation.hits.sensitive }}</dd>
            </div>
            <div class="dash__cell">
              <dt>昵称命中</dt>
              <dd>{{ data.moderation.hits.nickname }}</dd>
            </div>
          </dl>

          <h3 class="dash__sub-title">命中趋势</h3>
          <p v-if="!data.moderation.hitsTrend.length" class="dash__ok">窗口内没有命中记录。</p>
          <ul v-else class="dash__trend">
            <li v-for="p in data.moderation.hitsTrend" :key="p.date" class="dash__trendrow">
              <span class="dash__trendday">{{ p.date.slice(5) }}</span>
              <span class="dash__trendbars">
                <span class="dash__bar dash__bar--s" :style="{ width: barWidth(p.sensitive) }" />
                <span class="dash__bar dash__bar--n" :style="{ width: barWidth(p.nickname) }" />
              </span>
              <span class="dash__trendnum">{{ p.sensitive }} / {{ p.nickname }}</span>
            </li>
          </ul>
          <p v-if="data.moderation.hitsTrend.length" class="dash__legend">
            条形按窗口峰值归一；数字为「敏感词 / 昵称」
          </p>
        </section>

        <!-- 6. 搜索热词 -->
        <section class="dash__block">
          <h2 class="dash__title">热门搜索</h2>
          <p v-if="!data.search.hot.length" class="dash__ok">窗口内还没有搜索记录。</p>
          <ol v-else class="dash__hot">
            <li v-for="(t, i) in data.search.hot" :key="t.term" class="dash__hotrow">
              <span class="dash__hotrank" :class="{ 'dash__hotrank--top': i < 3 }">{{
                i + 1
              }}</span>
              <span class="dash__hotterm">{{ t.term }}</span>
              <span class="dash__hotcount">{{ t.count }}</span>
            </li>
          </ol>
        </section>

        <p class="dash__foot">
          统计口径：窗口按 UTC 天切分；聚合于 {{ shortTime(data.generatedAt) }}
        </p>
      </template>
    </template>
  </div>
</template>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-page-bg);
  padding-bottom: 24px;
}

.dash__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 16px;
}

.dash__ranges {
  display: flex;
  gap: 8px;
}

.dash__range {
  padding: 5px 12px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-card-bg);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.dash__range--on {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.dash__refresh {
  border: none;
  background: transparent;
  padding: 4px 2px;
  color: var(--color-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.dash__refresh:disabled {
  color: var(--color-text-secondary);
  cursor: default;
}

.dash__banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 16px 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-error);
  font-size: 12px;
}

.dash__banner button {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 16px;
  cursor: pointer;
}

.dash__hint {
  margin: 0;
  padding: 32px 16px;
  text-align: center;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.dash__block {
  margin: 0 16px 12px;
  padding: 14px;
  border-radius: 12px;
  background: var(--color-card-bg);
}

.dash__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 0 10px;
  color: var(--color-text-primary);
  font-size: 15px;
  font-weight: 600;
  font-family: var(--font-cn);
}

.dash__sub-title {
  margin: 14px 0 8px;
  color: var(--color-text-primary);
  font-size: 13px;
  font-weight: 600;
  font-family: var(--font-cn);
}

.dash__badge {
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--color-divider);
  color: var(--color-text-secondary);
  font-size: 11px;
  font-weight: 400;
}

.dash__badge--warn {
  background: var(--color-error-bg);
  color: var(--color-error);
}

.dash__ok {
  margin: 0;
  padding: 8px 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-cn);
}

/* ---- 待办 ---- */

.dash__todo {
  display: flex;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.dash__todo > li {
  flex: 1;
  min-width: 0;
}

.dash__todocard {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 10px;
  border: 1px solid var(--color-divider);
  border-radius: 10px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  position: relative;
}

.dash__todocard--hot {
  border-color: var(--color-primary);
}

.dash__todocount {
  color: var(--color-text-primary);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
}

.dash__todocard--hot .dash__todocount {
  color: var(--color-primary);
}

.dash__todolabel {
  color: var(--color-text-secondary);
  font-size: 11px;
  font-family: var(--font-cn);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.dash__todoarrow {
  position: absolute;
  top: 8px;
  right: 8px;
  color: var(--color-text-secondary);
  font-size: 14px;
}

/* ---- 数字格 ---- */

.dash__grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin: 0;
}

.dash__cell dt {
  color: var(--color-text-secondary);
  font-size: 11px;
  font-family: var(--font-cn);
}

.dash__cell dd {
  margin: 2px 0 0;
  color: var(--color-text-primary);
  font-size: 17px;
  font-weight: 600;
}

.dash__cell dd.dash__warn {
  color: var(--color-error);
}

.dash__sub {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 400;
}

/* ---- 源健康 ---- */

.dash__rows {
  margin: 0;
  padding: 0;
  list-style: none;
}

.dash__row + .dash__row {
  margin-top: 12px;
}

.dash__rowhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.dash__rowname {
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.dash__rowrate {
  flex: none;
  color: var(--color-error);
  font-size: 13px;
  font-weight: 600;
}

.dash__meter {
  height: 4px;
  border-radius: 2px;
  background: var(--color-divider);
  overflow: hidden;
}

.dash__meterfill {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--color-error);
}

.dash__rowmeta {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 11px;
  font-family: var(--font-cn);
}

/* ---- 命中趋势 ---- */

.dash__trend {
  margin: 0;
  padding: 0;
  list-style: none;
}

.dash__trendrow {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
}

.dash__trendday {
  flex: none;
  width: 40px;
  color: var(--color-text-secondary);
  font-size: 11px;
}

.dash__trendbars {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.dash__bar {
  display: block;
  height: 4px;
  border-radius: 2px;
}

.dash__bar--s {
  background: var(--color-error);
}

.dash__bar--n {
  background: var(--color-primary);
}

.dash__trendnum {
  flex: none;
  width: 48px;
  text-align: right;
  color: var(--color-text-secondary);
  font-size: 11px;
}

.dash__legend {
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  font-size: 11px;
  font-family: var(--font-cn);
}

/* ---- 热词 ---- */

.dash__hot {
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: none;
}

.dash__hotrow {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
}

.dash__hotrank {
  flex: none;
  width: 18px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.dash__hotrank--top {
  color: var(--color-primary);
  font-weight: 700;
}

.dash__hotterm {
  flex: 1;
  min-width: 0;
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.dash__hotcount {
  flex: none;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.dash__foot {
  margin: 4px 16px 0;
  color: var(--color-text-secondary);
  font-size: 11px;
  font-family: var(--font-cn);
  text-align: center;
}
</style>
