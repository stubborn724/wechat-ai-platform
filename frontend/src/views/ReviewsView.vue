<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import type { ContentJob } from '@/api/types'

const jobs = ref<ContentJob[]>([])
const loading = ref(true)
const selected = ref<ContentJob | null>(null)
const decision = ref<'approve' | 'reject'>('approve')
const comment = ref('')
const targeted = ref(false)
const saving = ref(false)

const awaiting = computed(() =>
  jobs.value.filter(j => j.status === 'awaiting_review')
)

async function load() {
  loading.value = true
  try {
    const res = await client.get<{ items: ContentJob[] }>('/content-jobs?limit=100')
    jobs.value = res.data.items || []
    selected.value = awaiting.value[0] || null
  } catch {
    ElMessage.error('加载审核列表失败')
  } finally {
    loading.value = false
  }
}

function choose(job: ContentJob) {
  selected.value = job
  decision.value = 'approve'
  comment.value = ''
  targeted.value = false
}

async function submit() {
  if (!selected.value) return
  if (decision.value === 'reject' && !comment.value.trim()) {
    ElMessage.warning('退回时请填写修改原因')
    return
  }
  saving.value = true
  try {
    await client.post('/reviews', {
      job_id: selected.value.id,
      decision: decision.value === 'approve' ? 'approved' : 'rejected',
      comment: comment.value || null,
    })
    ElMessage.success(decision.value === 'approve' ? '审核已通过' : '已退回修改')
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '审核失败')
  } finally {
    saving.value = false
  }
}

const statusLabels: Record<string, string> = {
  draft: '草稿',
  queued: '排队中',
  generating: '生成中',
  awaiting_review: '待审核',
  approved: '已通过',
  scheduled: '已排期',
  publishing: '发布中',
  published: '已发布',
  draft_saved: '草稿已存',
  failed: '失败',
  cancelled: '已取消',
}

onMounted(load)
</script>

<template>
  <div class="reviews-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">HUMAN REVIEW</p>
        <h1>审核台</h1>
        <p class="lead">AI 负责起稿，人负责事实、品牌口径与最终判断。</p>
      </div>
      <div class="review-count">
        <strong>{{ awaiting.length }}</strong> 待审核
      </div>
    </div>

    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="awaiting.length === 0" class="empty-state">
      <el-empty description="审核队列已清空，目前没有等待人工确认的文章。">
      </el-empty>
    </div>

    <div v-else class="review-desk">
      <!-- Sidebar: Job list -->
      <aside class="review-sidebar">
        <p class="queue-label">待审校样</p>
        <div
          v-for="(job, index) in awaiting"
          :key="job.id"
          class="review-job-item"
          :class="{ active: selected?.id === job.id }"
          @click="choose(job)"
        >
          <span class="job-index">{{ String(index + 1).padStart(2, '0') }}</span>
          <div class="job-info">
            <strong>{{ job.latest_version?.title || job.topic }}</strong>
            <small>{{ new Date(job.updated_at).toLocaleString('zh-CN') }}</small>
          </div>
        </div>
      </aside>

      <!-- Main: Review content -->
      <article v-if="selected" class="proof-page">
        <header class="proof-header">
          <div>
            <p class="eyebrow">PROOF NO. {{ selected.latest_version?.version_number || 1 }}</p>
            <h2>{{ selected.latest_version?.title || selected.topic }}</h2>
            <p class="proof-summary">{{ selected.latest_version?.summary }}</p>
          </div>
          <div class="proof-stamp">待审</div>
        </header>

        <div class="proof-meta">
          <span>主题：{{ selected.topic }}</span>
          <span>来源：{{ selected.latest_version?.source || 'AI Worker' }}</span>
        </div>

        <div class="proof-body">
          {{ selected.latest_version?.body_markdown }}
        </div>

        <footer class="proof-footer">
          <div class="decision-tabs">
            <el-button
              :type="decision === 'approve' ? 'primary' : 'default'"
              @click="decision = 'approve'"
            >
              通过
            </el-button>
            <el-button
              :type="decision === 'reject' ? 'danger' : 'default'"
              @click="decision = 'reject'"
            >
              退回修改
            </el-button>
          </div>

          <el-input
            v-model="comment"
            type="textarea"
            :rows="3"
            :placeholder="decision === 'approve' ? '可选：记录事实和品牌口径检查结果' : '必填：指出需要修改的具体位置和原因'"
          />

          <div v-if="decision === 'reject'" class="targeted-option">
            <el-checkbox v-model="targeted">
              立即返回生成队列，按意见定向重写
            </el-checkbox>
          </div>

          <el-button
            :type="decision === 'approve' ? 'primary' : 'danger'"
            :loading="saving"
            @click="submit"
          >
            {{ saving ? '正在记录...' : decision === 'approve' ? '确认通过' : '确认退回' }}
          </el-button>
        </footer>
      </article>
    </div>
  </div>
