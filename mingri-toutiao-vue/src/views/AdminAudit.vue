<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { fetchAuditPage } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import { useAdminGuard, useAdminList } from '@/composables/useAdminPage';
import type { AdminAuditItem } from '@/types';

const router = useRouter();
const route = useRoute();
const user = useUserStore();
// D3：鉴权守卫抽到 composable
const { noAccess, handleError, resetDenied } = useAdminGuard();

// D3：列表状态机抽到 composable（游标分页；审计接口不返 total，故此处 total 为空）

const {
  list,
  hasMore,
  loading,
  loadingMore,
  error,
  reload,
  loadMore: loadMorePage,
} = useAdminList<AdminAuditItem>();
const PAGE_SIZE = 20;

/**
 * 动作 → 中文标签。
 * 后端用 `对象.动作` 命名，这里只做展示映射——新增动作时若忘了补映射，
 * 退化为原样显示（而不是渲染成空白），便于一眼发现漏配。
 */
const ACTION_LABELS: Record<string, string> = {
  'source.create': '新增源',
  'source.update': '修改源',
  'source.delete': '删除源',
  'source.reset': '重置内置源',
  // P1-18：连续永久失败达阈值后由系统自动禁用（默认关闭，需运维主动开启）
  'source.auto_disable': '自动禁用源',
  'admin.add': '添加管理员',
  'admin.remove': '移除管理员',
  // P1-20：资讯审核与下架
  'news.hide': '下架资讯',
  'news.restore': '恢复资讯',
  'news.update': '修改资讯',
};

/** 变更字段 → 中文名（与后端 _out 的字段对齐） */
const FIELD_LABELS: Record<string, string> = {
  name: '名称',
  channel: '频道',
  url: '地址',
  weight: '权重',
  enabled: '启用',
  respectRobots: '遵守 robots',
  isTop: '置顶',
};

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/**
 * 操作者（P1-18）：系统按规则自动执行的动作没有人类操作者，
 * 直接把后端的 `system` 显示出来会让人以为是某个账号，改成"系统自动执行"才说得通——
 * 审计页的核心价值就是"谁动的"，这一栏含糊等于白留痕。
 */
function actorLine(phone: string): string {
  return phone === 'system' ? '系统自动执行' : `由 ${phone} 操作`;
}

/** 把 enabled 的 true/false 翻成"启用/停用"，避免出现"true→false"这种后端视角的文案 */
function fieldValue(field: string, value: unknown): string {
  if (field === 'enabled') return value ? '启用' : '停用';
  if (field === 'respectRobots') return value ? '遵守' : '忽略';
  // P1-20：置顶开关。false 译成"取消置顶"而不是"不置顶"——后者读起来像从未置顶
  if (field === 'isTop') return value ? '置顶' : '取消置顶';
  if (value === null || value === undefined || value === '') return '空';
  return String(value);
}

/** 变更明细：更新渲染为"权重 5→3"，增删渲染关键信息 */
function changeTexts(item: AdminAuditItem): string[] {
  const changes = item.detail.changes;
  if (changes && typeof changes === 'object') {
    return Object.entries(changes as Record<string, unknown>).map(([field, pair]) => {
      const [before, after] = Array.isArray(pair) ? pair : [undefined, undefined];
      return `${FIELD_LABELS[field] ?? field} ${fieldValue(field, before)}→${fieldValue(field, after)}`;
    });
  }
  const snapshot = item.detail.snapshot;
  if (snapshot && typeof snapshot === 'object') {
    const s = snapshot as Record<string, unknown>;
    const parts: string[] = [];
    if (typeof s.name === 'string' && s.name) parts.push(s.name);
    if (typeof s.url === 'string' && s.url) parts.push(s.url);
    if (typeof s.weight === 'number') parts.push(`权重 ${s.weight}`);
    if (typeof s.enabled === 'boolean') parts.push(s.enabled ? '启用' : '停用');
    // 只在「关闭遵守」时补一句：true 是默认值，逐条打出来只会稀释真正的信息
    if (s.respectRobots === false) parts.push('忽略 robots');
    // P1-18：自动禁用必须**自证原因**——只写"被禁了"的审计条目等于没留痕，
    // 运营要能一眼看出"是因为连挂 7 次 404 才被禁的"
    if (typeof s.consecutive_failures === 'number') {
      parts.push(`连续失败 ${s.consecutive_failures} 次`);
    }
    if (typeof s.last_error === 'string' && s.last_error) parts.push(s.last_error);
    return parts;
  }
  if (typeof item.detail.seeded === 'number') return [`恢复 ${item.detail.seeded} 个内置源`];
  if (typeof item.detail.note === 'string' && item.detail.note) return [item.detail.note];
  // P1-20：下架/恢复的明细是 {title, reason}——target 只有资讯 id，
  // 运营认标题不认 id；理由更必须显示出来，只写"下架了一条资讯"等于没留痕
  if (typeof item.detail.title === 'string' && item.detail.title) {
    const parts = [item.detail.title];
    if (typeof item.detail.reason === 'string' && item.detail.reason) {
      parts.push(`理由：${item.detail.reason}`);
    }
    return parts;
  }
  return [];
}

/** 从源管理/管理员页带 ?target= 进入时，只看该对象的历史 */
const filterTarget = computed(() =>
  typeof route.query.target === 'string' ? route.query.target : '',
);

async function load() {
  if (!user.isAdmin) return;
  resetDenied();
  try {
    await reload(() =>
      fetchAuditPage({
        limit: PAGE_SIZE,
        target: filterTarget.value || undefined,
      }),
    );
  } catch (e) {
    error.value = handleError(e, '加载审计记录失败');
  }
}

async function loadMore() {
  await loadMorePage((c) =>
    fetchAuditPage({
      limit: PAGE_SIZE,
      cursor: c,
      target: filterTarget.value || undefined,
    }),
  );
}

onMounted(load);

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
</script>

<template>
  <div class="admin">
    <AppNav variant="title" title="运营审计" show-back @back="router.back()" />

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址/登录态过期的情况 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限查看审计记录"
      description="该功能仅对管理员开放。如需开通，请联系现有管理员把你的手机号加入白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <div v-if="filterTarget" class="admin__filterbar">
        <span>只看「{{ filterTarget }}」</span>
        <button type="button" class="admin__sellink" @click="router.push('/admin/audit')">
          查看全部
        </button>
      </div>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!list.length" class="admin__hint">还没有任何操作记录。</p>

      <ul v-else class="admin__list">
        <li v-for="a in list" :key="a.id" class="admin__item">
          <div class="admin__item-head">
            <span class="admin__action">{{ actionLabel(a.action) }}</span>
            <span v-if="a.target" class="admin__target">{{ a.target }}</span>
            <span class="admin__time">{{ formatTime(a.createdAt) }}</span>
          </div>
          <p class="admin__actor">{{ actorLine(a.actorPhone) }}</p>
          <ul v-if="changeTexts(a).length" class="admin__changes">
            <li v-for="(t, i) in changeTexts(a)" :key="i">{{ t }}</li>
          </ul>
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

.admin__filterbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 16px;
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
  margin: 12px 0 0;
  padding: 0 16px;
  list-style: none;
}

.admin__item {
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-card);
}

.admin__item-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.admin__action {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.admin__target {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  font-size: 11px;
  word-break: break-all;
}

.admin__time {
  margin-left: auto;
  flex: none;
  font-size: 11px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}

.admin__actor {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}

.admin__changes {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.admin__changes li {
  font-size: 12px;
  color: var(--color-text-primary);
  word-break: break-all;
}

.admin__more {
  display: flex;
  padding: 16px;
}
</style>
