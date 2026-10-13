<script setup lang="ts">
import Button from '@/components/Button.vue';
import AppNav from '@/components/AppNav.vue';
import ChannelTabs from '@/components/ChannelTabs.vue';
import NewsCard from '@/components/NewsCard.vue';
import { mockNews } from '@/data/mockNews';
import { CHANNELS } from '@/constants/channels';
import type { Author, ChannelTab } from '@/types';

const demoTabs: ChannelTab[] = CHANNELS.map((c, i) => ({ ...c, active: i === 0 }));

const author: Author = { name: '新华社', time: '2小时前' };

const samples = {
  text: mockNews.find((n) => n.variant === 'text')!,
  leftRight: mockNews.find((n) => n.variant === 'left-right')!,
  triple: mockNews.find((n) => n.variant === 'triple')!,
  video: mockNews.find((n) => n.variant === 'video')!,
};

const tokens = [
  { name: '主色', value: '#2D6CFF' },
  { name: '页面底', value: '#F4F5F6' },
  { name: '卡片底', value: '#FFFFFF' },
  { name: '主文字', value: '#222222' },
  { name: '次文字', value: '#9A9FA8' },
  { name: '分割线', value: '#F0F1F2' },
  { name: '错误', value: '#E5484D' },
  { name: '错误浅底', value: '#FCEDED' },
];
</script>

<template>
  <div class="showcase">
    <section class="sc-section">
      <h2 class="sc-h">色彩令牌</h2>
      <div class="sc-tokens">
        <div v-for="t in tokens" :key="t.value" class="sc-token">
          <span class="sc-token__swatch" :style="{ background: t.value }" />
          <span class="sc-token__name">{{ t.name }}</span>
          <span class="sc-token__value">{{ t.value }}</span>
        </div>
      </div>
    </section>

    <section class="sc-section">
      <h2 class="sc-h">Button</h2>
      <div class="sc-row">
        <Button variant="primary" label="主按钮" />
        <Button variant="secondary" label="次按钮" />
        <Button variant="text" label="文字按钮" />
        <Button variant="primary" disabled label="禁用" />
      </div>
      <div class="sc-row"><Button variant="primary" block label="主按钮 block" /></div>
    </section>

    <section class="sc-section">
      <h2 class="sc-h">AppNav（四变体）</h2>
      <div class="sc-stack">
        <AppNav variant="brand" show-search show-message />
        <AppNav variant="title" title="我的收藏" show-back />
        <AppNav variant="detail" :author="author" show-more />
      </div>
    </section>

    <section class="sc-section">
      <h2 class="sc-h">ChannelTabs</h2>
      <ChannelTabs :tabs="demoTabs" />
    </section>

    <section class="sc-section">
      <h2 class="sc-h">NewsCard（四变体）</h2>
      <div class="sc-grid">
        <NewsCard :item="samples.text" />
        <NewsCard :item="samples.leftRight" />
        <NewsCard :item="samples.triple" />
        <NewsCard :item="samples.video" />
      </div>
    </section>

    <section class="sc-section">
      <h2 class="sc-h">错误态（语义色）</h2>
      <div class="sc-error">
        <span class="sc-error__icon">!</span>
        <p>手机号或密码错误，请重新输入</p>
      </div>
      <Button variant="primary" block label="重新登录" />
    </section>
  </div>
</template>

<style scoped>
.showcase {
  width: 440px;
  max-height: 844px;
  overflow-y: auto;
  background: #fff;
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 12px 40px rgba(17, 24, 39, 0.12);
}

.sc-section {
  margin-bottom: 28px;
}

.sc-h {
  font-size: 13px;
  font-weight: 700;
  color: #222;
  margin: 0 0 12px;
  letter-spacing: 0.04em;
}

.sc-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.sc-stack {
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-card);
  overflow: hidden;
}

.sc-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sc-tokens {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.sc-token {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.sc-token__swatch {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid var(--color-divider);
  flex-shrink: 0;
}

.sc-token__name {
  color: #222;
  font-weight: 500;
}

.sc-token__value {
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}

.sc-error {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--color-error-bg);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}

.sc-error__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-error);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.sc-error p {
  margin: 0;
  color: var(--color-error);
  font-size: 13px;
}
</style>
