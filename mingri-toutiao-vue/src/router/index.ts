import { createRouter, createWebHistory } from 'vue-router';

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
    { path: '/search', name: 'search', component: () => import('@/views/Search.vue') },
    {
      path: '/search/results',
      name: 'search-results',
      component: () => import('@/views/SearchResults.vue'),
    },
    { path: '/favorites', name: 'favorites', component: () => import('@/views/Favorites.vue') },
    { path: '/history', name: 'history', component: () => import('@/views/History.vue') },
    // 运营向入口：不在 TabBar 中，仅对管理员在「我的」页展示（后端按 ADMIN_PHONES 判定）
    {
      path: '/admin/sources',
      name: 'admin-sources',
      component: () => import('@/views/AdminSources.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});

export default router;
