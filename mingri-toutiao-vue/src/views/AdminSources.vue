<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { CHANNELS, channelLabel, type Channel } from '@/constants/channels';
import { createSource, deleteSource, fetchSources, resetSources, updateSource } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import type { RssSourceItem } from '@/types';

const router = useRouter();
const user = useUserStore();

const list = ref<RssSourceItem[]>([]);
const loading = ref(false);
const error = ref('');
/** 403：当前账号不在白名单内（入口被隐藏后仍可能手敲网址到达） */
const denied = ref(false);

type FormMode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; key: string };
const formMode = ref<FormMode>({ kind: 'closed' });
const draft = reactive({
  key: '',
  name: '',
  channel: 'tech' as Channel,
  url: '',
  weight: 5,
  enabled: true,
});
const formError = ref('');
const saving = ref(false);

const pendingDelete = ref<RssSourceItem | null>(null);
const pendingReset = ref(false);
const busyKey = ref('');
let toastTimer: ReturnType<typeof setTimeout> | undefined;
const toast = ref('');

function showToast(msg: string) {
  toast.value = msg;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.value = '';
  }, 3000);
}

// ------------------------------ 筛选（P1-13） ------------------------------

type StatusFilter = 'all' | 'enabled' | 'disabled' | 'error' | 'none';
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'enabled', label: '启用中' },
  { value: 'disabled', label: '已停用' },
  { value: 'error', label: '异常' },
  { value: 'none', label: '未抓取' },
];

const keyword = ref('');
const channelFilter = ref<'all' | Channel>('all');
const statusFilter = ref<StatusFilter>('all');

const channelChips = computed(() => [
  { value: 'all' as const, label: '全部' },
  ...CHANNELS.map((c) => ({ value: c.key, label: c.label })),
]);

/** 汇总计数：源多了之后「几个异常 / 几个没抓过」是首要排障信息 */
const counts = computed(() => {
  let enabled = 0;
  let errorCount = 0;
  let none = 0;
  for (const s of list.value) {
    if (s.enabled) enabled++;
    if (s.lastStatus === 'error') errorCount++;
    if (s.lastStatus === null) none++;
  }
  return { total: list.value.length, enabled, error: errorCount, none };
});

const filtering = computed(
  () =>
    keyword.value.trim() !== '' || channelFilter.value !== 'all' || statusFilter.value !== 'all',
);

function clearFilters() {
  keyword.value = '';
  channelFilter.value = 'all';
  statusFilter.value = 'all';
}

/** 汇总数字即筛选入口：再点一次取消 */
function toggleStatusFilter(next: StatusFilter) {
  statusFilter.value = statusFilter.value === next ? 'all' : next;
}

/**
 * 可见列表 = 筛选结果 + **异常置顶**。
 * 稳定排序：组内保持后端返回的权重降序，只把异常源提到最前——
 * 「哪个源挂了」应该是打开页面第一眼看到的信息。
 */
const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  const hit = list.value.filter((s) => {
    if (channelFilter.value !== 'all' && s.channel !== channelFilter.value) return false;
    if (statusFilter.value === 'enabled' && !s.enabled) return false;
    if (statusFilter.value === 'disabled' && s.enabled) return false;
    if (statusFilter.value === 'error' && s.lastStatus !== 'error') return false;
    if (statusFilter.value === 'none' && s.lastStatus !== null) return false;
    if (kw && !`${s.name} ${s.url} ${s.key}`.toLowerCase().includes(kw)) return false;
    return true;
  });
  return hit.sort((a, b) => Number(a.lastStatus !== 'error') - Number(b.lastStatus !== 'error'));
});

// ------------------------------ 批量操作（P1-13） ------------------------------

const selecting = ref(false);
const selected = ref<Set<string>>(new Set());
const batchBusy = ref(false);
const pendingBatchDelete = ref(false);

function toggleSelectMode() {
  selecting.value = !selecting.value;
  selected.value = new Set();
  closeForm(); // 批量模式下不保留内联编辑，避免两种交互叠在一起
}

function isSelected(key: string): boolean {
  return selected.value.has(key);
}