</template>

<style scoped>
.reviews-page {
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

.review-count {
  display: flex;
  align-items: baseline;
  gap: 8px;
  color: #909399;
  font-size: 13px;
}

.review-count strong {
  color: #e6a23c;
  font-size: 42px;
  font-weight: 500;
}

.loading-section {
  padding: 40px 0;
}

.empty-state {
  padding: 60px 0;
}

.review-desk {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 24px;
  align-items: start;
}

.review-sidebar {
  border-top: 2px solid #303133;
}

.queue-label {
  margin: 0;
  padding: 13px 8px;
  color: #909399;
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.review-job-item {
  display: grid;
  grid-template-columns: 27px 1fr;
  gap: 10px;
  padding: 15px 9px;
  border-top: 1px solid #e4e7ed;
  cursor: pointer;
  transition: background 0.15s;
}

.review-job-item:hover {
  background: #f5f7fa;
}

.review-job-item.active {
  background: #ecf5ff;
  box-shadow: inset 3px 0 #409eff;
}

.job-index {
  color: #909399;
  font-size: 10px;
  font-family: monospace;
  line-height: 1.45;
}

.job-info strong {
  display: block;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.45;
}

.job-info small {
  display: block;
  margin-top: 6px;
  color: #909399;
  font-size: 10px;
}

/* Proof page */
.proof-page {
  padding: 32px;
  border: 1px solid #e4e7ed;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  border-radius: 8px;
}

.proof-header {
  display: flex;
  gap: 30px;
  justify-content: space-between;
  padding-bottom: 24px;
  border-bottom: 2px solid #303133;
}

.proof-header h2 {
  margin-bottom: 12px;
  font-size: 24px;
  font-weight: 700;
}

.proof-summary {
  color: #606266;
  line-height: 1.65;
  margin: 0;
}

.proof-stamp {
  display: grid;
  min-width: 64px;
  height: 64px;
  place-items: center;
  border: 3px double #e6a23c;
  border-radius: 50%;
  color: #e6a23c;
  font-weight: 700;
  font-size: 19px;
  transform: rotate(-9deg);
  flex-shrink: 0;
}

.proof-meta {
  display: flex;
  gap: 24px;
  padding: 12px 0;
  border-bottom: 1px solid #e4e7ed;
  color: #909399;
  font-size: 10px;
  font-family: monospace;
}

.proof-body {
  min-height: 180px;
  padding: 32px 4px;
  white-space: pre-wrap;
  font-size: 15px;
  line-height: 2;
  color: #303133;
}

.proof-footer {
  display: grid;
  gap: 16px;
  padding-top: 24px;
  border-top: 1px solid #e4e7ed;
}

.decision-tabs {
  display: flex;
  gap: 8px;
}

.targeted-option {
  font-size: 13px;
}

@media (max-width: 820px) {
  .review-desk {
    grid-template-columns: 1fr;
  }

  .review-sidebar {
    display: flex;
    overflow: auto;
    border-bottom: 1px solid #e4e7ed;
    border-top: none;
  }

  .queue-label {
    display: none;
  }

  .review-job-item {
    min-width: 220px;
    border-right: 1px solid #e4e7ed;
    border-top: none;
  }
}

@media (max-width: 520px) {
  .proof-page {
    padding: 20px 16px;
  }

  .proof-stamp {
    min-width: 52px;
    height: 52px;
    font-size: 15px;
  }

  .proof-meta {
    flex-direction: column;
    gap: 4px;
  }

  .proof-body {
    padding: 24px 2px;
  }
}
</style>
