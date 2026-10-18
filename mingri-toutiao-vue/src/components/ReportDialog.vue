<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import Button from '@/components/Button.vue';
import { REPORT_REASONS, type ReportReason, type ReportTargetType } from '@/constants/reports';
import { submitReport } from '@/api/reports';
import { useUserStore } from '@/stores/user';

/**
 * 举报分类弹窗（E5 / P1-66）。
 *
 * 三个刻意的设计：
 * 1. **不预选理由**：预选等于把「第一个类目」当成默认意图，误点一下就提交了
 *    一条自己没选的举报。必须显式选择才允许提交。
 * 2. **不展示处置结果**：成功后只说「已提交」，不回显 autoHidden/状态。
 *    回显等于把自动处置阈值告诉刷子（设计文档 §六）。
 * 3. **未登录直接给登录出口**，而不是让用户选完理由再吃一个 401——
 *    把失败放在最后一步是最浪费用户操作的设计。
 */
const props = withDefaults(
  defineProps<{
    /** 是否显示（配合 v-model:open） */
    open: boolean;
    /** 举报对象类型：评论 / 资讯 */
    targetType?: ReportTargetType;
    /** 举报对象 id（评论 id 或资讯 id，原样透传） */
    targetId?: string;
    /** 弹窗标题：默认按对象类型生成 */
    title?: string;
  }>(),
  { targetType: 'comment', targetId: '', title: '' },
);

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'submitted'): void;
}>();

const router = useRouter();
const user = useUserStore();

const reason = ref<ReportReason | ''>('');
const submitting = ref(false);
const error = ref('');
/** 提交成功态：与「未提交」区分，避免成功后弹窗内容一闪而过 */
const done = ref(false);

const heading = computed(
  () => props.title || (props.targetType === 'news' ? '举报该资讯' : '举报该评论'),
);
const loggedIn = computed(() => Boolean(user.isLoggedIn));

/** 弹窗容器：打开时把焦点移进来，键盘用户不必从头 Tab 一遍 */
const sheet = ref<HTMLElement | null>(null);

/** 成功后的自动关闭：给用户看清提示的时间，又不强迫他多点一次 */
let closeTimer: ReturnType<typeof setTimeout> | undefined;

function clearCloseTimer() {
  if (closeTimer !== undefined) {
    clearTimeout(closeTimer);
    closeTimer = undefined;
  }
}

function close() {
  clearCloseTimer();
  emit('update:open', false);
}

/** 每次打开都从干净状态开始：上次选的理由/报错不能残留到下一次举报 */
watch(
  () => props.open,
  (open) => {
    if (open) {
      reason.value = '';
      error.value = '';
      done.value = false;
      submitting.value = false;
      void nextTick(() => sheet.value?.focus());
    } else {
      clearCloseTimer();
    }
  },
  { immediate: true },
);

async function submit() {
  if (!reason.value || submitting.value) return;
  submitting.value = true;
  error.value = '';
  try {
    await submitReport(props.targetType, props.targetId, reason.value);
    done.value = true;
    emit('submitted');
    // 让「已提交」停留一会儿；用户也可立刻点「知道了」关掉
    closeTimer = setTimeout(close, 1500);
  } catch (e) {
    error.value = e instanceof Error ? e.message : '提交失败，请稍后重试';
  } finally {
    submitting.value = false;
  }
}

function goLogin() {
  close();
  void router.push('/login');
}

/** Esc 关闭：移动端没有键盘，但桌面预览（本项目的 PhoneFrame 演示）会用到 */
function onKeydown(ev: KeyboardEvent) {
  if (ev.key === 'Escape' && props.open) close();
}

onMounted(() => window.addEventListener('keydown', onKeydown));
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown);
  clearCloseTimer();
});
</script>

