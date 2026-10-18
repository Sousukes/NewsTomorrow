import { createRouter, createWebHistory } from 'vue-router';
import { useUserStore } from '@/stores/user';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeFeed.vue'),
      meta: { tab: 'home' },
    },
    {
      path: '/video',
      name: 'video',
      component: () => import('@/views/Videos.vue'),
      meta: { tab: 'video' },
    },
    {
      path: '/messages',
      name: 'messages',
      component: () => import('@/views/Messages.vue'),
      meta: { tab: 'message' },
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('@/views/Profile.vue'),
      meta: { tab: 'mine' },
    },
    { path: '/news/:id', name: 'news-detail', component: () => import('@/views/NewsDetail.vue') },
    {
      path: '/news/:id/comments',
      name: 'comments',
      component: () => import('@/views/Comments.vue'),
    },
    { path: '/news/:id/gallery', name: 'gallery', component: () => import('@/views/Gallery.vue') },
    { path: '/login', name: 'login', component: () => import('@/views/Login.vue') },
    // 修改密码（C4）：受限账号的**唯一出口**——路由守卫只放它过去
    {
      path: '/change-password',
      name: 'change-password',
      component: () => import('@/views/ChangePassword.vue'),
    },
    { path: '/search', name: 'search', component: () => import('@/views/Search.vue') },
    {
      path: '/search/results',
      name: 'search-results',
      component: () => import('@/views/SearchResults.vue'),
    },
    { path: '/favorites', name: 'favorites', component: () => import('@/views/Favorites.vue') },
    { path: '/history', name: 'history', component: () => import('@/views/History.vue') },
    // 运营向入口：不在 TabBar 中，仅对管理员在「我的」页展示（后端按白名单判定）
    {
      path: '/admin/sources',
      name: 'admin-sources',
      component: () => import('@/views/AdminSources.vue'),
    },
    // 管理员白名单管理（P1-14）：应用内增删「指定账号」
    {
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('@/views/AdminUsers.vue'),
    },
    // 运营审计时间线（P1-15）：谁在什么时候改了什么
    {
      path: '/admin/audit',
      name: 'admin-audit',
      component: () => import('@/views/AdminAudit.vue'),
    },
    // 资讯审核与下架（P1-20）：软下架/恢复/置顶，理由必填 + 审计留痕
    {
      path: '/admin/news',
      name: 'admin-news',
      component: () => import('@/views/AdminNews.vue'),
    },
    // 评论治理（P1-22）：软隐藏/恢复，理由必填 + 审计留痕（与资讯审核同处方）
    {
      path: '/admin/comments',
      name: 'admin-comments',
      component: () => import('@/views/AdminComments.vue'),
    },
    // 敏感词库与命中日志（P1-23）：词库维护 + 误杀复盘的唯一证据
    {
      path: '/admin/words',
      name: 'admin-words',
      component: () => import('@/views/AdminWords.vue'),
    },
    // 昵称敏感词库与命中日志（P1-24）：注册与改名共用同一条校验链
    {
      path: '/admin/nickname-words',
      name: 'admin-nickname-words',
      component: () => import('@/views/AdminNicknameWords.vue'),
    },
    // 昵称待人工复核队列（P1-24）：机器放行、人再看一眼
    {
      path: '/admin/nickname-reviews',
      name: 'admin-nickname-reviews',
      component: () => import('@/views/AdminNicknameReviews.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});

/**
 * C4 路由守卫：受限账号（管理员刚代其重置过密码）只能去改密页。
 *
 * 为什么不在组件里各自判断：受限期有 20 多个页面，逐个加判断必然漏；
 * 漏掉的那个页面会让用户以为"改了就行"，然后在某个操作上撞 40305——
 * 比一开始就被拦住更让人困惑。
 */
router.beforeEach((to) => {
  // Pinia 在应用启动时已激活；守卫运行在路由解析阶段，此时 store 可用
  const user = useUserStore();
  if (!user.mustChangePassword) return true;
  if (to.name === 'change-password' || to.name === 'login') return true;
  return { name: 'change-password' };
});

export default router;
