<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { addAdminUser, fetchAdminUsers, removeAdminUser, resetUserPassword } from '@/api/admin';
import { useUserStore } from '@/stores/user';
import { messageOf as message, useAdminGuard } from '@/composables/useAdminPage';
import type { AdminUserItem } from '@/types';

const router = useRouter();
const user = useUserStore();
// D3：鉴权守卫抽到 composable（手敲网址 / 登录态过期 / 令牌已失效三种情况）
const { noAccess, handleError, resetDenied } = useAdminGuard();

const list = ref<AdminUserItem[]>([]);
/** 环境变量来源的管理员数量（只下发了计数，未下发号码） */
const envAdminCount = ref(0);
const loading = ref(false);
const error = ref('');

const draft = reactive({ phone: '', note: '' });
const formError = ref('');
const saving = ref(false);

const pendingRemove = ref<AdminUserItem | null>(null);
const busyPhone = ref('');
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
 * 否则页面会退化成"白名单是空的"，把权限问题伪装成数据问题。
 */

async function load() {
  if (!user.isAdmin) return;
  loading.value = true;
  error.value = '';
  resetDenied();
  try {
    const data = await fetchAdminUsers();
    list.value = data.list;
    envAdminCount.value = data.envAdminCount;
  } catch (e) {
    error.value = handleError(e, '加载白名单失败');
  } finally {
    loading.value = false;
  }
}

onMounted(load);

// ------------------------------ 添加 ------------------------------

/** 前端预校验：与后端 schema 同规则，但**不能**替代后端校验（这里只为少跑一轮无效请求） */
function validateDraft(): string {
  if (!/^1\d{10}$/.test(draft.phone.trim())) return '请输入正确的 11 位手机号';
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
    const phone = draft.phone.trim();
    await addAdminUser({ phone, note: draft.note.trim() });
    showToast(`已添加管理员 ${phone}`);
    draft.phone = '';
    draft.note = '';
    await load();
  } catch (e) {
    formError.value = message(e, '添加失败，请稍后重试');
  } finally {
    saving.value = false;
  }
}

// ------------------------------ 移除 ------------------------------

async function doRemove() {
  const target = pendingRemove.value;
  if (!target) return;
  busyPhone.value = target.phone;
  try {
    await removeAdminUser(target.phone);
    // 删除的本地真相 = 服务端真相
    list.value = list.value.filter((a) => a.phone !== target.phone);
    showToast(`已移除 ${target.phone}`);
  } catch (e) {
    // 末位保护（40904）等服务端语义直接透出：界面不重复造校验
    error.value = message(e, '移除失败');
  } finally {
    busyPhone.value = '';
    pendingRemove.value = null;
  }
}

// ------------------------------ 代重置密码（C4） ------------------------------

/**
 * 用手机号直接重置，而不是"从管理员列表里挑一个"。
 *
 * 理由：C4 的真实场景是**普通用户**忘记密码，而本页列的是应用内管理员白名单——
 * 普通用户根本不在列表里，做成列表项等于"只允许重置管理员"，反而漏了正主。
 */
const resetPhone = ref('');
const resetBusy = ref(false);
const resetError = ref('');
const resetResult = ref<{ phone: string; tempPassword: string } | null>(null);

async function doReset() {
  const phone = resetPhone.value.trim();
  if (!/^1\d{10}$/.test(phone)) {
    resetError.value = '请输入正确的 11 位手机号';
    return;
  }
  resetBusy.value = true;
  resetError.value = '';
  try {
    const data = await resetUserPassword(phone);
    resetResult.value = { phone: data.phone, tempPassword: data.tempPassword };
    resetPhone.value = '';
    showToast(`已重置 ${data.phone} 的密码`);
  } catch (e) {
    resetResult.value = null;
    resetError.value = message(e, '重置失败');
  } finally {
    resetBusy.value = false;
  }
}

function copyTempPassword() {
  const text = resetResult.value?.tempPassword ?? '';
  if (!text) return;
  void navigator.clipboard?.writeText(text);
  showToast('已复制临时密码');
}

// ------------------------------ 展示辅助 ------------------------------

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
</script>

