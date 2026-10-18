<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { fetchAdminComments, hideComment, restoreComment, reviewComment } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import { messageOf as message, useAdminGuard, useAdminList } from '@/composables/useAdminPage';
import type { AdminCommentItem } from '@/types';

const router = useRouter();
const user = useUserStore();
// D3：鉴权守卫抽到 composable
const { noAccess, handleError, resetDenied } = useAdminGuard();

const PAGE_SIZE = 20;

// D3：列表状态机抽到 composable（游标分页契约：服务端 nextCursor + hasMore）

const {
  list,
  total,
  hasMore,
  loading,
  loadingMore,
  error,
  reload,
  loadMore: loadMorePage,
} = useAdminList<AdminCommentItem>();
// ------------------------------ 筛选（P1-22） ------------------------------

// pending（P1-23）：命中敏感词被脱敏、等人工看一眼的评论
type StatusFilter = 'all' | 'visible' | 'hidden' | 'pending';
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'visible', label: '可见' },
  { value: 'hidden', label: '已隐藏' },
  { value: 'pending', label: '待复核' },
];

const status = ref<StatusFilter>('all');
const keyword = ref('');
/** 按所属资讯过滤（点行上的资讯标签设置；null 为不限） */
const newsId = ref<string | null>(null);
const newsTitle = ref('');

const filtering = computed(
  () => status.value !== 'all' || keyword.value.trim() !== '' || newsId.value !== null,
);