<template>
  <div v-if="open" class="mask" @click.self="close">
    <div
      ref="sheet"
      class="sheet"
      role="dialog"
      aria-modal="true"
      aria-labelledby="report-title"
      tabindex="-1"
    >
      <header class="sheet__head">
        <h3 id="report-title" class="sheet__title">{{ heading }}</h3>
        <button class="sheet__close" aria-label="关闭举报弹窗" @click="close">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M6 6l12 12M18 6L6 18"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            />
          </svg>
        </button>
      </header>

      <!-- 未登录：先给出口，别让用户选完理由才被 401 拦下 -->
      <template v-if="!loggedIn">
        <p class="sheet__desc">举报需要登录，登录后即可提交。</p>
        <div class="sheet__actions">
          <Button variant="secondary" label="取消" @click="close" />
          <Button variant="primary" label="去登录" @click="goLogin" />
        </div>
      </template>

      <!-- 已提交：只确认收到，不回显处置结果（阈值不对外） -->
      <template v-else-if="done">
        <p class="sheet__desc sheet__desc--ok" role="status">已提交，感谢反馈。</p>
        <p class="sheet__hint">我们会尽快核实处理，处理结果不再单独通知。</p>
        <div class="sheet__actions">
          <Button variant="primary" block label="知道了" @click="close" />
        </div>
      </template>

      <template v-else>
        <p class="sheet__desc">请选择举报原因</p>
        <div class="sheet__reasons" role="radiogroup" aria-labelledby="report-title">
          <button
            v-for="r in REPORT_REASONS"
            :key="r"
            class="reason"
            :class="{ 'is-active': reason === r }"
            role="radio"
            :aria-checked="reason === r"
            :disabled="submitting"
            @click="reason = r"
          >
            <span class="reason__dot" aria-hidden="true" />
            <span class="reason__label">{{ r }}</span>
          </button>
        </div>

        <p v-if="error" class="sheet__error" role="alert">{{ error }}</p>

        <div class="sheet__actions">
          <Button variant="secondary" label="取消" :disabled="submitting" @click="close" />
          <Button
            variant="primary"
            :label="submitting ? '提交中…' : '提交举报'"
            :disabled="!reason || submitting"
            @click="submit"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 覆盖层：定位相对 PhoneFrame 内的 .app-shell（与 StepUpDialog 同一套约定） */
.mask {
  position: absolute;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: rgb(0 0 0 / 45%);
}

.sheet {
  width: 100%;
  max-height: 86%;
  overflow-y: auto;
  padding: 16px 16px 20px;
  border-radius: 16px 16px 0 0;
  background: var(--color-card-bg);
  box-shadow: 0 -8px 24px rgb(0 0 0 / 18%);
  outline: none;
  font-family: var(--font-cn);
}

.sheet__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.sheet__title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.sheet__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 14px;
  background: transparent;
  padding: 0;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.sheet__close:hover {
  color: var(--color-text-primary);
}

.sheet__desc {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}

.sheet__desc--ok {
  color: var(--color-text-primary);
  font-size: 14px;
  font-weight: 600;
}

.sheet__hint {
  margin: 0 0 16px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}

.sheet__reasons {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.reason {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 11px 12px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-page-bg);
  color: var(--color-text-primary);
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.reason:disabled {
  opacity: 0.6;
  cursor: default;
}

.reason.is-active {
  border-color: var(--color-primary);
}

.reason__dot {
  flex-shrink: 0;
  width: 14px;
  height: 14px;
  border: 1.5px solid var(--color-border);
  border-radius: 50%;
  background: var(--color-card-bg);
}

.reason.is-active .reason__dot {
  border-color: var(--color-primary);
  /* 内圈用 box-shadow 画，避免再套一个元素 */
  box-shadow: inset 0 0 0 3px var(--color-primary);
}

.reason__label {
  flex: 1;
  min-width: 0;
}

.sheet__error {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-error);
}

.sheet__actions {
  display: flex;
  gap: 12px;
}

.sheet__actions :deep(.mt-btn) {
  flex: 1;
}
</style>