<template>
  <div class="admin">
    <AppNav variant="title" title="管理员管理" show-back @back="router.back()" />

    <p v-if="toast" class="admin__toast" role="status">{{ toast }}</p>

    <!-- 无权限：入口按 isAdmin 隐藏，这里兜住手敲网址/登录态过期的情况 -->
    <EmptyState
      v-if="noAccess"
      icon="collect"
      title="无权限管理管理员"
      description="该功能仅对管理员开放。如需开通，请联系现有管理员把你的手机号加入白名单。"
    >
      <Button variant="primary" label="返回" @click="router.push('/profile')" />
    </EmptyState>

    <template v-else>
      <div class="admin__actions">
        <Button variant="secondary" label="审计记录" @click="router.push('/admin/audit')" />
      </div>

      <!-- 双源机制说明：env 是 break-glass，库白名单供日常运营 -->
      <div class="admin__notice" :class="{ 'admin__notice--warn': envAdminCount === 0 }">
        <p class="admin__notice-title">管理员来自两个来源</p>
        <p class="admin__notice-text">
          环境变量（ADMIN_PHONES）中的管理员共 {{ envAdminCount }} 位，修改后重启生效，
          作为应急通道保留；下面列表是应用内白名单，增删即时生效。
        </p>
        <!-- 应急通道缺失提示：让运营自己看见，而不是等后台真进不去才发现 -->
        <p v-if="envAdminCount === 0" class="admin__notice-warn">
          当前未配置环境变量管理员（应急通道）。建议在生产环境至少保留一条——
          数据库故障、白名单行被误删或迁移回滚时，它是唯一还能进后台的入口。
        </p>
      </div>

      <!-- 添加表单 -->
      <form class="admin__form admin__form--new" @submit.prevent="submitForm">
        <h2 class="admin__form-title">添加管理员</h2>
        <label class="admin__field">
          <span>手机号</span>
          <input
            v-model="draft.phone"
            maxlength="11"
            inputmode="numeric"
            placeholder="11 位手机号"
            aria-label="管理员手机号"
          />
        </label>
        <label class="admin__field">
          <span>备注（选填）</span>
          <input v-model="draft.note" maxlength="100" placeholder="如：运营-张三" />
        </label>
        <p class="admin__form-tip">
          支持预授权：手机号无需先注册账号，加入后其注册/登录时即具备管理能力。
        </p>
        <p v-if="formError" class="admin__form-error">{{ formError }}</p>
        <div class="admin__form-actions">
          <Button
            variant="primary"
            size="md"
            :label="saving ? '添加中…' : '添加'"
            type="submit"
            :disabled="saving"
          />
        </div>
      </form>

      <p v-if="error" class="admin__banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" aria-label="关闭提示" @click="error = ''">×</button>
      </p>

      <p v-if="loading" class="admin__hint">加载中…</p>
      <p v-else-if="!list.length" class="admin__hint">
        应用内白名单还是空的，先通过上方表单添加一位管理员。
      </p>

      <ul v-else class="admin__list">
        <li v-for="a in list" :key="a.phone" class="admin__item">
          <div class="admin__item-head">
            <span class="admin__name">{{ a.phone }}</span>
            <span class="admin__chip" :class="{ 'admin__chip--muted': !a.registered }">
              {{ a.registered ? `已注册${a.name ? `·${a.name}` : ''}` : '预授权' }}
            </span>
          </div>

          <p v-if="a.note" class="admin__note">{{ a.note }}</p>

          <div class="admin__meta">
            <span>由 {{ a.addedBy || '系统' }} 添加</span>
            <span v-if="a.createdAt">{{ formatTime(a.createdAt) }}</span>
          </div>

          <div class="admin__item-actions">
            <button
              type="button"
              class="is-danger"
              :disabled="busyPhone === a.phone"
              @click="pendingRemove = a"
            >
              移除
            </button>
          </div>
        </li>
      </ul>

      <!-- 代重置密码（C4）：对**任意已注册用户**生效，故用手机号输入而非列表项 -->
      <form class="admin__form admin__form--new" @submit.prevent="doReset">
        <h2 class="admin__form-title">重置用户密码</h2>
        <label class="admin__field">
          <span>用户手机号</span>
          <input v-model="resetPhone" maxlength="11" placeholder="11 位手机号" />
        </label>
        <p class="admin__form-tip">
          系统会生成一个一次性临时密码，该用户登录后**必须**先修改密码才能发评论、收藏或进入后台。
          重置会立即作废该账号已登录的所有设备。
        </p>
        <p v-if="resetError" class="admin__form-error">{{ resetError }}</p>
        <div class="admin__form-actions">
          <Button
            type="submit"
            variant="primary"
            size="md"
            :label="resetBusy ? '重置中…' : '生成临时密码'"
            :disabled="resetBusy"
          />
        </div>
      </form>

      <div v-if="resetResult" class="admin__confirm" role="alertdialog" aria-label="临时密码">
        <p>
          「{{ resetResult.phone }}」的临时密码：
          <strong class="admin__temp">{{ resetResult.tempPassword }}</strong>
        </p>
        <p>只显示这一次，请立即通过安全渠道转交本人，并提醒其登录后修改。</p>
        <div class="admin__confirm-actions">
          <Button variant="secondary" size="md" label="复制密码" @click="copyTempPassword" />
          <Button variant="secondary" size="md" label="我已转交" @click="resetResult = null" />
        </div>
      </div>

      <!-- 移除确认 -->
      <div
        v-if="pendingRemove"
        class="admin__confirm"
        role="alertdialog"
        aria-label="确认移除管理员"
      >
        <p>
          移除「{{
            pendingRemove.phone
          }}」？该账号将立即失去管理能力（环境变量中的管理员不受影响）。
        </p>
        <div class="admin__confirm-actions">
          <Button
            variant="primary"
            size="md"
            label="确认移除"
            :disabled="busyPhone === pendingRemove.phone"
            @click="doRemove"
          />
          <Button variant="secondary" size="md" label="取消" @click="pendingRemove = null" />
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

.admin__actions {
  display: flex;
  gap: 10px;
  padding: 10px 16px;
}

/* 双源机制说明卡 */
.admin__notice {
  margin: 14px 16px 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-card);
}

.admin__notice-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.admin__notice-text {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

/* 应急通道缺失：整卡转为警示态，运营打开页面就能看见 */
.admin__notice--warn {
  border: 1px solid #d93025;
}

.admin__notice-warn {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: #d93025;
}

/* 临时密码：等宽 + 大字 + 高对比——它要被口头/手写传达一次，字形含糊就是事故 */
.admin__temp {
  font-family: var(--font-num, monospace);
  font-size: 18px;
  letter-spacing: 1px;
  color: var(--color-primary, #d43d3d);
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
  align-items: center;
  gap: 8px;
}

.admin__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  font-family: var(--font-num);
}

.admin__chip {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--color-page-bg);
  color: var(--color-primary);
  font-size: 11px;
  white-space: nowrap;
}

.admin__chip--muted {
  color: var(--color-text-secondary);
}

.admin__note {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--color-text-primary);
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

.admin__field input {
  padding: 8px 10px;
  border: 1px solid var(--color-divider);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: var(--font-cn);
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
