<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import PhoneFrame from '@/components/PhoneFrame.vue';
import TabBar from '@/components/TabBar.vue';
import ErrorBoundary from '@/components/ErrorBoundary.vue';
import StepUpDialog from '@/components/StepUpDialog.vue';
import Showcase from '@/views/Showcase.vue';
import { useMessagesStore } from '@/stores/messages';
import { useFollowsStore } from '@/stores/follows';
import { useUserStore } from '@/stores/user';
import { useOnline } from '@/composables/useOnline';
import { registerServiceWorker, type PwaUpdateHandle } from '@/pwa/register';
import type { TabItem } from '@/types';

const route = useRoute();
const router = useRouter();
const messages = useMessagesStore();
const user = useUserStore();

const tabs: TabItem[] = [
  { key: 'home', label: '首页', icon: 'home' },
  { key: 'video', label: '视频', icon: 'video' },
  { key: 'message', label: '消息', icon: 'message' },
  { key: 'mine', label: '我的', icon: 'mine' },
];

const tabRoutes: Record<string, string> = {
  home: '/',
  video: '/video',
  message: '/messages',
  mine: '/profile',
};

const showTabBar = computed(() => !!route.meta.tab);
const activeTab = computed(() => (route.meta.tab as string) || 'home');

/**
 * 需要缓存（保留滚动位置与列表状态）的 Tab 级视图。
 * 名称取自 <script setup> 的组件名（Vue 依据文件名推断）。
 */
const keepAliveViews = ['HomeFeed', 'Videos', 'Messages'];

/** 未读徽标：路由切换后刷新，保证从消息页返回时数字已更新 */
const badges = ref<Record<string, number>>({});
async function refreshBadges() {
  badges.value = { message: await messages.refreshUnread() };
}
onMounted(refreshBadges);
watch(() => route.path, refreshBadges);

// 启动时若已是登录态，把本地收藏/历史并入云端（未登录或 mock 模式内部自动跳过）
onMounted(() => {
  void user.syncMarks();
});

// E6b：关注集合是账号数据——启动拉一次，登录态变化（登录/登出/切账号）再拉一次。
// 不拉的话首页「关注」与详情页的关注按钮会停留在上一次账号的状态（串号的老坑）。
const follows = useFollowsStore();
onMounted(() => void follows.refresh());
watch(
  () => user.isLoggedIn,
  () => void follows.refresh(),
);

// E8：Service Worker 注册与更新提示（prod 才有；dev/jsdom 下 register 内部自行跳过）
const pwa = ref<PwaUpdateHandle | null>(null);
onMounted(() => {
  pwa.value = registerServiceWorker();
});

// 网络状态：掉线时给一条**持久**提示（不用 toast——toast 会消失，
// 而"当前离线"这件事在用户恢复网络前应该一直可见）
const { online } = useOnline();

function onTab(key: string) {
  const path = tabRoutes[key];
  if (path) router.push(path);
}
</script>

<template>
  <div class="page">
    <header class="page__header">
      <h1>明日头条 · Vue 3 多页应用</h1>
      <p>
        vue-router + Pinia 驱动的可导航 App 骨架，复用组件库与设计令牌；左侧为运行中的
        App，右侧为组件库陈列。
      </p>
    </header>
    <div class="page__stage">
      <PhoneFrame>
        <div class="app-shell">
          <!-- E8：新版本提示（点击才刷新，不自动 skipWaiting——那会在阅读中途换掉执行环境） -->
          <div v-if="pwa && pwa.available()" class="app-banner app-banner--update">
            <span>发现新版本</span>
            <button type="button" @click="pwa?.refresh()">刷新</button>
          </div>
          <!-- E8：离线提示（持久可见；SW 会把 GET 请求回落缓存，但列表会是旧数据） -->
          <div v-if="!online" class="app-banner app-banner--offline">
            <span>当前离线，展示的是缓存内容</span>
          </div>
          <main class="app-shell__main">
            <!-- 渲染异常时降级为局部错误页，避免整页白屏 -->
            <ErrorBoundary>
              <!-- Tab 级列表页缓存：切回时保留滚动位置与已加载数据 -->
              <router-view v-slot="{ Component }">
                <KeepAlive :include="keepAliveViews">
                  <component :is="Component" />
                </KeepAlive>
              </router-view>
            </ErrorBoundary>
          </main>
          <TabBar
            v-if="showTabBar"
            :items="tabs"
            :active="activeTab"
            :badges="badges"
            @update:active="onTab"
          />
          <!-- 高敏操作二次校验（C10）：由 40304 全局触发，与具体页面解耦 -->
          <StepUpDialog />
        </div>
      </PhoneFrame>
      <ErrorBoundary title="组件库陈列出了点问题">
        <Showcase />
      </ErrorBoundary>
    </div>
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 32px;
  background: var(--color-page-bg);
}

.page__header {
  max-width: 1200px;
  margin: 0 auto 24px;
}

.page__header h1 {
  font-size: 24px;
  margin: 0 0 8px;
  color: var(--color-text-primary);
}

.page__header p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  max-width: 760px;
  line-height: 1.6;
}

.page__stage {
  display: flex;
  gap: 32px;
  align-items: flex-start;
  max-width: 1200px;
  margin: 0 auto;
  flex-wrap: wrap;
}

.app-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.app-shell__main {
  flex: 1;
  overflow-y: auto;
  background: var(--color-page-bg);
}

/* E8：状态横幅。放在 app-shell 顶部而不是全局 fixed——它属于"这个 App"的状态，
   陈列区（右侧组件库）不该跟着变。 */
.app-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 6px 12px;
  font-size: 12px;
  font-family: var(--font-cn);
  flex-shrink: 0;
}

.app-banner--offline {
  color: var(--color-text-primary);
  background: var(--color-divider);
}

.app-banner--update {
  color: #fff;
  background: var(--color-primary);
}

.app-banner--update button {
  padding: 2px 10px;
  font-size: 12px;
  color: var(--color-primary);
  background: #fff;
  border: none;
  border-radius: 10px;
  cursor: pointer;
}
</style>
