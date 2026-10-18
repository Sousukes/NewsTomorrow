import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { REPORT_REASONS } from '@/constants/reports';

/**
 * 举报弹窗（E5 / P1-66）。
 *
 * 这里钉住的是**三条产品规则**，而不是 DOM 细节：
 * 1. 未登录先给登录出口（别让用户选完理由才吃 401）；
 * 2. 不预选理由（预选 = 把第一个类目当成默认意图，误点即误报）；
 * 3. 成功后不回显处置结果（阈值不对外）。
 */

const submitReport = vi.fn();
/** 模拟 user store 的登录态（弹窗只读 `isLoggedIn`，无需整套 Pinia） */
const userState = { isLoggedIn: true };
const push = vi.fn();

vi.mock('@/api/reports', () => ({
  submitReport: (...args: unknown[]) => submitReport(...args),
}));

vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}));

import ReportDialog from './ReportDialog.vue';

const mountDialog = (props: Record<string, unknown> = {}) =>
  mount(ReportDialog, {
    props: { open: true, targetType: 'comment', targetId: 'c1', ...props },
  });

type Wrapper = ReturnType<typeof mountDialog>;

const settle = async (w: Wrapper) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

/** 按文案找按钮（组件库按钮是 native button，文案在 slot 里） */
const buttonByText = (w: Wrapper, text: string) =>
  w.findAll('button').find((b) => b.text() === text);

const reasonButtons = (w: Wrapper) => w.findAll('.reason');

beforeEach(() => {
  vi.clearAllMocks();
  userState.isLoggedIn = true;
  submitReport.mockResolvedValue({ status: 'pending', autoHidden: false });
});

describe('components/ReportDialog 举报分类弹窗', () => {
  it('已登录时列出全部预设分类', () => {
    const w = mountDialog();
    expect(reasonButtons(w)).toHaveLength(REPORT_REASONS.length);
    expect(reasonButtons(w).map((b) => b.text())).toEqual([...REPORT_REASONS]);
  });

  it('不预选任何理由，未选时提交按钮不可用', () => {
    const w = mountDialog();
    expect(w.findAll('.reason.is-active')).toHaveLength(0);
    expect(buttonByText(w, '提交举报')?.attributes('disabled')).toBeDefined();
  });

  it('选中理由后提交：按 (类型, 目标 id, 理由) 透传并显示成功态', async () => {
    const w = mountDialog({ targetType: 'news', targetId: 'n9' });
    await reasonButtons(w)[2].trigger('click');
    await buttonByText(w, '提交举报')!.trigger('click');
    await settle(w);

    expect(submitReport).toHaveBeenCalledWith('news', 'n9', REPORT_REASONS[2]);
    expect(w.text()).toContain('已提交，感谢反馈');
    expect(w.emitted('submitted')).toHaveLength(1);
  });

  it('成功后不回显处置结果（阈值/是否隐藏不对外）', async () => {
    submitReport.mockResolvedValue({ status: 'pending', autoHidden: true });
    const w = mountDialog();
    await reasonButtons(w)[0].trigger('click');
    await buttonByText(w, '提交举报')!.trigger('click');
    await settle(w);

    expect(w.text()).toContain('已提交');
    expect(w.text()).not.toContain('隐藏');
    expect(w.text()).not.toContain('下架');
  });

  it('提交失败时显示服务端提示且停留在选择态（可改理由重试）', async () => {
    submitReport.mockRejectedValue(new Error('举报对象不存在或已删除'));
    const w = mountDialog();
    await reasonButtons(w)[1].trigger('click');
    await buttonByText(w, '提交举报')!.trigger('click');
    await settle(w);

    expect(w.text()).toContain('举报对象不存在或已删除');
    // 仍在选择态：理由列表还在，成功文案没出现
    expect(reasonButtons(w)).toHaveLength(REPORT_REASONS.length);
    expect(w.text()).not.toContain('已提交');
    expect(w.emitted('submitted')).toBeUndefined();
  });

  it('未登录：不渲染理由列表，只给「去登录」出口', async () => {
    userState.isLoggedIn = false;
    const w = mountDialog();

    expect(reasonButtons(w)).toHaveLength(0);
    expect(w.text()).toContain('举报需要登录');

    await buttonByText(w, '去登录')!.trigger('click');
    expect(push).toHaveBeenCalledWith('/login');
    expect(w.emitted('update:open')?.[0]).toEqual([false]);
  });

  it('关闭按钮与遮罩点击都会通知父级关闭', async () => {
    const w = mountDialog();
    await w.find('.sheet__close').trigger('click');
    expect(w.emitted('update:open')?.[0]).toEqual([false]);

    const w2 = mountDialog();
    await w2.find('.mask').trigger('click');
    expect(w2.emitted('update:open')?.[0]).toEqual([false]);
  });

  it('重新打开会清空上次的选择（避免沿用旧理由误提交）', async () => {
    const w = mountDialog();
    await reasonButtons(w)[3].trigger('click');
    expect(w.findAll('.reason.is-active')).toHaveLength(1);

    await w.setProps({ open: false });
    await w.setProps({ open: true });
    await settle(w);

    expect(w.findAll('.reason.is-active')).toHaveLength(0);
    expect(buttonByText(w, '提交举报')?.attributes('disabled')).toBeDefined();
  });
});
