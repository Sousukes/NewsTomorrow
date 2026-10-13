import { describe, expect, it } from 'vitest';
import { computed, nextTick, ref } from 'vue';
import { useCarousel } from '@/composables/useCarousel';

const mkFrames = (n: number) => computed(() => Array.from({ length: n }, (_, i) => `f${i}`));

/** 触发 transitionend */
function fireTransitionEnd() {
  const e = new Event('transitionend') as TransitionEvent;
  Object.defineProperty(e, 'propertyName', { value: 'transform' });
  return e;
}

describe('useCarousel', () => {
  describe('轨道构造', () => {
    it('无限回环时首尾各加一个克隆帧', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      //原始 [f0,f1,f2] → 扩展 [f2,f0,f1,f2,f0]
      expect(c.slides.value).toEqual(['f2', 'f0', 'f1', 'f2', 'f0']);
      expect(c.N.value).toBe(3);
    });

    it('infinite:false 时不添加克隆帧', () => {
      const c = useCarousel({ frames: mkFrames(3), infinite: false });
      expect(c.slides.value).toEqual(['f0', 'f1', 'f2']);
    });

    it('少于2 帧时不添加克隆帧（避免重复）', () => {
      const c = useCarousel({ frames: mkFrames(1) });
      expect(c.slides.value).toEqual(['f0']);
    });
  });

  describe('索引与位移', () => {
    it('起始停在真实第 0 张（index=1）', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      expect(c.index.value).toBe(1);
      expect(c.realPos.value).toBe(0);
    });

    it('realPos 始终归一化到 0..N-1', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      c.index.value = 0; // 克隆末帧
      expect(c.realPos.value).toBe(2);
      c.index.value = 4; // 克隆首帧
      expect(c.realPos.value).toBe(0);
    });

    it('trackStyle 输出 translateX 且含拖拽偏移', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      c.offset.value = 30;
      expect(c.trackStyle.value.transform).toBe('translateX(calc(-100% + 30px))');
    });
  });

  describe('相邻切换', () => {
    it('next 后index+1，锁定期拒绝再次触发', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      c.next();
      expect(c.index.value).toBe(2);
      c.next(); // 锁未释放，应被忽略
      expect(c.index.value).toBe(2);
    });

    it('落到末帧克隆位后，transitionend 触发静默复位到真实末帧', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      c.goTo(1); // → 2
      c.next(); // → 3（真实末帧 f2）
      c.onTransitionEnd(fireTransitionEnd()); // 收尾释放锁
      c.next(); // → 4（克隆首帧）
      expect(c.index.value).toBe(4);

      c.onTransitionEnd(fireTransitionEnd());
      // 复位到真实第 0 张，realPos 不变（视觉上仍是"最后一页的下一张"）
      expect(c.index.value).toBe(1);
      expect(c.realPos.value).toBe(0);
    });

    it('复位后释放输入锁（可继续切换）', () => {
      const c = useCarousel({ frames: mkFrames(4) });
      c.next();
      c.onTransitionEnd(fireTransitionEnd());
      c.next();
      expect(c.index.value).toBe(3);
    });

    it('忽略非 transform 的 transitionend', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      c.next();
      const e = new Event('transitionend') as TransitionEvent;
      Object.defineProperty(e, 'propertyName', { value: 'opacity' });
      c.onTransitionEnd(e);
      // 锁未释放，说明没被误判为收尾
      c.next();
      expect(c.index.value).toBe(2);
    });

    it('兜底定时器也能收尾（模拟 reduced-motion）', async () => {
      vi.useFakeTimers();
      const c = useCarousel({ frames: mkFrames(3), transitionMs: 10 });
      c.goTo(1); // → 2
      c.next(); // → 3（真实末帧）
      vi.advanceTimersByTime(200); // 定时器收尾，释放锁
      await nextTick();
      c.next(); // → 4（克隆首帧）
      expect(c.index.value).toBe(4);

      // 不触发 transitionend，仅靠兜底定时器复位
      vi.advanceTimersByTime(200);
      await nextTick();
      expect(c.index.value).toBe(1);
      vi.useRealTimers();
    });
  });

  describe('infinite:false 的边界', () => {
    it('到首尾不越界', () => {
      const c = useCarousel({ frames: mkFrames(3), infinite: false });
      c.prev(); // 已在第 0 张
      expect(c.index.value).toBe(0);
      c.goTo(2);
      c.next();
      expect(c.index.value).toBe(2);
    });
  });

  describe('goTo', () => {
    it('跳转到指定真实帧', () => {
      const c = useCarousel({ frames: mkFrames(5) });
      c.goTo(2);
      expect(c.realPos.value).toBe(2);
      expect(c.index.value).toBe(3); // 2 + cloneOffset
    });

    it('越界参数被夹紧', () => {
      const c = useCarousel({ frames: mkFrames(3) });
      c.goTo(99);
      expect(c.realPos.value).toBe(2);
      c.goTo(-5);
      expect(c.realPos.value).toBe(0);
    });
  });

  describe('手势', () => {
    it('拖拽超过阈值触发 next/prev', () => {
      const c = useCarousel({ frames: mkFrames(4) });
      c.onPointerDown(new MouseEvent('pointerdown') as PointerEvent);
      c.onPointerMove({ clientX: -80 } as PointerEvent);
      c.onPointerUp();
      expect(c.index.value).toBe(2);

      c.onTransitionEnd(fireTransitionEnd());
      c.onPointerDown(new MouseEvent('pointerdown') as PointerEvent);
      c.onPointerMove({ clientX: 80 } as PointerEvent);
      c.onPointerUp();
      expect(c.realPos.value).toBe(0);
    });

    it('拖拽不足阈值则回弹（index 不变）', () => {
      const c = useCarousel({ frames: mkFrames(4) });
      c.onPointerDown(new MouseEvent('pointerdown') as PointerEvent);
      c.onPointerMove({ clientX: -10 } as PointerEvent);
      c.onPointerUp();
      expect(c.index.value).toBe(1);
    });

    it('pointercancel 取消拖拽并回弹', () => {
      const c = useCarousel({ frames: mkFrames(4) });
      c.onPointerDown(new MouseEvent('pointerdown') as PointerEvent);
      c.onPointerMove({ clientX: -90 } as PointerEvent);
      c.onPointerCancel();
      expect(c.index.value).toBe(1);
      expect(c.dragging.value).toBe(false);
    });

    it('moved 标记区分拖拽与点击', () => {
      const c = useCarousel({ frames: mkFrames(4) });
      c.onPointerDown(new MouseEvent('pointerdown') as PointerEvent);
      c.onPointerMove({ clientX: -40 } as PointerEvent);
      expect(c.moved.value).toBe(true);
    });
  });

  describe('数据变化重置', () => {
    it('帧数变化后回到第一帧', async () => {
      const n = ref(3);
      const c = useCarousel({
        frames: computed(() => Array.from({ length: n.value }, (_, i) => `f${i}`)),
      });
      c.goTo(2);
      expect(c.realPos.value).toBe(2);
      n.value = 5;
      await nextTick();
      expect(c.realPos.value).toBe(0);
    });
  });
});
