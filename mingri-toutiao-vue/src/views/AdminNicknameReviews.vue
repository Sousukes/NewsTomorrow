<script setup lang="ts">
/**
 * 昵称待人工复核队列（P1-24）。
 *
 * 命中「复核」级词条的昵称会被**放行**（不拦用户），同时打上待复核标记进这个队列——
 * 机器放它过去，人再看一眼。这一页回答三件事：
 * 1. **谁在等看**（队列按标记时间倒序，来一条处理一条）；
 * 2. **当初提交的是什么**（flaggedName / flaggedMatched——用户可能已经又改过名，
 *    治理要针对「那次提交」，不是「现在叫什么」，所以行上两者都展示）；
 * 3. **看完了做什么**（通过复核 = 人看过了，只清标记、不改昵称本身；
 *    仍不合适的正确出口是去词库把该词调成「拦截」，误杀的出口是「白名单」）。
 *
 * 与评论复核（P1-23）的差别：评论复核只清 pendingReview 标记；昵称是账号级属性，
 * 复核同样只清标记——后端没有管理端改名接口，「看不顺眼就帮他改掉」不是选项，
 * 能调的只有词条动作。分页用 limit+offset（后端无游标）。
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { fetchNicknameReviews, reviewNickname } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import type { NicknameReviewItem } from '@/types';

const router = useRouter();
const user = useUserStore();

const PAGE_SIZE = 20;

const list = ref<NicknameReviewItem[]>([]);
/** 当前筛选条件下的总条数（后端 COUNT，与翻页无关） */
const total = ref(0);
/** limit+offset 分页：已加载条数 < 总数即还有下一页 */
const hasMore = computed(() => list.value.length < total.value);
const loading = ref(false);
const loadingMore = ref(false);
const error = ref('');
/** 403：当前账号不具备管理能力（入口被隐藏后仍可能手敲网址到达） */
const denied = ref(false);

// ------------------------------ 筛选 ------------------------------

/** keyword 服务端匹配**当前昵称或手机号**（回答「这个人还在队列里吗」） */
const keyword = ref('');
const filtering = computed(() => keyword.value.trim() !== '');

function applySearch() {
  load();
}

/** 服务端筛选铁律：清了筛选条件必须重新拉取，状态复位 ≠ 数据复位 */
function clearFilters() {
  keyword.value = '';
  load();
}

// ------------------------------ 数据加载 ------------------------------

const busyId = ref<number | null>(null);
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
 * 否则页面会退化成「没有待复核的昵称」，把权限问题伪装成数据问题。
 */
const noAccess = computed(() => !user.isLoggedIn || !user.isAdmin || denied.value);

async function load() {
  if (!user.isAdmin) return;
  loading.value = true;
  error.value = '';
  denied.value = false;
  try {
    const page = await fetchNicknameReviews({
      keyword: keyword.value.trim(),
      limit: PAGE_SIZE,
    });
    list.value = page.list;
    total.value = page.total;
  } catch (e) {
    const msg = message(e, '加载复核队列失败');
    if (/403|无权限/.test(msg)) denied.value = true;
    else error.value = msg;
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  if (loadingMore.value || !hasMore.value) return;
  loadingMore.value = true;
  try {
    const page = await fetchNicknameReviews({
      keyword: keyword.value.trim(),
      limit: PAGE_SIZE,
      offset: list.value.length, // 后端是 limit+offset，没有游标
    });
    list.value = [...list.value, ...page.list];
    total.value = page.total;
  } catch (e) {
    error.value = message(e, '加载更多失败');
  } finally {
    loadingMore.value = false;
  }
}

onMounted(load);

// ------------------------------ 复核 ------------------------------

/**
 * 通过人工复核：只清「待复核」标记，**不改昵称本身**。
 * 后端幂等（重复提交只是再成功一次），前端仍做忙位守卫，连点只发一次请求。
 * 成功后就地移出队列并递减总数——留在眼前会让「复核成功了但还在队列里」造成误判。
 */
async function doReview(r: NicknameReviewItem) {
  if (busyId.value !== null) return;
  busyId.value = r.userId;
  try {
    const result = await reviewNickname(r.userId);
    // 服务端返回的 userId 是唯一真相；就地过滤比按下标稳
    list.value = list.value.filter((x) => x.userId !== result.userId);
    total.value = Math.max(0, total.value - 1);
    showToast(`已通过复核：${result.name || r.name}`);
  } catch (e) {
    error.value = message(e, '复核失败');
  } finally {
    busyId.value = null;
  }
}

// ------------------------------ 展示辅助 ------------------------------

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}
</script>