function toggleSelect(key: string) {
  const next = new Set(selected.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  selected.value = next;
}

/** 全选只作用于**当前可见**项——筛选后的批量才是符合直觉的批量 */
const allVisibleSelected = computed(
  () => filtered.value.length > 0 && filtered.value.every((s) => selected.value.has(s.key)),
);

function toggleSelectAll() {
  const next = new Set(selected.value);
  if (allVisibleSelected.value) filtered.value.forEach((s) => next.delete(s.key));
  else filtered.value.forEach((s) => next.add(s.key));
  selected.value = next;
}

/**
 * 批量启停：先本地乐观翻转，再逐个调用；失败项还原并汇总报错。
 * 逐个（而非 Promise.all）是为了避免并发打满后端，几十个源的量级串行也够快。
 */
async function batchSetEnabled(next: boolean) {
  const keys = [...selected.value];
  if (!keys.length || batchBusy.value) return;
  batchBusy.value = true;
  const backups = new Map<string, boolean>();
  for (const k of keys) {
    const s = list.value.find((x) => x.key === k);
    if (s) {
      backups.set(k, s.enabled);
      s.enabled = next;
    }
  }
  const failed: string[] = [];
  for (const k of keys) {
    try {
      await updateSource(k, { enabled: next });
    } catch {
      failed.push(k);
    }
  }
  for (const k of failed) {
    const s = list.value.find((x) => x.key === k);
    if (s && backups.has(k)) s.enabled = backups.get(k)!;
  }
  batchBusy.value = false;
  selected.value = new Set();
  if (failed.length === 0) showToast(`已${next ? '启用' : '停用'} ${keys.length} 个源`);
  else error.value = `${failed.length} 个源操作失败，已还原，请重试`;
}

async function doBatchDelete() {
  const keys = [...selected.value];
  if (!keys.length || batchBusy.value) return;
  batchBusy.value = true;
  const failed: string[] = [];
  for (const k of keys) {
    try {
      await deleteSource(k);
      list.value = list.value.filter((s) => s.key !== k); // 删除的本地真相 = 服务端真相
    } catch {
      failed.push(k);
    }
  }
  batchBusy.value = false;
  pendingBatchDelete.value = false;
  selected.value = new Set();
  if (failed.length === 0) showToast(`已删除 ${keys.length} 个源`);
  else error.value = `${failed.length} 个源删除失败，请重试`;
}

/**
 * 是否不具备访问资格。
 * 除了接口真的返回 403，**未登录 / 非管理员**也要一眼看出问题——
 * 否则页面会退化成"还没有配置任何源"，把权限问题伪装成数据问题。
 */
const noAccess = computed(() => !user.isLoggedIn || !user.isAdmin || denied.value);

function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

async function load() {
  if (!user.isAdmin) return;
  loading.value = true;
  error.value = '';
  denied.value = false;
  try {
    const rows = await fetchSources();
    list.value = rows;
    // 选择态去枯：刚拉到的列表里已不存在的 key 不再算选中（可能被他人删除）
    if (selected.value.size) {
      const alive = new Set(rows.map((r) => r.key));
      selected.value = new Set([...selected.value].filter((k) => alive.has(k)));
    }
  } catch (e) {
    const msg = message(e, '加载源列表失败');
    if (/403|无权限/.test(msg)) denied.value = true;
    else error.value = msg;
  } finally {
    loading.value = false;
  }
}

onMounted(load);

// ------------------------------ 表单 ------------------------------

function openCreate() {
  Object.assign(draft, { key: '', name: '', channel: 'tech', url: '', weight: 5, enabled: true });
  formError.value = '';
  formMode.value = { kind: 'create' };
}

function openEdit(s: RssSourceItem) {
  Object.assign(draft, {
    key: s.key,
    name: s.name,
    channel: s.channel,
    url: s.url,
    weight: s.weight,
    enabled: s.enabled,
  });
  formError.value = '';
  formMode.value = { kind: 'edit', key: s.key };
}

function closeForm() {
  formMode.value = { kind: 'closed' };
  formError.value = '';
}

/** 前端预校验：与后端规则一致，但**不能**替代后端校验（这里只为少跑一轮无效请求） */
function validateDraft(): string {
  if (formMode.value.kind === 'create' && !/^[a-z0-9_]+$/.test(draft.key)) {
    return '标识只能用英文小写字母、数字和下划线';
  }
  if (!draft.name.trim()) return '请填写源名称';
  if (!/^https?:\/\/\S+$/i.test(draft.url.trim()))
    return '源地址必须是以 http/https 开头的完整地址';
  if (!Number.isInteger(draft.weight) || draft.weight < 0 || draft.weight > 100) {
    return '权重需为 0–100 的整数';
  }
  return '';
}

async function submitForm() {
  formError.value = '';
  const invalid = validateDraft();
  if (invalid) {
    formError.value = invalid;
    return;
  }
  saving.value = true;
  try {
    if (formMode.value.kind === 'create') {
      await createSource({
        key: draft.key,
        name: draft.name.trim(),
        channel: draft.channel,
        url: draft.url.trim(),
        weight: draft.weight,
        enabled: draft.enabled,
      });
      showToast(`已新增「${draft.name.trim()}」`);
    } else {
      const key = formMode.value.kind === 'edit' ? formMode.value.key : draft.key;
      await updateSource(key, {
        name: draft.name.trim(),
        channel: draft.channel,
        url: draft.url.trim(),
        weight: draft.weight,
        enabled: draft.enabled,
      });
      showToast(`已更新「${draft.name.trim()}」`);
    }
    closeForm();
    await load();
  } catch (e) {
    formError.value = message(e, '保存失败，请稍后重试');
  } finally {
    saving.value = false;
  }
}

// ------------------------------ 启停 / 删除 / 重置 ------------------------------

async function toggle(s: RssSourceItem) {
  const next = !s.enabled;
  s.enabled = next; // 乐观更新：开关是最常用的操作，不该等网络往返
  busyKey.value = s.key;
  try {
    await updateSource(s.key, { enabled: next });
  } catch (e) {
    s.enabled = !next; // 失败回滚，避免界面与服务端不一致
    error.value = message(e, '启停失败');
  } finally {
    busyKey.value = '';
  }
}

async function doDelete() {
  const target = pendingDelete.value;
  if (!target) return;
  busyKey.value = target.key;
  try {
    await deleteSource(target.key);
    list.value = list.value.filter((s) => s.key !== target.key);
    showToast(`已删除「${target.name}」`);
  } catch (e) {
    error.value = message(e, '删除失败');
  } finally {
    busyKey.value = '';
    pendingDelete.value = null;
  }
}

async function doReset() {
  busyKey.value = '__reset__';
  try {
    const seeded = await resetSources();
    showToast(`已重置为 ${seeded} 个内置源`);
    await load();
  } catch (e) {
    error.value = message(e, '重置失败');
  } finally {
    busyKey.value = '';
    pendingReset.value = false;
  }
}

// ------------------------------ 展示辅助 ------------------------------

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function statusText(s: RssSourceItem): string {
  if (s.lastStatus === 'ok') return '正常';
  if (s.lastStatus === 'error') return '异常';
  return '未抓取';
}
</script>

<template>
  <div class="admin">
    <AppNav variant="title" title="RSS 源管理" show-back @back="router.back()" />

    <p v-if="toast" class="admin__toast" role="status">{{ toast }}</p>

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址/登录态过期的情况 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限管理信源"
      description="该功能仅对管理员开放。如需开通，请让现有管理员在「管理员管理」中加入你的手机号，或把账号加入后端 ADMIN_PHONES 白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <!-- 汇总即筛入口：点数字看该状态的全部源，再点一次取消 -->
      <div class="admin__summary">
        <button
          type="button"
          class="admin__stat"
          :class="{ 'is-active': !filtering }"
          aria-label="显示全部源"
          @click="clearFilters"
        >
          <span class="admin__stat-num">{{ counts.total }}</span>
          <span class="admin__stat-label">个源</span>
        </button>
        <button
          type="button"
          class="admin__stat"
          :class="{ 'is-active': statusFilter === 'enabled' }"
          :aria-pressed="statusFilter === 'enabled'"
          @click="toggleStatusFilter('enabled')"
        >
          <span class="admin__stat-num">{{ counts.enabled }}</span>
          <span class="admin__stat-label">启用中</span>
        </button>
        <button
          type="button"
          class="admin__stat admin__stat--warn"
          :class="{ 'is-active': statusFilter === 'error' }"
          :aria-pressed="statusFilter === 'error'"
          @click="toggleStatusFilter('error')"
        >
          <span class="admin__stat-num">{{ counts.error }}</span>
          <span class="admin__stat-label">异常</span>
        </button>
        <button
          type="button"
          class="admin__stat"
          :class="{ 'is-active': statusFilter === 'none' }"
          :aria-pressed="statusFilter === 'none'"
          @click="toggleStatusFilter('none')"
        >
          <span class="admin__stat-num">{{ counts.none }}</span>
          <span class="admin__stat-label">未抓取</span>
        </button>
      </div>

      <!-- 搜索 + 状态筛选 -->
      <div class="admin__toolbar">
        <input
          v-model="keyword"
          type="search"
          class="admin__search"
          placeholder="搜索名称 / 地址 / 标识"
          aria-label="搜索源"
        />
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
          :class="{ 'is-active': statusFilter === st.value }"
          :aria-pressed="statusFilter === st.value"
          @click="statusFilter = st.value"
        >
          {{ st.label }}
        </button>
      </div>
      <div class="admin__filters" role="group" aria-label="按频道筛选">
        <button
          v-for="c in channelChips"
          :key="c.value"
          type="button"
          class="admin__chipbtn"
          :class="{ 'is-active': channelFilter === c.value }"
          :aria-pressed="channelFilter === c.value"
          @click="channelFilter = c.value"
        >
          {{ c.label }}
        </button>
      </div>

      <div class="admin__actions">
        <Button variant="primary" label="新增源" @click="openCreate" />
        <Button variant="secondary" label="恢复内置源" @click="pendingReset = true" />
        <Button
          variant="secondary"
          :label="selecting ? '退出批量' : '批量管理'"
          @click="toggleSelectMode"
        />
        <Button variant="secondary" label="管理员" @click="router.push('/admin/users')" />
        <Button variant="secondary" label="审计" @click="router.push('/admin/audit')" />
      </div>

      <!-- 批量模式选择条 -->
      <div v-if="selecting" class="admin__selbar">
        <button type="button" class="admin__sellink" @click="toggleSelectAll">
          {{ allVisibleSelected ? '取消全选' : '全选' }}
        </button>
        <span class="admin__selcount">已选 {{ selected.size }} 项</span>
        <button type="button" class="admin__sellink" @click="toggleSelectMode">完成</button>
      </div>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!list.length" class="admin__hint">还没有配置任何源。</p>
      <p v-else-if="!filtered.length" class="admin__hint">
        没有匹配的源。
        <button type="button" class="admin__sellink" @click="clearFilters">清除筛选</button>
      </p>

      <ul v-else class="admin__list">
        <li
          v-for="s in filtered"
          :key="s.key"
          class="admin__item"
          :class="{
            'is-off': !s.enabled,
            'is-selected': isSelected(s.key),
          }"
          @click="selecting && toggleSelect(s.key)"
        >
          <label v-if="selecting" class="admin__pick" @click.stop>
            <input
              type="checkbox"
              :checked="isSelected(s.key)"
              :aria-label="`选择 ${s.name}`"
              @change="toggleSelect(s.key)"
            />
          </label>

          <div class="admin__item-head">
            <span class="admin__name">{{ s.name }}</span>
            <span class="admin__chip">{{ channelLabel(s.channel) }}</span>
            <span class="admin__weight">权重 {{ s.weight }}</span>
            <button
              v-if="!selecting"
              type="button"
              class="admin__switch"
              role="switch"
              :aria-checked="s.enabled"
              :aria-label="`${s.name} 启用状态`"
              :disabled="busyKey === s.key"
              @click="toggle(s)"
            >
              <span class="admin__knob" />
            </button>
          </div>

          <p class="admin__url" :title="s.url">{{ s.url }}</p>

          <div class="admin__meta">
            <span class="admin__status" :class="`is-${s.lastStatus ?? 'none'}`">
              {{ statusText(s) }}
            </span>
            <span v-if="s.lastItemCount !== null">最近 {{ s.lastItemCount }} 条</span>
            <span v-if="s.lastFetchedAt">{{ formatTime(s.lastFetchedAt) }}</span>
            <code class="admin__key">{{ s.key }}</code>
          </div>

          <p v-if="s.lastError" class="admin__last-error" :title="s.lastError">
            {{ s.lastError }}
          </p>

          <div v-if="!selecting" class="admin__item-actions">
            <button type="button" @click="openEdit(s)">编辑</button>
            <!-- 「这个源被谁动过」：带 target 进审计页，只看该源的历史 -->
            <button
              type="button"
              :aria-label="`查看 ${s.name} 的变更历史`"
              @click="router.push(`/admin/audit?target=${s.key}`)"
            >
              历史
            </button>
            <button type="button" class="is-danger" @click="pendingDelete = s">删除</button>
          </div>

          <!-- 内联编辑：就地展开，避免为一个小表单引入弹层 -->
          <form
            v-if="!selecting && formMode.kind === 'edit' && formMode.key === s.key"
            class="admin__form"
            @submit.prevent="submitForm"
          >
            <label class="admin__field">
              <span>名称</span>
              <input v-model="draft.name" maxlength="100" placeholder="源名称" />
            </label>
            <label class="admin__field">
              <span>频道</span>
              <select v-model="draft.channel">
                <option v-for="c in CHANNELS" :key="c.key" :value="c.key">{{ c.label }}</option>
              </select>
            </label>
            <label class="admin__field">
              <span>地址</span>
              <input
                v-model="draft.url"
                maxlength="500"
                placeholder="https://example.com/feed.xml"
              />
            </label>
            <label class="admin__field admin__field--num">
              <span>权重（0–100）</span>
              <input v-model.number="draft.weight" type="number" min="0" max="100" step="1" />
            </label>
            <label class="admin__check">
              <input v-model="draft.enabled" type="checkbox" />
              <span>启用该源</span>
            </label>
            <p v-if="formError" class="admin__form-error">{{ formError }}</p>
            <div class="admin__form-actions">
              <Button
                variant="primary"
                size="md"
                :label="saving ? '保存中…' : '保存'"
                type="submit"
                :disabled="saving"
              />
              <Button variant="secondary" size="md" label="取消" @click="closeForm" />
            </div>
          </form>
        </li>
      </ul>

      <!-- 新增表单 -->
      <form
        v-if="formMode.kind === 'create'"
        class="admin__form admin__form--new"
        @submit.prevent="submitForm"
      >
        <h2 class="admin__form-title">新增源</h2>
        <label class="admin__field">
          <span>标识（key）</span>
          <input v-model="draft.key" maxlength="64" placeholder="小写字母/数字/下划线" />
        </label>
        <label class="admin__field">
          <span>名称</span>
          <input v-model="draft.name" maxlength="100" placeholder="源名称" />
        </label>
        <label class="admin__field">
          <span>频道</span>
          <select v-model="draft.channel">
            <option v-for="c in CHANNELS" :key="c.key" :value="c.key">{{ c.label }}</option>
          </select>
        </label>
        <label class="admin__field">
          <span>地址</span>
          <input v-model="draft.url" maxlength="500" placeholder="https://example.com/feed.xml" />
        </label>
        <label class="admin__field admin__field--num">
          <span>权重（0–100）</span>
          <input v-model.number="draft.weight" type="number" min="0" max="100" step="1" />
        </label>
        <label class="admin__check">
          <input v-model="draft.enabled" type="checkbox" />
          <span>启用该源</span>
        </label>
        <p class="admin__form-tip">
          权重越高，多源转载同一稿件时越先入库。停用优于删除——删掉后健康状态一并丢失。
        </p>
        <p v-if="formError" class="admin__form-error">{{ formError }}</p>
        <div class="admin__form-actions">
          <Button
            variant="primary"
            size="md"
            :label="saving ? '保存中…' : '新增'"
            type="submit"
            :disabled="saving"
          />
          <Button variant="secondary" size="md" label="取消" @click="closeForm" />
        </div>
      </form>

      <!-- 批量操作条：sticky 底部，长列表也能一键可达 -->
      <div v-if="selecting" class="admin__batchbar" role="group" aria-label="批量操作">
        <button
          type="button"
          :disabled="!selected.size || batchBusy"
          @click="batchSetEnabled(true)"
        >
          批量启用
        </button>
        <button
          type="button"
          :disabled="!selected.size || batchBusy"
          @click="batchSetEnabled(false)"
        >
          批量停用
        </button>
        <button
          type="button"
          class="is-danger"
          :disabled="!selected.size || batchBusy"
          @click="pendingBatchDelete = true"
        >
          批量删除
        </button>
      </div>

      <!-- 删除确认 -->
      <div v-if="pendingDelete" class="admin__confirm" role="alertdialog" aria-label="确认删除">
        <p>删除「{{ pendingDelete.name }}」？删除后其抓取健康状态也会一并丢失。</p>
        <div class="admin__confirm-actions">
          <Button
            variant="primary"
            size="md"
            label="确认删除"
            :disabled="busyKey === pendingDelete.key"
            @click="doDelete"
          />
          <Button variant="secondary" size="md" label="取消" @click="pendingDelete = null" />
        </div>
      </div>

      <!-- 批量删除确认 -->
      <div
        v-if="pendingBatchDelete"
        class="admin__confirm"
        role="alertdialog"
        aria-label="确认批量删除"
      >
        <p>删除选中的 {{ selected.size }} 个源？删除后其抓取健康状态也会一并丢失。</p>
        <div class="admin__confirm-actions">
          <Button
            variant="primary"
            size="md"
            :label="batchBusy ? '删除中…' : '确认删除'"
            :disabled="batchBusy"
            @click="doBatchDelete"
          />
          <Button variant="secondary" size="md" label="取消" @click="pendingBatchDelete = false" />
        </div>
      </div>

      <!-- 重置确认 -->
      <div v-if="pendingReset" class="admin__confirm" role="alertdialog" aria-label="确认重置">
        <p>恢复为内置源？当前所有自定义源将被删除，此操作不可撤销。</p>
        <div class="admin__confirm-actions">
          <Button
            variant="primary"
            size="md"
            label="确认重置"
            :disabled="busyKey === '__reset__'"
            @click="doReset"
          />
          <Button variant="secondary" size="md" label="取消" @click="pendingReset = false" />
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

