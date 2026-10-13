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
