import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/LoginView.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/',
      component: () => import('@/layouts/OperationsLayout.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'Dashboard', component: () => import('@/views/DashboardView.vue') },
        { path: 'accounts', name: 'Accounts', component: () => import('@/views/AccountsView.vue') },
        { path: 'articles', name: 'Articles', component: () => import('@/views/article/ArticleCreateView.vue') },
        { path: 'articles/list', name: 'ArticleList', component: () => import('@/views/article/ArticleListView.vue') },
        { path: 'articles/synced/:id', name: 'SyncedArticleDetail', component: () => import('@/views/article/SyncedArticleDetailView.vue') },
        { path: 'articles/:taskId', name: 'ArticleDetail', component: () => import('@/views/article/ArticleDetailView.vue') },
        { path: 'assets', name: 'Assets', component: () => import('@/views/AssetsView.vue') },
        { path: 'feed-sources', name: 'FeedSources', component: () => import('@/views/FeedSourcesView.vue') },
        { path: 'knowledge', name: 'KnowledgeBases', component: () => import('@/views/KnowledgeBasesView.vue') },
        { path: 'publish-plans', name: 'PublishPlans', component: () => import('@/views/PublishPlansView.vue') },
        { path: 'scheduled-tasks', name: 'ScheduledTasks', component: () => import('@/views/ScheduledTasksView.vue') },
        { path: 'reviews', name: 'Reviews', component: () => import('@/views/ReviewsView.vue') },
        { path: 'comments', name: 'Comments', component: () => import('@/views/CommentsView.vue') },
        { path: 'messages', name: 'Messages', component: () => import('@/views/MessagesView.vue') },
        { path: 'imitation/pools', name: 'ImitationPools', component: () => import('@/views/ImitationPoolsView.vue') },
        { path: 'imitation/tasks', name: 'ImitationTasks', component: () => import('@/views/ImitationTasksView.vue') },
        { path: 'watermark', name: 'WatermarkSettings', component: () => import('@/views/WatermarkSettingsView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})

// Navigation guard
router.beforeEach((to, from, next) => {
  const requiresAuth = to.matched.some(record => record.meta.requiresAuth !== false)
  const token = localStorage.getItem('access_token')
  if (requiresAuth && !token && to.path !== '/login') {
    next('/login')
  } else {
    next()
  }
})

export default router
