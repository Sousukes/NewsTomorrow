<script setup lang="ts">
/**
 * 举报复核队列（E5 的宿主页，P1-77 补齐）。
 *
 * **为什么这一页之前不存在是一个真实缺口**：用户在 P1-66 就能提交举报，
 * 后端 P1-65 就有 `/api/admin/reports` 与 resolve 端点，但前端从未对接——
 * 运营看不到、也处理不了，举报进了黑洞。本页把这条链路接通。
 *
 * 三条刻意设计：
 * 1. **行上直接给「被举报的是什么」**：后端 P1-77 起带 `targetTitle` /
 *    `targetSnippet` / `targetNewsId`。只给 id 的话，评论因为没有独立页面，
 *    运营根本无从判断——那这一页就只剩一个装饰性的待办数字。
 * 2. **确认隐藏要二次确认，驳回不用**：隐藏会让内容对所有人不可见（虽可恢复），
 *    驳回是恢复可见（安全方向）。破坏性操作要确认，恢复不需要。
 * 3. **处置后就地移出待处理列表**：与评论复核同款处理——处理完还留在队列里，
 *    下一个人会重复看一遍。
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { fetchReports, resolveReport } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import { messageOf as message, useAdminGuard, useAdminList } from '@/composables/useAdminPage';
import type { AdminReportItem } from '@/types';

const router = useRouter();
const user = useUserStore();
const { noAccess, handleError, resetDenied } = useAdminGuard();

const PAGE_SIZE = 20;

type StatusFilter = 'pending' | 'all' | 'resolved' | 'dismissed';
const status = ref<StatusFilter>('pending');
const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'pending', label: '待处理' },
  { value: 'resolved', label: '已处置' },
  { value: 'dismissed', label: '已驳回' },
  { value: 'all', label: '全部' },
];

const { list, hasMore, loading, loadingMore, error, reload, loadMore } =
  useAdminList<AdminReportItem>();

async function load() {
  if (!user.isAdmin) return;
  resetDenied();
  try {
    await reload(() =>
      fetchReports({
        status: status.value,
        limit: PAGE_SIZE,
        // 后端只给 nextCursor，这里据此推导 hasMore（两个后端都如此，勿依赖 total）
        cursor: null,
      }).then((r) => ({ list: r.list, nextCursor: r.nextCursor, hasMore: r.nextCursor !== null })),
    );
  } catch (e) {
    // 权限问题交给 handleError：它置 denied 并返回空串，避免同时显示两段文案
    const msg = handleError(e, '举报列表加载失败，请稍后重试');
    if (msg) error.value = msg;
  }
}

onMounted(load);

function pick(next: StatusFilter) {
  if (next === status.value) return;
  status.value = next;
  void load();
}

function more() {
  void loadMore((cursor) =>
    fetchReports({ status: status.value, limit: PAGE_SIZE, cursor: Number(cursor) }).then((r) => ({
      list: r.list,
      nextCursor: r.nextCursor,
      hasMore: r.nextCursor !== null,
    })),
  );
}

/* ------------------------------ 处置 ------------------------------ */

const busyId = ref<number | null>(null);
/** 正在等二次确认的举报 id（只有「确认隐藏」走这一步） */
const confirming = ref<number | null>(null);
let toastTimer: ReturnType<typeof setTimeout> | undefined;
const toast = ref('');

function showToast(msg: string) {
  toast.value = msg;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.value = '';
  }, 3000);
}

async function doResolve(item: AdminReportItem, resolution: 'hidden' | 'kept') {
  busyId.value = item.id;
  confirming.value = null;
  try {
    const updated = await resolveReport(item.id, { resolution });
    if (status.value === 'pending') {
      // 待处理队列：处理完就移出，否则下一个人会重复看一遍
      list.value = list.value.filter((r) => r.id !== updated.id);
    } else {
      list.value = list.value.map((r) => (r.id === updated.id ? updated : r));
    }
    showToast(resolution === 'hidden' ? '已确认隐藏' : '已驳回，内容恢复可见');
  } catch (e) {
    showToast(message(e, '操作未生效，请稍后再试'));
  } finally {
    busyId.value = null;
  }
}

/* ------------------------------ 展示换算 ------------------------------ */

const TYPE_LABELS: Record<string, string> = { comment: '评论', news: '资讯' };
const STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  resolved: '已处置',
  dismissed: '已驳回',
};

function typeLabel(t: string): string {
  // 未知类型退化为原值而不是空白：一眼能看出漏配
  return TYPE_LABELS[t] ?? t;
}

/** 处置后的结论文案（仅非 pending 行显示） */
function resolutionText(item: AdminReportItem): string {
  if (item.status === 'pending') return STATUS_LABELS[item.status] ?? item.status;
  if (item.resolution === 'hidden') return '已确认隐藏';
  if (item.resolution === 'kept') return '已驳回（恢复可见）';
  return STATUS_LABELS[item.status] ?? item.status;
}

/** 被举报对象的跳转地址：资讯进详情，评论进它所属资讯的评论区 */
function targetLink(item: AdminReportItem): string {
  if (!item.targetNewsId) return '';
  return item.targetType === 'comment'
    ? `/news/${item.targetNewsId}/comments`
    : `/news/${item.targetNewsId}`;
}

function formatTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const pendingCount = computed(() => list.value.filter((r) => r.status === 'pending').length);
</script>

<template>
  <div class="rp">
    <AppNav variant="title" title="举报复核" show-back @back="router.back()" />

    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限查看举报"
      description="该功能仅对管理员开放。如需开通，请联系现有管理员把你的手机号加入白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <div class="rp__filterbar">
        <button
          v-for="o in STATUS_OPTIONS"
          :key="o.value"
          type="button"
          class="rp__chip"
          :class="{ 'rp__chip--on': status === o.value }"
          @click="pick(o.value)"
        >
          {{ o.label }}
        </button>
      </div>

      <p v-if="error" class="rp__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="rp__hint">加载中…</p>
      <p v-else-if="!list.length" class="rp__hint">
        {{ status === 'pending' ? '没有待复核的举报。' : '这个筛选下没有记录。' }}
      </p>

      <ul v-else class="rp__list">
        <li v-for="r in list" :key="r.id" class="rp__item">
          <div class="rp__head">
            <span class="rp__type">{{ typeLabel(r.targetType) }}</span>
            <span class="rp__reason">{{ r.reason }}</span>
            <span v-if="r.status !== 'pending'" class="rp__status">{{ resolutionText(r) }}</span>
            <span class="rp__time">{{ formatTime(r.createdAt) }}</span>
          </div>

          <!-- 被举报对象：复核要判断的就是这一块 -->
          <p v-if="r.targetMissing" class="rp__gone">被举报对象已不存在（可能已被删除）</p>
          <p v-else class="rp__target">
            <span class="rp__quote">{{
              r.targetType === 'comment' ? r.targetSnippet : r.targetTitle
            }}</span>
            <button
              v-if="targetLink(r)"
              type="button"
              class="rp__link"
              @click="router.push(targetLink(r))"
            >
              查看原文
            </button>
          </p>

          <div v-if="r.status === 'pending'" class="rp__actions">
            <template v-if="confirming === r.id">
              <Button
                variant="primary"
                size="md"
                label="确认隐藏"
                :disabled="busyId === r.id"
                @click="doResolve(r, 'hidden')"
              />
              <Button variant="secondary" size="md" label="取消" @click="confirming = null" />
            </template>
            <template v-else>
              <Button
                variant="secondary"
                size="md"
                label="确认隐藏"
                :disabled="busyId === r.id"
                @click="confirming = r.id"
              />
              <Button
                variant="secondary"
                size="md"
                label="驳回（恢复可见）"
                :disabled="busyId === r.id"
                @click="doResolve(r, 'kept')"
              />
            </template>
          </div>
        </li>
      </ul>

      <div v-if="hasMore" class="rp__more">
        <Button
          variant="secondary"
          size="md"
          :label="loadingMore ? '加载中…' : '加载更多'"
          :disabled="loadingMore"
          @click="more"
        />
      </div>

      <p v-if="status === 'pending' && pendingCount" class="rp__foot">
        待处理 {{ pendingCount }} 条
      </p>
    </template>

    <p v-if="toast" class="rp__toast" role="status">{{ toast }}</p>
  </div>
</template>

<style scoped>
.rp {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-page-bg);
  padding-bottom: 24px;
}

.rp__filterbar {
  display: flex;
  gap: 8px;
  padding: 10px 16px;
  overflow-x: auto;
}

.rp__chip {
  flex: none;
  padding: 5px 12px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-card-bg);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.rp__chip--on {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.rp__banner {
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

.rp__banner button {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 16px;
  cursor: pointer;
}

.rp__hint {
  margin: 0;
  padding: 32px 16px;
  text-align: center;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.rp__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0 16px;
  list-style: none;
}

.rp__item {
  padding: 12px;
  border-radius: 12px;
  background: var(--color-card-bg);
}

.rp__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.rp__type {
  flex: none;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--color-divider);
  color: var(--color-text-secondary);
  font-size: 11px;
}

.rp__reason {
  flex: none;
  color: var(--color-error);
  font-size: 12px;
  font-family: var(--font-cn);
}

.rp__status {
  flex: none;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--color-divider);
  color: var(--color-text-secondary);
  font-size: 11px;
}

.rp__time {
  margin-left: auto;
  flex: none;
  color: var(--color-text-secondary);
  font-size: 11px;
}

.rp__target {
  margin: 0 0 10px;
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  line-height: 1.5;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.rp__quote {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.rp__link {
  flex: none;
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-primary);
  font-size: 12px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.rp__gone {
  margin: 0 0 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-cn);
}

.rp__actions {
  display: flex;
  gap: 8px;
}

.rp__more {
  display: flex;
  justify-content: center;
  padding: 16px;
}

.rp__foot {
  margin: 0;
  padding: 0 16px;
  text-align: center;
  color: var(--color-text-secondary);
  font-size: 11px;
  font-family: var(--font-cn);
}

.rp__toast {
  position: fixed;
  left: 50%;
  bottom: 64px;
  transform: translateX(-50%);
  margin: 0;
  padding: 8px 16px;
  border-radius: 18px;
  background: rgba(0, 0, 0, 0.78);
  color: #fff;
  font-size: 12px;
  font-family: var(--font-cn);
  z-index: 20;
}
</style>
