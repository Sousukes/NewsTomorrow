<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { channelLabel } from '@/constants/channels';
import { fetchAdminNews, hideNews, restoreNews, updateNewsTop } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import type { AdminNewsItem } from '@/types';

const router = useRouter();
const user = useUserStore();

const PAGE_SIZE = 20;

const list = ref<AdminNewsItem[]>([]);
/** 当前筛选条件下的总条数（后端 COUNT，与翻页无关） */
const total = ref(0);
const hasMore = ref(false);
const nextCursor = ref<string | null>(null);
const loading = ref(false);
const loadingMore = ref(false);
const error = ref('');
/** 403：当前账号不具备管理能力（入口被隐藏后仍可能手敲网址到达） */
const denied = ref(false);

// ------------------------------ 筛选（P1-20） ------------------------------

type StatusFilter = 'all' | 'visible' | 'hidden';
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'visible', label: '可见' },
  { value: 'hidden', label: '已下架' },
];

const status = ref<StatusFilter>('all');
const keyword = ref('');

const filtering = computed(() => status.value !== 'all' || keyword.value.trim() !== '');

function clearFilters() {
  status.value = 'all';
  keyword.value = '';
  load(); // 服务端筛选：清了必须重新拉，否则列表还停留在旧筛选结果上
}

/** 汇总数字即筛选入口：再点一次取消（与源管理页同款交互） */
function toggleStatusFilter(next: StatusFilter) {
  status.value = status.value === next ? 'all' : next;
  load();
}

function applySearch() {
  load();
}

// ------------------------------ 数据加载 ------------------------------

const busyId = ref('');
let toastTimer: ReturnType<typeof setTimeout> | undefined;
const toast = ref('');

function showToast(msg: string) {
  toast.value = msg;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.value = '';
  }, 3000);
}

function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/**
 * 是否不具备访问资格。
 * 除了接口真的返回 403，**未登录 / 非管理员**也要一眼看出问题——
 * 否则页面会退化成"没有资讯"，把权限问题伪装成数据问题。
 */
const noAccess = computed(() => !user.isLoggedIn || !user.isAdmin || denied.value);

