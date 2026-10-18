import { defineStore } from 'pinia';
import { ref } from 'vue';
import { stepUp as apiStepUp } from '@/api/auth';
import { resolveStepUp } from '@/api/http';

/**
 * 二次校验对话框（C10）。
 *
 * 存在的理由：`http.ts` 收到 40304 时**不能自己弹 UI**（它是无视图的请求层），
 * 也不能在请求层等一个 Promise 直到天荒地老。故拆成两半——
 * 请求层广播 `STEP_UP_REQUIRED_EVENT` 并挂起等待，本 store 负责弹窗与验证，
 * 验证通过后调 `resolveStepUp(true)` 把挂起的请求放行（带上新令牌重试）。
 */
export const useStepUpStore = defineStore('stepUp', () => {
  const visible = ref(false);
  const password = ref('');
  const error = ref('');
  const submitting = ref(false);

  function open() {
    password.value = '';
    error.value = '';
    visible.value = true;
  }

  function close() {
    visible.value = false;
  }

  /** 取消：放行等待中的请求并告诉它「没拿到令牌」（按原错误抛出） */
  function cancel() {
    close();
    resolveStepUp(false);
  }

  /** 提交密码：成功 → 放行；失败 → 留在弹窗内并显示错误 */
  async function submit() {
    if (submitting.value) return;
    submitting.value = true;
    error.value = '';
    try {
      const ok = await apiStepUp(password.value);
      if (ok) {
        close();
        resolveStepUp(true);
      } else {
        error.value = '二次校验失败，请重试';
      }
    } catch (e) {
      // 429 = 连续失败被锁（复用登录的账号维度锁定）；401 = 密码错
      error.value = e instanceof Error ? e.message : '二次校验失败，请重试';
    } finally {
      submitting.value = false;
    }
  }

  return { visible, password, error, submitting, open, close, cancel, submit };
});