<template>
  <div class="admin">
    <AppNav variant="title" title="昵称复核" show-back @back="router.back()" />

    <p v-if="toast" class="admin__toast" role="status">{{ toast }}</p>

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址/登录态过期的情况 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限管理昵称复核"
      description="该功能仅对管理员开放。如需开通，请让现有管理员在「管理员管理」中加入你的手机号，或把账号加入后端 ADMIN_PHONES 白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <p class="admin__intro">
        命中<b>复核</b>级词条的昵称会被放行（不拦用户），同时进这个队列等人工看一眼。
        <b>通过复核</b
        >只代表「人看过了、可以展示」，<b>不改昵称本身</b>——仍不合适的正确出口是去词库把该词调成
        <b>拦截</b>，误杀的出口是 <b>白名单</b>。
      </p>

      <!-- 搜索：按当前昵称或手机号 -->
      <div class="admin__toolbar">
        <input
          v-model="keyword"
          type="search"
          class="admin__search"
          placeholder="搜索当前昵称或手机号"
          aria-label="搜索昵称或手机号"
          @keyup.enter="applySearch"
        />
        <button type="button" class="admin__sellink" @click="applySearch">搜索</button>
        <button v-if="filtering" type="button" class="admin__sellink" @click="clearFilters">
          清除筛选
        </button>
      </div>

      <div class="admin__actions">
        <Button
          variant="secondary"
          label="昵称敏感词"
          @click="router.push('/admin/nickname-words')"
        />
        <Button variant="secondary" label="评论治理" @click="router.push('/admin/comments')" />
        <Button variant="secondary" label="审计" @click="router.push('/admin/audit')" />
      </div>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!list.length && !filtering" class="admin__hint">
        还没有待复核的昵称。命中「复核」级词条的提交会先放行、再进这个队列。
      </p>
      <p v-else-if="!list.length" class="admin__hint">
        没有匹配的昵称。
        <button type="button" class="admin__sellink" @click="clearFilters">清除筛选</button>
      </p>

      <template v-else>
        <p class="admin__count">共 {{ total }} 条</p>
        <ul class="admin__list">
          <li v-for="r in list" :key="r.userId" class="admin__item" :data-user="r.userId">
            <div class="admin__item-head">
              <span class="admin__name" :title="r.name">{{ r.name }}</span>
              <span
                class="admin__chip admin__chip--review"
                title="命中复核级词条，已放行，等人看一眼"
              >
                待复核
              </span>
            </div>

            <p class="admin__meta">
              <span>{{ r.phone }}</span>
              <span v-if="r.flaggedAt">标记于 {{ formatTime(r.flaggedAt) }}</span>
            </p>

            <!-- 当初提交的昵称 ≠ 当前昵称时（用户又改过名），治理依据是那次提交 -->
            <p class="admin__content" :title="r.flaggedName ?? ''">
              当初提交：{{ r.flaggedName || '（未记录）' }}
            </p>
            <p v-if="r.flaggedMatched" class="admin__matched">
              命中「{{ r.flaggedMatched }}」——调动作或补别名去词库
            </p>

            <div class="admin__item-actions">
              <button type="button" :disabled="busyId === r.userId" @click="doReview(r)">
                {{ busyId === r.userId ? '复核中…' : '通过复核' }}
              </button>
              <!-- 「这个账号被谁动过」：带 target 进审计页，只看它的历史 -->
              <button
                type="button"
                :aria-label="`查看该用户的变更历史`"
                @click="router.push(`/admin/audit?target=${r.userId}`)"
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

.admin__intro {
  padding: 12px 16px 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

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

.admin__sellink {
  flex: none;
  border: none;
  background: none;
  color: var(--color-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.admin__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 16px 0;
}

.admin__banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 12px 16px 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fdecea;
  color: #d93025;
  font-size: 13px;
}

.admin__banner button {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 16px;
  cursor: pointer;
}

.admin__hint {
  padding: 16px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.admin__count {
  padding: 12px 16px 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.admin__list {
  margin: 0;
  padding: 0 12px;
  list-style: none;
}

.admin__item {
  margin-bottom: 10px;
  padding: 12px;
  border-radius: 10px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-xs);
}

.admin__item-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.admin__name {
  color: var(--color-text-primary);
  font-size: 14px;
  font-weight: 600;
  word-break: break-all;
}

.admin__chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  font-size: 11px;
}

/* 待复核：已放行、等人看一眼——警示黄而不是红，它不是处置态 */
.admin__chip--review {
  background: #fff4e5;
  color: #b06000;
}

.admin__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.admin__content {
  margin: 8px 0 0;
  color: var(--color-text-primary);
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}

/* 命中词与「去哪调」提示：复核队列的每条行都要能直接导向动作 */
.admin__matched {
  margin: 6px 0 0;
  color: #b06000;
  font-size: 12px;
  word-break: break-all;
}

.admin__item-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.admin__item-actions button {
  padding: 4px 12px;
  border: 1px solid var(--color-divider);
  border-radius: 999px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 12px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.admin__item-actions button:disabled {
  color: var(--color-text-secondary);
  cursor: default;
}

.admin__more {
  display: flex;
  justify-content: center;
  padding: 12px 16px 0;
}
</style>
