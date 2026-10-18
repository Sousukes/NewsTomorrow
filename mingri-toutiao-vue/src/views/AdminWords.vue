<script setup lang="ts">
/**
 * 敏感词库维护 + 命中日志（P1-23）。
 *
 * 这一页要回答三件事：
 * 1. **机器现在在拦什么**（词库）；
 * 2. **改完立刻生效吗**（保存即失效本进程缓存，集群下用「重载索引」同步其他实例）；
 * 3. **有没有误杀**（命中日志——脱敏不可逆，它是复盘与申诉的唯一证据）。
 *
 * 刻意把命中日志放在词库下面而不是单独开页面：加完词的人接下来必然想看
 * "这个词拦掉了什么"，两个列表在同一屏才形成闭环。
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import {
  createWord,
  deleteWord,
  fetchModerationHits,
  fetchWords,
  reloadWords,
  updateWord,
} from '@/api/admin';
import { useUserStore } from '@/stores/user';
import { messageOf as message, useAdminGuard } from '@/composables/useAdminPage';
import type { SensitiveHitItem, SensitiveWordItem, WordAction } from '@/types';

const router = useRouter();
const user = useUserStore();
// D3：鉴权守卫抽到 composable（手敲网址 / 登录态过期 / 令牌已失效三种情况）
const { noAccess, handleError, resetDenied } = useAdminGuard();

const WORD_LIMIT = 200;
const HIT_PAGE_SIZE = 20;

const ACTIONS: { value: WordAction; label: string; hint: string }[] = [
  { value: 'block', label: '拦截', hint: '命中即拒绝提交（高危内容）' },
  { value: 'mask', label: '脱敏', hint: '替换为 * 后发布，并置为待人工复核' },
  { value: 'allow', label: '白名单', hint: '命中它的片段豁免——降误杀用' },
];

function actionLabel(a: WordAction): string {
  return ACTIONS.find((x) => x.value === a)?.label ?? a;
}

// ------------------------------ 词库 ------------------------------

const words = ref<SensitiveWordItem[]>([]);
const wordTotal = ref(0);
const loading = ref(false);
const error = ref('');

const keyword = ref('');
const actionFilter = ref<WordAction | ''>('');
const onlyEnabled = ref(false);

// 新增表单
const newWord = ref('');
const newAction = ref<WordAction>('mask');
const newCategory = ref('');
const adding = ref(false);
const addError = ref('');

let toastTimer: ReturnType<typeof setTimeout> | undefined;
const toast = ref('');

function showToast(msg: string) {
  toast.value = msg;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.value = '';
  }, 3000);
}

async function load() {
  if (!user.isAdmin) return;
  loading.value = true;
  error.value = '';
  resetDenied();
  try {
    const page = await fetchWords({
      keyword: keyword.value.trim(),
      action: actionFilter.value,
      enabled: onlyEnabled.value ? true : undefined,
      limit: WORD_LIMIT,
    });
    words.value = page.list;
    wordTotal.value = page.total;
  } catch (e) {
    error.value = handleError(e, '加载词库失败');
  } finally {
    loading.value = false;
  }
}

function toggleActionFilter(next: WordAction) {
  actionFilter.value = actionFilter.value === next ? '' : next;
  load();
}

function toggleOnlyEnabled() {
  onlyEnabled.value = !onlyEnabled.value;
  load();
}

function clearFilters() {
  keyword.value = '';
  actionFilter.value = '';
  onlyEnabled.value = false;
  load(); // 服务端筛选：清了必须重新拉（状态复位 ≠ 数据复位）
}

const filtering = computed(
  () => keyword.value.trim() !== '' || actionFilter.value !== '' || onlyEnabled.value,
);

async function doAdd() {
  const word = newWord.value.trim();
  if (!word) {
    addError.value = '请填写敏感词';
    return;
  }
  adding.value = true;
  addError.value = '';
  try {
    const created = await createWord({
      word,
      action: newAction.value,
      category: newCategory.value.trim(),
      enabled: true,
    });
    // 就地插入，避免整表重拉（列表按命中次数降序，新词 0 次落在末尾附近）
    words.value = [...words.value.filter((w) => w.id !== created.id), created];
    wordTotal.value += 1;
    newWord.value = '';
    newCategory.value = '';
    showToast(`已添加「${created.word}」（${actionLabel(created.action)}），立即生效`);
  } catch (e) {
    addError.value = message(e, '添加失败，请稍后重试');
  } finally {
    adding.value = false;
  }
}

async function toggleEnabled(w: SensitiveWordItem) {
  try {
    const updated = await updateWord(w.id, { enabled: !w.enabled });
    const i = words.value.findIndex((x) => x.id === updated.id);
    if (i >= 0) words.value[i] = updated;
    showToast(updated.enabled ? `已启用「${updated.word}」` : `已停用「${updated.word}」`);
  } catch (e) {
    error.value = message(e, '更新失败');
  }
}

const confirmingDelete = ref<number | null>(null);

async function doDelete(w: SensitiveWordItem) {
  try {
    await deleteWord(w.id);
    words.value = words.value.filter((x) => x.id !== w.id);
    wordTotal.value = Math.max(0, wordTotal.value - 1);
    confirmingDelete.value = null;
    showToast(`已删除「${w.word}」`);
  } catch (e) {
    error.value = message(e, '删除失败');
  }
}

const reloading = ref(false);
async function doReload() {
  reloading.value = true;
  try {
    const count = await reloadWords();
    showToast(`已重载词库索引（${count} 条生效）`);
  } catch (e) {
    error.value = message(e, '重载失败');
  } finally {
    reloading.value = false;
  }
}

// ------------------------------ 命中日志 ------------------------------

const hits = ref<SensitiveHitItem[]>([]);
const hitTotal = ref(0);
const hitHasMore = ref(false);
const hitCursor = ref<number | null>(null);
const hitLoading = ref(false);
const hitField = ref<'content' | 'name' | ''>('');

async function loadHits(more = false) {
  if (!user.isAdmin) return;
  hitLoading.value = true;
  try {
    const page = await fetchModerationHits({
      field: hitField.value,
      limit: HIT_PAGE_SIZE,
      cursor: more ? hitCursor.value : null,
    });
    hits.value = more ? [...hits.value, ...page.list] : page.list;
    hitTotal.value = page.total;
    hitHasMore.value = page.hasMore;
    hitCursor.value = page.nextCursor;
  } catch (e) {
    error.value = message(e, '加载命中日志失败');
  } finally {
    hitLoading.value = false;
  }
}

function toggleHitField(next: 'content' | 'name') {
  hitField.value = hitField.value === next ? '' : next;
  loadHits();
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

onMounted(() => {
  load();
  loadHits();
});
</script>

<template>
  <div class="admin">
    <AppNav variant="title" title="敏感词库" show-back @back="router.back()" />

    <p v-if="toast" class="admin__toast" role="status">{{ toast }}</p>

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址 / 登录态过期 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限管理敏感词"
      description="该功能仅对管理员开放。如需开通，请让现有管理员在「管理员管理」中加入你的手机号，或把账号加入后端 ADMIN_PHONES 白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <p class="admin__intro">
        评论提交时在服务端检测正文（可选昵称）。<b>拦截</b>直接拒绝提交，
        <b>脱敏</b>替换为 * 后发布并置为待复核，<b>白名单</b>用于豁免误杀的片段。
        匹配前会做归一化——大小写、全半角、空格标点与零宽字符干扰均不影响判定。
      </p>

      <!-- 新增 -->
      <div class="admin__form">
        <input
          v-model="newWord"
          type="text"
          class="admin__input"
          placeholder="输入敏感词（≥2 个有效字符）"
          aria-label="敏感词"
          maxlength="100"
          @keyup.enter="doAdd"
        />
        <select v-model="newAction" class="admin__select" aria-label="命中后的处理方式">
          <option v-for="a in ACTIONS" :key="a.value" :value="a.value">{{ a.label }}</option>
        </select>
        <input
          v-model="newCategory"
          type="text"
          class="admin__input admin__input--narrow"
          placeholder="分组（选填）"
          aria-label="分组"
          maxlength="30"
          @keyup.enter="doAdd"
        />
        <Button
          variant="primary"
          size="md"
          :label="adding ? '添加中…' : '添加'"
          :disabled="adding"
          @click="doAdd"
        />
      </div>
      <p class="admin__form-hint">{{ ACTIONS.find((a) => a.value === newAction)?.hint }}</p>
      <p v-if="addError" class="admin__form-error">{{ addError }}</p>

      <!-- 筛选 -->
      <div class="admin__toolbar">
        <input
          v-model="keyword"
          type="search"
          class="admin__search"
          placeholder="搜索敏感词"
          aria-label="搜索敏感词"
          @keyup.enter="load"
        />
        <button type="button" class="admin__sellink" @click="load">搜索</button>
        <button v-if="filtering" type="button" class="admin__sellink" @click="clearFilters">
          清除筛选
        </button>
      </div>
      <div class="admin__filters" role="group" aria-label="按动作筛选">
        <button
          v-for="a in ACTIONS"
          :key="a.value"
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': actionFilter === a.value }"
          :aria-pressed="actionFilter === a.value"
          @click="toggleActionFilter(a.value)"
        >
          {{ a.label }}
        </button>
        <button
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': onlyEnabled }"
          :aria-pressed="onlyEnabled"
          @click="toggleOnlyEnabled"
        >
          仅看已启用
        </button>
      </div>

      <div class="admin__actions">
        <Button
          variant="secondary"
          :label="reloading ? '重载中…' : '重载索引'"
          :disabled="reloading"
          @click="doReload"
        />
        <Button variant="secondary" label="评论治理" @click="router.push('/admin/comments')" />
        <Button variant="secondary" label="资讯审核" @click="router.push('/admin/news')" />
        <Button variant="secondary" label="审计" @click="router.push('/admin/audit')" />
      </div>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!words.length && !filtering" class="admin__hint">
        词库还是空的。没有词就不会拦截任何评论——评论目前完全靠人工治理。
      </p>
      <p v-else-if="!words.length" class="admin__hint">
        没有匹配的词。
        <button type="button" class="admin__sellink" @click="clearFilters">清除筛选</button>
      </p>

      <template v-else>
        <p class="admin__count">共 {{ wordTotal }} 条</p>
        <ul class="admin__list">
          <li
            v-for="w in words"
            :key="w.id"
            class="admin__item"
            :class="{ 'is-off': !w.enabled }"
            :data-word="w.word"
          >
            <div class="admin__item-head">
              <span class="admin__name" :title="w.word">{{ w.word }}</span>
              <span
                class="admin__chip"
                :class="`admin__chip--${w.action}`"
                :title="ACTIONS.find((a) => a.value === w.action)?.hint"
              >
                {{ actionLabel(w.action) }}
              </span>
              <span v-if="w.category" class="admin__chip">{{ w.category }}</span>
              <span v-if="!w.enabled" class="admin__chip admin__chip--hidden">已停用</span>
            </div>
            <p class="admin__meta">
              <span>{{ w.hitCount }} 次命中</span>
              <span v-if="w.createdBy">由 {{ w.createdBy }} 添加</span>
            </p>
            <div class="admin__item-actions">
              <button type="button" @click="toggleEnabled(w)">
                {{ w.enabled ? '停用' : '启用' }}
              </button>
              <button
                v-if="confirmingDelete !== w.id"
                type="button"
                class="is-danger"
                @click="confirmingDelete = w.id"
              >
                删除
              </button>
              <template v-else>
                <button type="button" class="is-danger" @click="doDelete(w)">确认删除</button>
                <button type="button" @click="confirmingDelete = null">取消</button>
              </template>
            </div>
          </li>
        </ul>
      </template>

      <!-- 命中日志：误杀复盘与申诉的唯一证据 -->
      <h2 class="admin__section">命中日志</h2>
      <p class="admin__intro">
        脱敏不可逆——正文里被替换掉的原文只在这里留着。判断「是不是误杀」、
        受理申诉都靠这些行（记录只读，不提供删除）。
      </p>
      <div class="admin__filters" role="group" aria-label="按检测字段筛选命中日志">
        <button
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': hitField === 'content' }"
          :aria-pressed="hitField === 'content'"
          @click="toggleHitField('content')"
        >
          正文
        </button>
        <button
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': hitField === 'name' }"
          :aria-pressed="hitField === 'name'"
          @click="toggleHitField('name')"
        >
          昵称
        </button>
      </div>

      <p v-if="hitLoading && !hits.length" class="admin__hint">加载中…</p>
      <p v-else-if="!hits.length" class="admin__hint">还没有命中记录。</p>
      <template v-else>
        <p class="admin__count">共 {{ hitTotal }} 条</p>
        <ul class="admin__list">
          <li v-for="h in hits" :key="h.id" class="admin__item" :data-hit-word="h.word">
            <div class="admin__item-head">
              <span class="admin__name">命中「{{ h.word }}」</span>
              <span class="admin__chip" :class="`admin__chip--${h.action}`">
                {{ h.action === 'block' ? '已拦截' : '已脱敏' }}
              </span>
              <span class="admin__chip">{{ h.field === 'name' ? '昵称' : '正文' }}</span>
            </div>
            <p class="admin__content" :title="h.snippet">{{ h.snippet }}</p>
            <p class="admin__meta">
              <span v-if="h.userName">{{ h.userName }}</span>
              <span v-if="h.createdAt">{{ formatTime(h.createdAt) }}</span>
              <span v-if="h.commentId">评论 {{ h.commentId }}</span>
              <span v-else class="admin__hint-inline">未落库</span>
            </p>
          </li>
        </ul>
        <div v-if="hitHasMore" class="admin__more">
          <Button
            variant="secondary"
            size="md"
            :label="hitLoading ? '加载中…' : '加载更多'"
            :disabled="hitLoading"
            @click="loadHits(true)"
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

.admin__section {
  margin: 24px 16px 4px;
  color: var(--color-text-primary);
  font-size: 16px;
  font-weight: 600;
}

.admin__form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 16px 0;
}

.admin__input {
  flex: 1 1 140px;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--color-divider);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
}

.admin__input--narrow {
  flex: 0 1 110px;
}

.admin__select {
  padding: 8px 10px;
  border: 1px solid var(--color-divider);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
}

.admin__form-hint {
  padding: 6px 16px 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.admin__form-error {
  padding: 6px 16px 0;
  color: #d93025;
  font-size: 12px;
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

.admin__hint {
  padding: 16px;
  color: var(--color-text-secondary);
  font-size: 13px;
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

.admin__item.is-off {
  opacity: 0.55;
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

.admin__chip--block {
  background: #fdecea;
  color: #d93025;
}

.admin__chip--mask {
  background: #fff4e5;
  color: #b06000;
}

.admin__chip--allow {
  background: #e8f5e9;
  color: #1e8e3e;
}

.admin__chip--hidden {
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
}

.admin__content {
  margin: 8px 0 0;
  color: var(--color-text-primary);
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}

.admin__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.admin__hint-inline {
  color: var(--color-text-secondary);
}

.admin__item-actions {
  display: flex;
  flex-wrap: wrap;
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

.admin__item-actions button.is-danger {
  border-color: #d93025;
  color: #d93025;
}

.admin__more {
  display: flex;
  justify-content: center;
  padding: 12px 16px 0;
}
</style>
