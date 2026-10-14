<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import Button from '@/components/Button.vue';
import { useUserStore } from '@/stores/user';
import { THEME_OPTIONS } from '@/constants/theme';

const router = useRouter();
const user = useUserStore();

/** 主题三态：跟随系统 / 浅色 / 深色（store 持久化，useTheme 负责生效） */
const themeOptions = THEME_OPTIONS;

const favCount = computed(() => user.favorites.length);
const historyCount = computed(() => user.history.length);

/**
 * 运营入口仅对管理员展示。
 * `isAdmin` 由登录响应下发（后端按 `ADMIN_PHONES` 判定），
 * 未登录 / 非管理员 / mock 模式下均为 false——入口不会出现。
 */
const showAdminEntry = computed(() => user.isLoggedIn && user.isAdmin);

const menu = computed(() => {
  const items = [
    { key: 'favorites', label: '我的收藏', desc: '查看你保存的内容', to: '/favorites' },
    { key: 'history', label: '浏览历史', desc: '回顾你看过的资讯', to: '/history' },
  ];
  if (showAdminEntry.value) {
    items.push({
      key: 'admin',
      label: 'RSS 源管理',
      desc: '增删启停信源、查看抓取状态',
      to: '/admin/sources',
    });
    items.push({
      key: 'admin-news',
      label: '资讯审核',
      desc: '下架违规资讯、恢复与置顶',
      to: '/admin/news',
    });
    items.push({
      key: 'admin-users',
      label: '管理员管理',
      desc: '指定可访问后台的账号',
      to: '/admin/users',
    });
  }
  items.push({ key: 'settings', label: '设置', desc: '账号与安全', to: '' });
  return items;
});

function go(to: string) {
  if (to) router.push(to);
}

async function logout() {
  await user.logout();
  router.push('/');
}
</script>

<template>
  <div class="profile">
    <div class="profile__banner">
      <span class="profile__avatar" />
      <div class="profile__info">
        <span class="profile__name">{{ user.isLoggedIn ? '明日头条用户' : '未登录' }}</span>
        <span class="profile__sub">
          {{ user.isLoggedIn ? '欢迎回来，继续浏览今日热点' : '登录后同步收藏与历史' }}
        </span>
      </div>
    </div>

    <div class="profile__theme">
      <div class="profile__theme-head">
        <span class="profile__theme-title">外观</span>
        <span class="profile__theme-desc">选择浅色、深色或跟随系统</span>
      </div>
      <div class="profile__segment" role="radiogroup" aria-label="主题模式">
        <button
          v-for="opt in themeOptions"
          :key="opt.value"
          class="profile__segment-item"
          :class="{ 'is-active': user.theme === opt.value }"
          :aria-checked="user.theme === opt.value"
          role="radio"
          @click="user.setTheme(opt.value)"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>

    <div class="profile__menu">
      <button v-for="item in menu" :key="item.key" class="profile__item" @click="go(item.to)">
        <span class="profile__item-main">
          <span class="profile__item-label">{{ item.label }}</span>
          <span class="profile__item-desc">{{ item.desc }}</span>
        </span>
        <span v-if="item.key === 'favorites' && favCount" class="profile__badge">{{
          favCount
        }}</span>
        <span v-else-if="item.key === 'history' && historyCount" class="profile__badge">{{
          historyCount
        }}</span>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path
            d="M9 6l6 6-6 6"
            stroke="#c5c8cd"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>

    <div class="profile__footer">
      <Button variant="secondary" block label="退出登录" @click="logout" />
    </div>
  </div>
</template>

<style scoped>
.profile {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-page-bg);
}

.profile__banner {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 28px 20px;
  background: var(--color-card-bg);
}

.profile__avatar {
  width: 60px;
  height: 60px;
  border-radius: var(--radius-avatar);
  background: linear-gradient(135deg, #4e8bff, #1e4fd8);
  flex-shrink: 0;
}

.profile__info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.profile__name {
  font-size: 19px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.profile__sub {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.profile__theme {
  padding: 14px 16px;
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

.profile__theme-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 10px;
}

.profile__theme-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.profile__theme-desc {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.profile__segment {
  display: flex;
  gap: 6px;
  padding: 3px;
  border-radius: 10px;
  background: var(--color-page-bg);
}

.profile__segment-item {
  flex: 1;
  padding: 7px 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.profile__segment-item.is-active {
  background: var(--color-card-bg);
  color: var(--color-primary);
  font-weight: 600;
  box-shadow: var(--shadow-card);
}

.profile__menu {
  margin-top: 10px;
  background: var(--color-card-bg);
}

.profile__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 16px 20px;
  border: none;
  background: transparent;
  text-align: left;
  border-bottom: 1px solid var(--color-divider);
  cursor: pointer;
}

.profile__item-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.profile__item-label {
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.profile__item-desc {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.profile__badge {
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-num);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.profile__footer {
  margin-top: auto;
  padding: 20px;
}
</style>