async function load() {
  if (!user.isAdmin) return;
  loading.value = true;
  error.value = '';
  denied.value = false;
  try {
    const page = await fetchAdminNews({
      status: status.value,
      keyword: keyword.value.trim(),
      limit: PAGE_SIZE,
    });
    list.value = page.list;
    total.value = page.total;
    hasMore.value = page.hasMore;
    nextCursor.value = page.nextCursor;
  } catch (e) {
    const msg = message(e, '加载资讯列表失败');
    if (/403|无权限/.test(msg)) denied.value = true;
    else error.value = msg;
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  if (!hasMore.value || loadingMore.value || !nextCursor.value) return;
  loadingMore.value = true;
  try {
    const page = await fetchAdminNews({
      status: status.value,
      keyword: keyword.value.trim(),
      limit: PAGE_SIZE,
      cursor: nextCursor.value,
    });
    list.value = [...list.value, ...page.list];
    hasMore.value = page.hasMore;
    nextCursor.value = page.nextCursor;
  } catch (e) {
    error.value = message(e, '加载更多失败');
  } finally {
    loadingMore.value = false;
  }
}

onMounted(load);

// ------------------------------ 下架 / 恢复 / 置顶 ------------------------------

const pendingHide = ref<AdminNewsItem | null>(null);
const hideReason = ref('');
const hideError = ref('');
const hiding = ref(false);

function openHide(n: AdminNewsItem) {
  pendingHide.value = n;
  hideReason.value = '';
  hideError.value = '';
}

/**
 * 行内更新：服务端返回值是唯一真相。
 * 变化后不再匹配当前筛选（如在「可见」页下架了一条）就地从列表移除并递减
 * 总数——留在眼前会让"下架成功了但还在可见列表里"造成误判。
 */
function applyUpdate(updated: AdminNewsItem) {
  const matchesFilter =
    status.value === 'all' ||
    (status.value === 'visible' && !updated.hidden) ||
    (status.value === 'hidden' && updated.hidden);
  const i = list.value.findIndex((x) => x.id === updated.id);
  if (!matchesFilter) {
    if (i >= 0) list.value.splice(i, 1);
    total.value = Math.max(0, total.value - 1);
    return;
  }
  if (i >= 0) list.value[i] = updated;
}

async function doHide() {
  const target = pendingHide.value;
  if (!target) return;
  const reason = hideReason.value.trim();
  // 前端预校验只为少跑一轮无效请求；**不能**替代后端校验（空理由 422 在后端把关）
  if (!reason) {
    hideError.value = '请填写下架理由（留痕是准入条件）';
    return;
  }
  if (reason.length > 300) {
    hideError.value = '理由不能超过 300 字';
    return;
  }
  hiding.value = true;
  hideError.value = '';
  try {
    const updated = await hideNews(target.id, { reason });
    applyUpdate(updated);
    pendingHide.value = null;
    showToast(`已下架《${updated.title}》`);
  } catch (e) {
    hideError.value = message(e, '下架失败，请稍后重试');
  } finally {
    hiding.value = false;
  }
}

async function doRestore(n: AdminNewsItem) {
  busyId.value = n.id;
  try {
    const updated = await restoreNews(n.id);
    applyUpdate(updated);
    showToast(`已恢复《${updated.title}》`);
  } catch (e) {
    error.value = message(e, '恢复失败');
  } finally {
    busyId.value = '';
  }
}

async function toggleTop(n: AdminNewsItem) {
  const next = !n.isTop;
  n.isTop = next; // 乐观更新：置顶是高频操作，不该等网络往返
  busyId.value = n.id;
  try {
    await updateNewsTop(n.id, { isTop: next });
  } catch (e) {
    n.isTop = !next; // 失败回滚，避免界面与服务端不一致
    error.value = message(e, '置顶操作失败');
  } finally {
    busyId.value = '';
  }
}

// ------------------------------ 展示辅助 ------------------------------

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}`;
}
</script>

<template>
  <div class="admin">
    <AppNav variant="title" title="资讯审核" show-back @back="router.back()" />

    <p v-if="toast" class="admin__toast" role="status">{{ toast }}</p>

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址/登录态过期的情况 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限审核资讯"
      description="该功能仅对管理员开放。如需开通，请让现有管理员在「管理员管理」中加入你的手机号，或把账号加入后端 ADMIN_PHONES 白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <!-- 搜索 + 状态筛选 -->
      <div class="admin__toolbar">
        <input
          v-model="keyword"
          type="search"
          class="admin__search"
          placeholder="搜索标题 / 摘要"
          aria-label="搜索资讯"
          @keyup.enter="applySearch"
        />
        <button type="button" class="admin__sellink" @click="applySearch">搜索</button>
        <button v-if="filtering" type="button" class="admin__sellink" @click="clearFilters">
          清除筛选
        </button>
      </div>
      <div class="admin__filters" role="group" aria-label="按状态筛选">
        <button
          v-for="st in STATUS_FILTERS"
          :key="st.value"
          type="button"
          class="admin__chipbtn"
          :class="{
            'is-active': status === st.value,
            'is-danger': st.value === 'hidden' && status === st.value,
          }"
          :aria-pressed="status === st.value"
          @click="toggleStatusFilter(st.value)"
        >
          {{ st.label }}
        </button>
      </div>

      <div class="admin__actions">
        <Button variant="secondary" label="RSS 源管理" @click="router.push('/admin/sources')" />
        <Button variant="secondary" label="管理员" @click="router.push('/admin/users')" />
        <Button variant="secondary" label="审计" @click="router.push('/admin/audit')" />
      </div>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!list.length && !filtering" class="admin__hint">还没有任何资讯。</p>
      <p v-else-if="!list.length" class="admin__hint">
        没有匹配的资讯。
        <button type="button" class="admin__sellink" @click="clearFilters">清除筛选</button>
      </p>

      <template v-else>
        <p class="admin__count">共 {{ total }} 条</p>
        <ul class="admin__list">
          <li v-for="n in list" :key="n.id" class="admin__item" :class="{ 'is-hidden': n.hidden }">
            <div class="admin__item-head">
              <span class="admin__name" :title="n.title">{{ n.title }}</span>
              <span class="admin__chip">{{ channelLabel(n.channel) }}</span>
              <span v-if="n.isTop" class="admin__chip admin__chip--top">置顶</span>
              <span
                v-if="n.hidden"
                class="admin__chip admin__chip--hidden"
                :title="`下架理由：${n.hiddenReason ?? '（未记录）'}`"
              >
                已下架
              </span>
            </div>

            <p v-if="n.summary" class="admin__summary" :title="n.summary">{{ n.summary }}</p>

            <div class="admin__meta">
              <span v-if="n.source">{{ n.source }}</span>
              <span v-if="n.publishedAt">{{ formatTime(n.publishedAt) }}</span>
              <span>{{ n.commentCount }} 评论</span>
              <span v-if="n.hidden && n.hiddenAt" class="admin__hidden-at">
                下架于 {{ formatTime(n.hiddenAt) }}
              </span>
            </div>

            <p v-if="n.hidden && n.hiddenReason" class="admin__reason" :title="n.hiddenReason">
              理由：{{ n.hiddenReason }}
            </p>

            <div class="admin__item-actions">
              <button
                v-if="!n.hidden"
                type="button"
                class="is-danger"
                :disabled="busyId === n.id"
                @click="openHide(n)"
              >
                下架
              </button>
              <button v-else type="button" :disabled="busyId === n.id" @click="doRestore(n)">
                恢复
              </button>
              <button type="button" :disabled="busyId === n.id" @click="toggleTop(n)">
                {{ n.isTop ? '取消置顶' : '置顶' }}
              </button>
              <!-- 「这条资讯被谁动过」：带 target 进审计页，只看它的历史 -->
              <button
                type="button"
                :aria-label="`查看《${n.title}》的变更历史`"
                @click="router.push(`/admin/audit?target=${n.id}`)"
              >
                历史
              </button>
            </div>
          </li>
        </ul>

        <div v-if="hasMore" class="admin__more">
          <Button
            variant="secondary"
            size="md"
            :label="loadingMore ? '加载中…' : '加载更多'"
            :disabled="loadingMore"
            @click="loadMore"
          />
        </div>
      </template>

      <!-- 下架确认：理由必填——「留痕是准入条件」 -->
      <div v-if="pendingHide" class="admin__confirm" role="alertdialog" aria-label="确认下架">
        <p>
          下架《{{ pendingHide.title }}》？下架后该资讯将从列表、详情、搜索与相关推荐中实时消失，
          可随时恢复。恢复后重新出现，无需等下一轮抓取。
        </p>
        <label class="admin__field">
          <span>下架理由（必填，≤300 字）</span>
          <textarea
            v-model="hideReason"
            maxlength="300"
            rows="3"
            placeholder="例如：来源不可靠 / 涉及侵权 / 内容错误"
          ></textarea>
        </label>
        <p v-if="hideError" class="admin__form-error">{{ hideError }}</p>
        <div class="admin__confirm-actions">
          <Button
            variant="primary"
            size="md"
            :label="hiding ? '下架中…' : '确认下架'"
            :disabled="hiding"
            @click="doHide"
          />
          <Button variant="secondary" size="md" label="取消" @click="pendingHide = null" />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.admin {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-page-bg);
  padding-bottom: 24px;
}

.admin__toast {
  margin: 10px 16px 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-primary);
  font-size: 13px;
}

/* 搜索 + 筛选 chips */
.admin__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px 0;
}

.admin__search {
  flex: 1;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--color-divider);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
}

.admin__filters {
  display: flex;
  gap: 6px;
  padding: 8px 16px 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.admin__filters::-webkit-scrollbar {
  display: none;
}

.admin__chipbtn {
  flex: none;
  padding: 4px 10px;
  border: 1px solid var(--color-divider);
  border-radius: 999px;
  background: var(--color-card-bg);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-cn);
  white-space: nowrap;
  cursor: pointer;
}

.admin__chipbtn.is-active {
  border-color: var(--color-primary);
  background: var(--color-primary);
  color: #ffffff;
}

/* 「已下架」筛选激活时用红色：它是处置态，不是普通分类 */
.admin__chipbtn.is-danger.is-active {
  border-color: #d93025;
  background: #d93025;
}

.admin__actions {
  display: flex;
  gap: 10px;
  padding: 10px 16px;
}

.admin__banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 16px 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--color-card-bg);
  color: #d93025;
  font-size: 12px;
}

.admin__banner button {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 16px;
  cursor: pointer;
}

.admin__hint {
  margin: 0;
  padding: 32px 16px;
  text-align: center;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.admin__count {
  margin: 0;
  padding: 4px 16px 8px;
  font-size: 11px;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}

.admin__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0 16px;
  list-style: none;
}

.admin__item {
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-card);
}

/* 已下架整行降透明度：状态要一眼可辨，但理由仍要可读 */
.admin__item.is-hidden {
  opacity: 0.72;
}

.admin__item-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.admin__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.admin__chip {
  flex: none;
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  font-size: 11px;
  white-space: nowrap;
}

.admin__chip--top {
  background: #eaf1fa;
  color: #2f6690;
}

.admin__chip--hidden {
  background: #fdeceb;
  color: #b3352b;
}

.admin__summary {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.admin__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.admin__hidden-at {
  font-variant-numeric: tabular-nums;
}

.admin__reason {
  margin: 6px 0 0;
  font-size: 11px;
  color: #b3352b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.admin__item-actions {
  display: flex;
  gap: 16px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--color-divider);
}

.admin__item-actions button {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
}

.admin__item-actions button:disabled {
  color: var(--color-text-secondary);
  cursor: default;
}

.admin__item-actions .is-danger {
  color: #d93025;
}

.admin__more {
  display: flex;
  justify-content: center;
  padding: 14px 16px 0;
}

.admin__sellink {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  white-space: nowrap;
  cursor: pointer;
}

.admin__confirm {
  position: sticky;
  bottom: 0;
  margin: 16px;
  padding: 14px;
  border-radius: 12px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-card);
}

.admin__confirm p {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-primary);
}

.admin__confirm-actions {
  display: flex;
  gap: 10px;
}

.admin__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.admin__field textarea {
  padding: 8px 10px;
  border: 1px solid var(--color-divider);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  resize: vertical;
}

.admin__form-error {
  margin: 8px 0 0;
  font-size: 12px;
  color: #d93025;
}
</style>
