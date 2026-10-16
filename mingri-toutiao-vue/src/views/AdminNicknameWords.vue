<script setup lang="ts">
/**
 * 昵称敏感词库维护 + 命中日志（P1-24）。
 *
 * 这一页要回答四件事：
 * 1. **机器现在在拦什么昵称**（词库——注册与改名共用同一条校验链）；
 * 2. **改完立刻生效吗**（保存即失效本进程缓存，集群下用「重载索引」同步其他实例）；
 * 3. **有没有误杀**（命中日志——昵称整体就是上下文，它是申诉与复盘的唯一证据）；
 * 4. **哪些提交在等人看**（入口指向复核队列，两个页面是同一件事的两半）。
 *
 * 与 P1-23 评论词库的两处关键差别：
 * - 动作集是 block/review/allow，**没有 mask**——昵称是账号级属性，
 *   「脱敏后发布」等于改掉用户自己起的名字，而用户此刻改不了；
 * - 词条带**别名**（拼音全拼/首字母/谐音/变形）：「shabi」「sb」这类字母写法
 *   只有配了别名才拦得住，同音汉字替换没有字符级算法可解，靠运营补。
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import {
  createNicknameWord,
  deleteNicknameWord,
  fetchNicknameHits,
  fetchNicknameWords,
  reloadNicknameWords,
  updateNicknameWord,
} from '@/api/admin';
import { useUserStore } from '@/stores/user';
import type { NicknameHitItem, NicknameWordAction, NicknameWordItem } from '@/types';

const router = useRouter();
const user = useUserStore();

const WORD_LIMIT = 200;
const HIT_PAGE_SIZE = 20;

const ACTIONS: { value: NicknameWordAction; label: string; hint: string }[] = [
  { value: 'block', label: '拦截', hint: '命中即拒绝设置（提示不回显命中词）' },
  { value: 'review', label: '复核', hint: '放行但置为待人工复核——机器的判断是概率性的' },
  { value: 'allow', label: '白名单', hint: '命中它的片段豁免——降误杀用' },
];

function actionLabel(a: NicknameWordAction): string {
  return ACTIONS.find((x) => x.value === a)?.label ?? a;
}

// ------------------------------ 词库 ------------------------------

const words = ref<NicknameWordItem[]>([]);
const wordTotal = ref(0);
const loading = ref(false);
const error = ref('');
const denied = ref(false);

const keyword = ref('');
const actionFilter = ref<NicknameWordAction | ''>('');
const onlyEnabled = ref(false);

const noAccess = computed(() => !user.isLoggedIn || !user.isAdmin || denied.value);

// 新增表单
const newWord = ref('');
const newAction = ref<NicknameWordAction>('review');
const newCategory = ref('');
/** 别名输入框：逗号分隔（中英文逗号均可），保存时拆成数组 */
const newAliases = ref('');
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