function clearFilters() {
  status.value = 'all';
  keyword.value = '';
  newsId.value = null;
  newsTitle.value = '';
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

/** 点行上的资讯标签 → 只看这篇文章下的评论（再点一次取消） */
function filterByNews(c: AdminCommentItem) {
  if (newsId.value === c.newsId) {
    clearNewsFilter();
  } else {
    newsId.value = c.newsId;
    newsTitle.value = c.newsTitle || c.newsId;
    load();
  }
}

function clearNewsFilter() {
  newsId.value = null;
  newsTitle.value = '';
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

/**
 * 是否不具备访问资格。
 * 除了接口真的返回 403，**未登录 / 非管理员**也要一眼看出问题——
 * 否则页面会退化成"没有评论"，把权限问题伪装成数据问题。
 */

async function load() {
  if (!user.isAdmin) return;
  resetDenied();
  try {
    await reload(() =>
      fetchAdminComments({
        status: status.value,
        keyword: keyword.value.trim(),
        newsId: newsId.value ?? undefined,
        limit: PAGE_SIZE,
      }),
    );
  } catch (e) {
    // 权限问题由 handleError 转成 denied 并返回空串（顺带清掉 error）
    error.value = handleError(e, '加载评论列表失败');
  }
}

async function loadMore() {
  await loadMorePage((cursor) =>
    fetchAdminComments({
      status: status.value,
      keyword: keyword.value.trim(),
      newsId: newsId.value ?? undefined,
      limit: PAGE_SIZE,
      cursor,
    }),
  );
}

onMounted(load);

// ------------------------------ 隐藏 / 恢复 ------------------------------

const pendingHide = ref<AdminCommentItem | null>(null);
const hideReason = ref('');
const hideError = ref('');
const hiding = ref(false);

function openHide(c: AdminCommentItem) {
  pendingHide.value = c;
  hideReason.value = '';
  hideError.value = '';
}

/**
 * 行内更新：服务端返回值是唯一真相。
 * 变化后不再匹配当前筛选（如在「可见」页隐藏了一条）就地从列表移除并递减
 * 总数——留在眼前会让"隐藏成功了但还在可见列表里"造成误判。
 */
function applyUpdate(updated: AdminCommentItem) {
  const matchesFilter =
    status.value === 'all' ||
    (status.value === 'visible' && !updated.hidden) ||
    (status.value === 'hidden' && updated.hidden) ||
    // 复核通过后不再"待复核"，就地移出 pending 列表（与隐藏/恢复同款处理）
    (status.value === 'pending' && updated.pendingReview);
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
    hideError.value = '请填写隐藏理由（留痕是准入条件）';
    return;
  }
  if (reason.length > 300) {
    hideError.value = '理由不能超过 300 字';
    return;
  }
  hiding.value = true;
  hideError.value = '';
  try {
    const updated = await hideComment(target.id, { reason });
    applyUpdate(updated);
    pendingHide.value = null;
    showToast('已隐藏该评论');
  } catch (e) {
    hideError.value = message(e, '隐藏失败，请稍后重试');
  } finally {
    hiding.value = false;
  }
}

async function doRestore(c: AdminCommentItem) {
  busyId.value = c.id;
  try {
    const updated = await restoreComment(c.id);
    applyUpdate(updated);
    showToast('已恢复该评论');
  } catch (e) {
    error.value = message(e, '恢复失败');
  } finally {
    busyId.value = '';
  }
}

/**
 * 通过人工复核（P1-23）：只清「待复核」标记，不改可见性与计数。
 *
 * 脱敏评论从一开始就可见，复核是"人看过了"这个事实，不是"重新放行"；
 * 复核后仍觉得不合适应当走**隐藏**，而不是把标记留着不管。
 */
async function doReview(c: AdminCommentItem) {
  busyId.value = c.id;
  try {
    const updated = await reviewComment(c.id);
    applyUpdate(updated);
    showToast('已通过复核');
  } catch (e) {
    error.value = message(e, '复核失败');
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
    <AppNav variant="title" title="评论治理" show-back @back="router.back()" />

    <p v-if="toast" class="admin__toast" role="status">{{ toast }}</p>

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址/登录态过期的情况 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限管理评论"
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
          placeholder="搜索评论内容"
          aria-label="搜索评论"
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

      <!-- 资讯过滤提示：当前只看某篇文章下的评论 -->
      <p v-if="newsId" class="admin__newsfilter">
        仅看《{{ newsTitle }}》下的评论
        <button type="button" class="admin__sellink" @click="clearNewsFilter">取消</button>
      </p>

      <div class="admin__actions">
        <Button variant="secondary" label="RSS 源管理" @click="router.push('/admin/sources')" />
        <Button variant="secondary" label="资讯审核" @click="router.push('/admin/news')" />
        <Button variant="secondary" label="管理员" @click="router.push('/admin/users')" />
        <Button variant="secondary" label="审计" @click="router.push('/admin/audit')" />
      </div>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!list.length && !filtering" class="admin__hint">还没有任何评论。</p>
      <p v-else-if="!list.length" class="admin__hint">
        没有匹配的评论。
        <button type="button" class="admin__sellink" @click="clearFilters">清除筛选</button>
      </p>

      <template v-else>
        <p class="admin__count">共 {{ total }} 条</p>
        <ul class="admin__list">
          <li v-for="c in list" :key="c.id" class="admin__item" :class="{ 'is-hidden': c.hidden }">
            <div class="admin__item-head">
              <span class="admin__name" :title="c.userName">{{ c.userName }}</span>
              <span v-if="c.rootId === null" class="admin__chip">主评论</span>
              <span v-else class="admin__chip">回复</span>
              <span
                v-if="c.hidden"
                class="admin__chip admin__chip--hidden"
                :title="`隐藏理由：${c.hiddenReason ?? '（未记录）'}`"
              >
                已隐藏
              </span>
              <span
                v-if="c.pendingReview"
                class="admin__chip admin__chip--pending"
                title="命中敏感词，已自动脱敏并发布，等待人工复核"
              >
                待复核
              </span>
            </div>

            <p class="admin__content" :title="c.content">{{ c.content }}</p>

            <div class="admin__meta">
              <!-- 点资讯标签按文章过滤：「这篇文章下有哪些评论被处置过」 -->
              <button
                type="button"
                class="admin__newschip"
                :title="`仅看《${c.newsTitle || c.newsId}》下的评论`"
                @click="filterByNews(c)"
              >
                《{{ c.newsTitle || c.newsId }}》
              </button>
              <span v-if="c.createdAt">{{ formatTime(c.createdAt) }}</span>
              <span>{{ c.likes }} 赞</span>
              <span v-if="c.rootId === null && c.replyCount > 0">{{ c.replyCount }} 条回复</span>
              <span v-if="c.hidden && c.hiddenAt" class="admin__hidden-at">
                隐藏于 {{ formatTime(c.hiddenAt) }}
              </span>
            </div>

            <p v-if="c.hidden && c.hiddenReason" class="admin__reason" :title="c.hiddenReason">
              理由：{{ c.hiddenReason }}
            </p>

            <div class="admin__item-actions">
              <button
                v-if="!c.hidden"
                type="button"
                class="is-danger"
                :disabled="busyId === c.id"
                @click="openHide(c)"
              >
                隐藏
              </button>
              <button v-else type="button" :disabled="busyId === c.id" @click="doRestore(c)">
                恢复
              </button>
              <!-- 脱敏评论仍可见，复核只是"人看过了"；真不合适要隐藏，别只留着标记 -->
              <button
                v-if="c.pendingReview"
                type="button"
                :disabled="busyId === c.id"
                @click="doReview(c)"
              >
                通过复核
              </button>
              <!-- 「这条评论被谁动过」：带 target 进审计页，只看它的历史 -->
              <button
                type="button"
                :aria-label="`查看该评论的变更历史`"
                @click="router.push(`/admin/audit?target=${c.id}`)"
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

      <!-- 隐藏确认：理由必填——「留痕是准入条件」 -->
      <div v-if="pendingHide" class="admin__confirm" role="alertdialog" aria-label="确认隐藏">
        <p>
          隐藏这条评论？隐藏后它将从评论列表、回复与预览中实时消失（其回复若失去主评论语境亦不可见），
          可随时恢复。恢复后重新出现，无需等任何定时任务。
        </p>
        <label class="admin__field">
          <span>隐藏理由（必填，≤300 字）</span>
          <textarea
            v-model="hideReason"
            maxlength="300"
            rows="3"
            placeholder="例如：含广告信息 / 人身攻击 / 与文章无关"
          ></textarea>
        </label>
        <p v-if="hideError" class="admin__form-error">{{ hideError }}</p>
        <div class="admin__confirm-actions">
          <Button
            variant="primary"
            size="md"
            :label="hiding ? '隐藏中…' : '确认隐藏'"
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

/* 搜索 + 筛选 chips（与资讯审核页同款） */
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

/* 「已隐藏」筛选激活时用红色：它是处置态，不是普通分类 */
.admin__chipbtn.is-danger.is-active {
  border-color: #d93025;
  background: #d93025;
}

/* 资讯过滤提示条 */
.admin__newsfilter {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 16px 0;
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-secondary);
  font-size: 12px;
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

/* 已隐藏整行降透明度：状态要一眼可辨，但内容与理由仍要可读 */
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

.admin__chip--hidden {
  background: #fdeceb;
  color: #b3352b;
}

/* 待复核：脱敏后已发布，等人看一眼——用警示黄而不是红，它不是处置态 */
.admin__chip--pending {
  background: #fff4e5;
  color: #a15c00;
}

/* 评论正文：最多两行，全文在 title 里（治理要扫读，长评论不该撑爆列表） */
.admin__content {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-primary);
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  word-break: break-word;
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

/* 行内资讯标签：点了按文章过滤 */
.admin__newschip {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-primary);
  font-size: 11px;
  font-family: var(--font-cn);
  cursor: pointer;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
