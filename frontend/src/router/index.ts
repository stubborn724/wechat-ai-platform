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
        { path: 'optimizations', name: 'OptimizationReview', component: () => import('@/views/OptimizationReviewView.vue') },
        { path: 'statistics', name: 'Statistics', component: () => import('@/views/StatisticsView.vue') },
        { path: 'comments', name: 'Comments', component: () => import('@/views/CommentsView.vue') },
        { path: 'messages', name: 'Messages', component: () => import('@/views/MessagesView.vue') },
        { path: 'leads', name: 'CommentLeads', component: () => import('@/views/CommentLeadWorkbench.vue') },
        { path: 'leads/packages', name: 'ContactPackages', component: () => import('@/views/config/ContactPackageList.vue') },
        { path: 'leads/packages/new', name: 'NewContactPackage', component: () => import('@/views/config/ContactPackageForm.vue') },
        { path: 'leads/packages/:id/edit', name: 'EditContactPackage', component: () => import('@/views/config/ContactPackageForm.vue') },
        { path: 'leads/rules', name: 'AutomationRules', component: () => import('@/views/config/AutomationRulesView.vue') },
        { path: 'leads/templates', name: 'MessageTemplates', component: () => import('@/views/config/MessageTemplatesView.vue') },
        { path: 'leads/stats', name: 'LeadStats', component: () => import('@/views/config/LeadStatsView.vue') },
{ path: 'imitation/pools', name: 'ImitationPools', component: () => import('@/views/ImitationPoolsView.vue') },
        { path: 'imitation/tasks', name: 'ImitationTasks', component: () => import('@/views/ImitationTasksView.vue') },
        { path: 'watermark', name: 'WatermarkSettings', component: () => import('@/views/WatermarkSettingsView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})

// Navigation guard
router.beforeEach(async (to, from, next) => {
  const requiresAuth = to.matched.some(record => record.meta.requiresAuth !== false)
  if (!requiresAuth || to.path === '/login') {
    next()
    return
  }

  // 优先通过 HttpOnly cookie 认证（由后端自动处理）
  // 检查 localStorage 中是否有 token（兼容旧版）
  const hasToken = !!localStorage.getItem('access_token')

  if (!hasToken && to.path !== '/login') {
    next('/login')
  } else {
    next()
  }
})

export default router