/* 汇总：四个计数即筛选入口 */
.admin__summary {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 14px 16px 4px;
}

.admin__stat {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 8px 4px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-card);
  cursor: pointer;
}

.admin__stat.is-active {
  border-color: var(--color-primary);
}

.admin__stat-num {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text-primary);
  font-family: var(--font-num);
}

.admin__stat-label {
  font-size: 11px;
  color: var(--color-text-secondary);
}

.admin__stat--warn .admin__stat-num,
.admin__stat--warn .admin__stat-label {
  color: #d93025;
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

.admin__item.is-off {
  opacity: 0.62;
}

.admin__item.is-selected {
  outline: 2px solid var(--color-primary);
  outline-offset: -2px;
}

.admin__pick {
  display: flex;
  padding-bottom: 8px;
}

.admin__pick input {
  width: 18px;
  height: 18px;
  accent-color: var(--color-primary);
  cursor: pointer;
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
}

.admin__chip,
.admin__weight {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  font-size: 11px;
  white-space: nowrap;
}

.admin__switch {
  margin-left: auto;
  width: 42px;
  height: 24px;
  padding: 2px;
  border: none;
  border-radius: 12px;
  background: var(--color-divider);
  cursor: pointer;
  transition: background 0.15s ease;
}

.admin__switch[aria-checked='true'] {
  background: var(--color-primary);
}

.admin__switch:disabled {
  opacity: 0.5;
  cursor: default;
}

.admin__knob {
  display: block;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.15s ease;
}

.admin__switch[aria-checked='true'] .admin__knob {
  transform: translateX(18px);
}

.admin__url {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  word-break: break-all;
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

.admin__status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.admin__status::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.admin__status.is-ok {
  color: #1a9c50;
}

.admin__status.is-error {
  color: #d93025;
}

.admin__status.is-none {
  color: var(--color-text-secondary);
}

.admin__key {
  margin-left: auto;
  font-size: 10px;
  opacity: 0.7;
}

.admin__last-error {
  margin: 6px 0 0;
  font-size: 11px;
  color: #d93025;
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

.admin__item-actions .is-danger {
  color: #d93025;
}

/* 批量模式：选择条 + 底部操作条 */
.admin__selbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 16px;
}

.admin__selcount {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.admin__sellink {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.admin__batchbar {
  position: sticky;
  bottom: 0;
  display: flex;
  gap: 10px;
  margin: 16px;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-card);
}

.admin__batchbar button {
  flex: 1;
  padding: 10px 0;
  border: 1px solid var(--color-primary);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-primary);
  font-size: 13px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.admin__batchbar button.is-danger {
  border-color: #d93025;
  color: #d93025;
}

.admin__batchbar button:disabled {
  border-color: var(--color-border);
  background: var(--color-border);
  color: var(--color-text-secondary);
  cursor: not-allowed;
}

.admin__form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 12px 0 0;
  padding: 12px;
  border-radius: 10px;
  background: var(--color-page-bg);
}

.admin__form--new {
  margin: 12px 16px 0;
}

.admin__form-title {
  margin: 0;
  font-size: 15px;
  color: var(--color-text-primary);
}

.admin__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.admin__field input,
.admin__field select {
  padding: 8px 10px;
  border: 1px solid var(--color-divider);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
}

.admin__field--num input {
  max-width: 120px;
}

.admin__check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-primary);
}

.admin__form-tip {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.admin__form-error {
  margin: 0;
  font-size: 12px;
  color: #d93025;
}

.admin__form-actions {
  display: flex;
  gap: 10px;
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
</style>