function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** 拆分别名输入：逗号分隔、去空白、去重、丢空项（后端 alias_list 会再做一遍） */
function parseAliases(raw: string): string[] {
  const out: string[] = [];
  for (const part of raw.split(/[,，]/)) {
    const t = part.trim();
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

async function load() {
  if (!user.isAdmin) return;
  loading.value = true;
  error.value = '';
  denied.value = false;
  try {
    const page = await fetchNicknameWords({
      keyword: keyword.value.trim(),
      action: actionFilter.value,
      enabled: onlyEnabled.value ? true : undefined,
      limit: WORD_LIMIT,
    });
    words.value = page.list;
    wordTotal.value = page.total;
  } catch (e) {
    const msg = message(e, '加载词库失败');
    if (/403|无权限/.test(msg)) denied.value = true;
    else error.value = msg;
  } finally {
    loading.value = false;
  }
}

function toggleActionFilter(next: NicknameWordAction) {
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
    const created = await createNicknameWord({
      word,
      action: newAction.value,
      category: newCategory.value.trim(),
      aliases: parseAliases(newAliases.value),
      enabled: true,
    });
    // 就地插入，避免整表重拉（列表按命中次数降序，新词 0 次落在末尾附近）
    words.value = [...words.value.filter((w) => w.id !== created.id), created];
    wordTotal.value += 1;
    newWord.value = '';
    newCategory.value = '';
    newAliases.value = '';
    showToast(`已添加「${created.word}」（${actionLabel(created.action)}），立即生效`);
  } catch (e) {
    addError.value = message(e, '添加失败，请稍后重试');
  } finally {
    adding.value = false;
  }
}

async function toggleEnabled(w: NicknameWordItem) {
  try {
    const updated = await updateNicknameWord(w.id, { enabled: !w.enabled });
    const i = words.value.findIndex((x) => x.id === updated.id);
    if (i >= 0) words.value[i] = updated;
    showToast(updated.enabled ? `已启用「${updated.word}」` : `已停用「${updated.word}」`);
  } catch (e) {
    error.value = message(e, '更新失败');
  }
}

/**
 * 行内改动作：block ↔ review 是运营最常用的调校（拦狠了放一档、放过了拦一档）。
 * 服务端返回值是唯一真相，就地替换整行。
 */
async function changeAction(w: NicknameWordItem, next: NicknameWordAction) {
  if (next === w.action) return;
  try {
    const updated = await updateNicknameWord(w.id, { action: next });
    const i = words.value.findIndex((x) => x.id === updated.id);
    if (i >= 0) words.value[i] = updated;
    showToast(`「${updated.word}」已改为${actionLabel(updated.action)}`);
  } catch (e) {
    error.value = message(e, '更新失败');
  }
}

// 行内改别名：展开成输入框，保存走 PATCH（空数组 = 清空全部别名）
const editingId = ref<number | null>(null);
const aliasDraft = ref('');

function startEditAlias(w: NicknameWordItem) {
  editingId.value = w.id;
  aliasDraft.value = w.aliases.join(', ');
}

function cancelEditAlias() {
  editingId.value = null;
  aliasDraft.value = '';
}

async function saveAlias(w: NicknameWordItem) {
  const next = parseAliases(aliasDraft.value);
  // 与后端同口径：无变化不发请求（字段级 diff，没有新事实就不留痕）
  if (next.length === w.aliases.length && next.every((a, i) => a === w.aliases[i])) {
    cancelEditAlias();
    return;
  }
  try {
    const updated = await updateNicknameWord(w.id, { aliases: next });
    const i = words.value.findIndex((x) => x.id === updated.id);
    if (i >= 0) words.value[i] = updated;
    cancelEditAlias();
    showToast(`「${updated.word}」别名已更新`);
  } catch (e) {
    error.value = message(e, '更新别名失败');
  }
}

const confirmingDelete = ref<number | null>(null);

async function doDelete(w: NicknameWordItem) {
  try {
    await deleteNicknameWord(w.id);
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
    const count = await reloadNicknameWords();
    showToast(`已重载昵称词库索引（${count} 条生效）`);
  } catch (e) {
    error.value = message(e, '重载失败');
  } finally {
    reloading.value = false;
  }
}

// ------------------------------ 命中日志 ------------------------------

const hits = ref<NicknameHitItem[]>([]);
const hitTotal = ref(0);
const hitHasMore = ref(false);
const hitCursor = ref<number | null>(null);
const hitLoading = ref(false);
const hitAction = ref<'' | 'block' | 'review'>('');
const hitSource = ref<'' | 'register' | 'update'>('');

async function loadHits(more = false) {
  if (!user.isAdmin) return;
  hitLoading.value = true;
  try {
    const page = await fetchNicknameHits({
      action: hitAction.value,
      source: hitSource.value,
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

function toggleHitAction(next: 'block' | 'review') {
  hitAction.value = hitAction.value === next ? '' : next;
  loadHits();
}

function toggleHitSource(next: 'register' | 'update') {
  hitSource.value = hitSource.value === next ? '' : next;
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
    <AppNav variant="title" title="昵称敏感词" show-back @back="router.back()" />

    <p v-if="toast" class="admin__toast" role="status">{{ toast }}</p>

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址 / 登录态过期 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限管理昵称敏感词"
      description="该功能仅对管理员开放。如需开通，请让现有管理员在「管理员管理」中加入你的手机号，或把账号加入后端 ADMIN_PHONES 白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <p class="admin__intro">
        注册与修改昵称共用同一条校验链。<b>拦截</b>直接拒绝设置（提示<b>不回显命中词</b>），
        <b>复核</b>放行但置为待人工复核，<b>白名单</b>用于豁免误杀的片段。
        匹配前会做归一化——大小写、全半角、空格标点、重复字符与字母数字混淆均不影响判定；
        「shabi」这类字母写法靠<b>别名</b>拦截。
      </p>

      <!-- 新增 -->
      <div class="admin__form">
        <input
          v-model="newWord"
          type="text"
          class="admin__input"
          placeholder="敏感词（≥2 个有效字符）"
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
        <input
          v-model="newAliases"
          type="text"
          class="admin__input admin__input--wide"
          placeholder="别名（选填，逗号分隔：shabi, sb）"
          aria-label="别名"
          maxlength="300"
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
          placeholder="搜索敏感词（不含别名）"
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
        <Button
          variant="secondary"
          label="复核队列"
          @click="router.push('/admin/nickname-reviews')"
        />
        <Button variant="secondary" label="评论治理" @click="router.push('/admin/comments')" />
        <Button variant="secondary" label="审计" @click="router.push('/admin/audit')" />
      </div>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!words.length && !filtering" class="admin__hint">
        词库还是空的。没有词就不会拦截任何昵称——改名目前完全靠人工治理。
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

            <!-- 别名：同一个词的另一种写法，与主词走同一条归一化管道 -->
            <div v-if="w.aliases.length" class="admin__aliases">
              <span class="admin__aliases-label">别名</span>
              <span v-for="al in w.aliases" :key="al" class="admin__alias" :title="al">{{
                al
              }}</span>
            </div>
            <p v-else class="admin__aliases admin__aliases--empty">未配别名</p>

            <p class="admin__meta">
              <span>{{ w.hitCount }} 次命中</span>
              <span v-if="w.createdBy">由 {{ w.createdBy }} 添加</span>
            </p>

            <!-- 行内别名编辑：展开成输入框，空 = 清空全部别名 -->
            <div v-if="editingId === w.id" class="admin__alias-edit">
              <input
                v-model="aliasDraft"
                type="text"
                class="admin__input"
                placeholder="别名（逗号分隔；清空即全部删除）"
                aria-label="编辑别名"
                maxlength="300"
                @keyup.enter="saveAlias(w)"
              />
              <button type="button" class="admin__sellink" @click="saveAlias(w)">保存</button>
              <button type="button" class="admin__sellink" @click="cancelEditAlias">取消</button>
            </div>

            <div class="admin__item-actions">
              <button type="button" @click="toggleEnabled(w)">
                {{ w.enabled ? '停用' : '启用' }}
              </button>
              <button v-if="editingId !== w.id" type="button" @click="startEditAlias(w)">
                改别名
              </button>
              <!-- 行内改动作：block ↔ review 是最常用的调校 -->
              <select
                class="admin__action-select"
                aria-label="修改命中后的处理方式"
                :value="w.action"
                @change="
                  changeAction(w, ($event.target as HTMLSelectElement).value as NicknameWordAction)
                "
              >
                <option v-for="a in ACTIONS" :key="a.value" :value="a.value">
                  {{ a.label }}
                </option>
              </select>
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
        昵称整体就是上下文——这里留着每个被拦/待复核昵称的原文与实际命中的模式
        （主词或哪条别名）、从哪个入口来。记录只读，不提供删除。
      </p>
      <div class="admin__filters" role="group" aria-label="筛选命中日志">
        <button
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': hitAction === 'block', 'is-danger': hitAction === 'block' }"
          :aria-pressed="hitAction === 'block'"
          @click="toggleHitAction('block')"
        >
          已拦截
        </button>
        <button
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': hitAction === 'review' }"
          :aria-pressed="hitAction === 'review'"
          @click="toggleHitAction('review')"
        >
          待复核
        </button>
        <button
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': hitSource === 'register' }"
          :aria-pressed="hitSource === 'register'"
          @click="toggleHitSource('register')"
        >
          注册
        </button>
        <button
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': hitSource === 'update' }"
          :aria-pressed="hitSource === 'update'"
          @click="toggleHitSource('update')"
        >
          改名
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
                {{ h.action === 'block' ? '已拦截' : '待复核' }}
              </span>
              <span class="admin__chip">{{ h.source === 'register' ? '注册' : '改名' }}</span>
            </div>
            <p class="admin__content" :title="h.snippet">{{ h.snippet }}</p>
            <p class="admin__meta">
              <!-- 实际命中的模式 ≠ 主词时要点出来：这是「该补哪条别名」的依据 -->
              <span v-if="h.matched !== h.word" class="admin__matched">
                实际命中「{{ h.matched }}」
              </span>
              <span v-if="h.userName">{{ h.userName }}</span>
              <span v-if="h.createdAt">{{ formatTime(h.createdAt) }}</span>
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

.admin__input--wide {
  flex: 1 1 200px;
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

/* 「已拦截」筛选激活时用红色：它是处置态，不是普通分类 */
.admin__chipbtn.is-danger.is-active {
  border-color: #d93025;
  background: #d93025;
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

.admin__chip--review {
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

/* 别名区：主词的替身，一眼看出"这条词还管这些写法" */
.admin__aliases {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin: 6px 0 0;
}

.admin__aliases-label {
  color: var(--color-text-secondary);
  font-size: 11px;
}

.admin__aliases--empty {
  color: var(--color-text-secondary);
  font-size: 11px;
}

.admin__alias {
  padding: 1px 6px;
  border: 1px solid var(--color-divider);
  border-radius: 6px;
  color: var(--color-text-secondary);
  font-size: 11px;
  word-break: break-all;
}

.admin__alias-edit {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0 0;
}

.admin__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

/* 实际命中 ≠ 主词：这是"该补哪条别名"的依据，用警示色点出来 */
.admin__matched {
  color: #b06000;
}

.admin__content {
  margin: 8px 0 0;
  color: var(--color-text-primary);
  font-size: 13px;
  line-height: 1.6;
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

.admin__item-actions button.is-danger {
  border-color: #d93025;
  color: #d93025;
}

.admin__action-select {
  padding: 4px 8px;
  border: 1px solid var(--color-divider);
  border-radius: 999px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 12px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.admin__more {
  display: flex;
  justify-content: center;
  padding: 12px 16px 0;
}
</style>
