import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { enableAutoUnmount, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { reactive } from 'vue';

/**
 * 二次校验对话框（C10）——此前 0% 覆盖。
 *
 * 它挂在 App 根部、靠**全局事件**触发，所以有三个容易出错的点：
 * 1. **卸载必须摘监听**：不摘就会在每次路由切换后累积监听器，
 *    同一个 40304 弹 N 次，且泄漏整个组件实例；
 * 2. **并发高敏请求只弹一个**：`store.visible` 为真时不再 `open()`
 *    （否则会把用户正在输入的密码清空）；
 * 3. 空密码不提交（避免把空串送去撞锁定计数）。
 */

const realStore = await vi.importActual<typeof import('@/stores/stepUp')>('@/stores/stepUp');
const openSpy = vi.fn();
const cancelSpy = vi.fn();
const submitSpy = vi.fn();
const state = reactive({ visible: false, password: '', error: '', submitting: false });

vi.mock('@/stores/stepUp', () => ({
  useStepUpStore: () => {
    // 用真实 store 的实现思路，但把动作换成 spy 并暴露可改状态
    const s = {
      get visible() {
        return state.visible;
      },
      set visible(v: boolean) {
        state.visible = v;
      },
      get password() {
        return state.password;
      },
      set password(v: string) {
        state.password = v;
      },
      error: '',
      submitting: false,
      open: () => {
        openSpy();
        state.visible = true;
      },
      close: () => {
        state.visible = false;
      },
      cancel: () => {
        cancelSpy();
        state.visible = false;
      },
      submit: () => submitSpy(),
    };
    return s;
  },
}));

vi.mock('@/api/http', async () => {
  const actual = await vi.importActual<typeof import('@/api/http')>('@/api/http');
  return { ...actual, STEP_UP_REQUIRED_EVENT: actual.STEP_UP_REQUIRED_EVENT };
});

import StepUpDialog from '@/components/StepUpDialog.vue';
import { STEP_UP_REQUIRED_EVENT } from '@/api/http';

const flush = () => new Promise((r) => setTimeout(r, 0));

// 本组件的监听挂在 window 上，用例之间必须彻底卸载
enableAutoUnmount(afterEach);

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  state.visible = false;
  state.password = '';
  void realStore; // 保留引用，说明本文件复用其语义而非另写一套
});

describe('StepUpDialog', () => {
  it('未触发时不渲染对话框', () => {
    const w = mount(StepUpDialog);
    expect(w.find('.dialog').exists()).toBe(false);
  });

  it('收到全局事件后弹出（40304 的入口）', async () => {
    const w = mount(StepUpDialog);
    window.dispatchEvent(new CustomEvent(STEP_UP_REQUIRED_EVENT));
    await flush();
    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(w.find('.dialog').exists()).toBe(true);
  });

  it('并发高敏请求只弹一次（重复 open 会清掉用户正在输入的密码）', async () => {
    state.visible = true; // 已经开着
    state.password = '用户输入到一半';
    mount(StepUpDialog);
    window.dispatchEvent(new CustomEvent(STEP_UP_REQUIRED_EVENT));
    await flush();
    expect(openSpy).not.toHaveBeenCalled();
    expect(state.password).toBe('用户输入到一半');
  });

  it('**卸载时摘掉监听**（否则每次路由切换都累积一个，弹窗会弹 N 次 + 泄漏实例）', async () => {
    const w = mount(StepUpDialog);
    w.unmount();
    window.dispatchEvent(new CustomEvent(STEP_UP_REQUIRED_EVENT));
    await flush();
    expect(openSpy).not.toHaveBeenCalled();
  });

  it('点遮罩空白处取消（点对话框内部不该关）', async () => {
    state.visible = true;
    const w = mount(StepUpDialog);
    await w.find('.dialog').trigger('click');
    expect(cancelSpy).not.toHaveBeenCalled();
    await w.find('.mask').trigger('click');
    expect(cancelSpy).toHaveBeenCalledTimes(1);
  });

  it('空密码按回车不提交（避免空串去撞锁定计数）', async () => {
    state.visible = true;
    state.password = '   ';
    const w = mount(StepUpDialog);
    const input = w.find('input[type="password"]');
    await input.trigger('keyup.enter');
    expect(submitSpy).not.toHaveBeenCalled();
  });

  it('有密码按回车提交', async () => {
    state.visible = true;
    state.password = 'pwd';
    const w = mount(StepUpDialog);
    await w.find('input[type="password"]').trigger('keyup.enter');
    expect(submitSpy).toHaveBeenCalledTimes(1);
  });
});
