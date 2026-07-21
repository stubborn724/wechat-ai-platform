<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import client from '@/api/client'
import { Finished, Document, Promotion, ChatDotSquare } from '@element-plus/icons-vue'

const auth = useAuthStore()
const router = useRouter()

const loading = ref(true)
const stats = ref({
  accounts: 0,
  articles: 0,
  pendingReview: 0,
  publishedToday: 0,
})

const recentJobs = ref<any[]>([])

async function loadDashboard() {
  loading.value = true
  try {
    const [jobRes, accountRes] = await Promise.all([
      client.get('/content-jobs?limit=6').catch(() => ({ data: { items: [] } })),
      client.get('/accounts').catch(() => ({ data: [] })),
    ])
    const jobs = jobRes.data.items || []
    const accounts = accountRes.data || []
    recentJobs.value = jobs.slice(0, 6)
    stats.value = {
      accounts: accounts.length,
      articles: jobs.length,
      pendingReview: jobs.filter((j: any) => j.status === 'awaiting_review').length,
      publishedToday: jobs.filter((j: any) => j.status === 'published').length,
    }
  } catch {
    // Defaults are fine
  } finally {
    loading.value = false
  }
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    draft: '草稿',
    queued: '排队中',
    generating: '生成中',
    awaiting_review: '待审核',
    approved: '已通过',
    scheduled: '已排期',
    publishing: '发布中',
    published: '已发布',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] || status
}

function statusType(status: string): string {
  if (['published'].includes(status)) return 'success'
  if (['failed', 'cancelled'].includes(status)) return 'danger'
  if (['awaiting_review'].includes(status)) return 'warning'
  if (['generating', 'publishing'].includes(status)) return 'primary'
  return 'info'
}

function viewJob(job: any) {
  router.push(`/content?job=${job.id}`)
}

onMounted(loadDashboard)
</script>

<template>
  <div class="dashboard-page">
    <!-- Page heading -->
    <div class="page-heading">
      <div>
        <p class="eyebrow">DASHBOARD</p>
        <h1>今天，内容流到哪了</h1>
        <p class="lead">先处理待审核，再关注正在生成和排期中的文章。</p>
      </div>
      <el-button type="primary" @click="router.push('/content')">
        发起新内容
      </el-button>
    </div>

    <!-- Loading state -->
    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <!-- Stats cards -->
    <template v-else>
      <el-row :gutter="20" class="stats-row">
        <el-col :xs="12" :sm="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-info">
                <p class="stat-label">待审核</p>
                <p class="stat-value warning">{{ stats.pendingReview }}</p>
              </div>
              <el-icon class="stat-icon warning-icon" :size="36">
                <Finished />
              </el-icon>
            </div>
            <p class="stat-desc">篇需要人工判断</p>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-info">
                <p class="stat-label">全部文章</p>
                <p class="stat-value">{{ stats.articles }}</p>
              </div>
              <el-icon class="stat-icon" :size="36">
                <Document />
              </el-icon>
            </div>
            <p class="stat-desc">内容任务总数</p>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-info">
                <p class="stat-label">已发布</p>
                <p class="stat-value success">{{ stats.publishedToday }}</p>
              </div>
              <el-icon class="stat-icon success-icon" :size="36">
                <Promotion />
              </el-icon>
            </div>
            <p class="stat-desc">已发布的文章</p>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-info">
                <p class="stat-label">公众号</p>
                <p class="stat-value">{{ stats.accounts }}</p>
              </div>
              <el-icon class="stat-icon" :size="36">
                <ChatDotSquare />
              </el-icon>
            </div>
            <p class="stat-desc">已绑定的公众号</p>
          </el-card>
        </el-col>
      </el-row>

      <!-- Recent content jobs -->
      <el-card shadow="never" class="recent-card">
        <template #header>
          <div class="card-header">
            <span><strong>最近内容任务</strong></span>
            <el-button text type="primary" @click="router.push('/content')">
              查看全部
            </el-button>
          </div>
        </template>

        <div v-if="recentJobs.length === 0" class="empty-state">
          <el-empty description="暂无内容任务">
            <el-button type="primary" @click="router.push('/content')">
              创建第一个任务
            </el-button>
          </el-empty>
        </div>

        <el-table v-else :data="recentJobs" stripe style="width: 100%">
          <el-table-column prop="topic" label="主题" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small" effect="plain">
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="170">
            <template #default="{ row }">
              {{ new Date(row.updated_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="viewJob(row)">
                查看
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page {
  max-width: 1200px;
}

.page-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 28px;
}

.eyebrow {
  font-size: 11px;
  letter-spacing: 0.15em;
  color: #909399;
  margin-bottom: 6px;
}

.page-heading h1 {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
  margin-bottom: 6px;
}

.lead {
  color: #909399;
  font-size: 14px;
}

.loading-section {
  padding: 40px 0;
}

.stats-row {
  margin-bottom: 24px;
}

.stat-card {
  margin-bottom: 20px;
  border-radius: 8px;
}

.stat-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.stat-info {
  flex: 1;
}

.stat-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 6px;
}

.stat-value {
  font-size: 32px;
  font-weight: 700;
  color: #303133;
  margin: 0;
  line-height: 1;
}

.stat-value.warning {
  color: #e6a23c;
}

.stat-value.success {
  color: #67c23a;
}

.stat-icon {
  color: #dcdfe6;
  flex-shrink: 0;
}

.warning-icon {
  color: #e6a23c;
}

.success-icon {
  color: #67c23a;
}

.stat-desc {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #f2f3f5;
}

.recent-card {
  border-radius: 8px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.empty-state {
  padding: 40px 0;
}
</style>
