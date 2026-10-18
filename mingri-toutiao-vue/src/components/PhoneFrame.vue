<script setup lang="ts">
// 手机外壳：固定 390×844，承载移动端屏幕内容。
</script>

<template>
  <div class="phone-frame">
    <slot />
  </div>
</template>

<style scoped>
.phone-frame {
  /* 建立定位上下文：弹窗/抽屉的遮罩（StepUpDialog、ReportDialog）用 `position: absolute`
     铺满**手机屏幕**。没有它，绝对定位会以视口为基准，遮罩跑到整个浏览器窗口、
     底部弹窗贴到页面底边而不是手机底边。 */
  position: relative;
  width: 390px;
  height: 844px;
  background: var(--color-page-bg);
  border-radius: 40px;
  box-shadow: var(--shadow-phone);
  overflow: hidden;
  border: 1px solid rgba(0, 0, 0, 0.06);
  flex-shrink: 0;
}
</style>
