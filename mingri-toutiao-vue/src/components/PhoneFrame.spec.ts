import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

/** PhoneFrame：纯外壳，但尺寸是设计约束（390×844），不能被随手改掉。 */
import PhoneFrame from '@/components/PhoneFrame.vue';

describe('PhoneFrame', () => {
  it('渲染插槽内容（没有插槽就没有手机屏幕）', () => {
    const w = mount(PhoneFrame, { slots: { default: '<div class="inner">页面内容</div>' } });
    expect(w.find('.inner').exists()).toBe(true);
    expect(w.text()).toContain('页面内容');
  });

  it('外壳类名存在（尺寸/圆角/阴影都挂在这个类上）', () => {
    const w = mount(PhoneFrame);
    expect(w.find('.phone-frame').exists()).toBe(true);
  });
});

beforeEach(() => {
  vi.clearAllMocks();
});
