<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { ContentJob, ContentJobArticle, Account, FeedSource } from '@/api/types'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(true)
const jobs = ref<ContentJob[]>([])
const accounts = ref<Account[]>([])
const feedSources = ref<FeedSource[]>([])
const showForm = ref(false)
const saving = ref(false)
const selected = ref<ContentJob | null>(null)
const selectedArticles = ref<ContentJobArticle[]>([])
const showArticles = ref(false)

const canCreate = computed(() =>
  ['super_administrator', 'enterprise_administrator', 'content_operator'].includes(auth.user?.role || '')
)

const form = reactive({
  topic: '',
  account_id: '',
  approval_mode: 'manual',
  use_multi_article: false,
  articles: [] as any[],
  footer_template: '',
  feed_source_ids: [] as string[],
})

const labels: Record<string, string> = {
  draft: '草稿', queued: '排队中', generating: '生成中',
  awaiting_review: '待审核', approved: '已通过', scheduled: '已排期',
  publishing: '发布中', published: '已发布', draft_saved: '草稿已存',
  failed: '失败', cancelled: '已取消',
}

const contentTypeOptions = [
  { value: 'image_text', label: '图文' },
  { value: 'video', label: '视频' },
  { value: 'pure_image', label: '纯图片' },
]

async function load() {
  loading.value = true
  try {
    const [j, a, f] = await Promise.all([
      client.get<ContentJobPage>('/content-jobs?limit=100'),
      client.get<{ total: number; items: Account[] }>('/accounts'),
      client.get<{ total: number; items: FeedSource[] }>('/feed-sources'),
    ])
    jobs.value = j.data.items || []
    accounts.value = a.data.items || []
    feedSources.value = f.data.items || []
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

function openForm() {
  form.topic = ''
  form.account_id = accounts.value[0]?.id.toString() || ''
  form.approval_mode = 'manual'
  form.use_multi_article = false
  form.articles = []
  form.footer_template = ''
  form.feed_source_ids = []
  showForm.value = true
}

function addArticleSlot() {
  form.articles.push({
    content_type: 'image_text',
    sort_order: form.articles.length,
    publish_domain: 'public',
    topic: '',
  })
}

function removeArticleSlot(index: number) {
  form.articles.splice(index, 1)
  form.articles.forEach((s, i) => { s.sort_order = i })
}

async function create() {
  saving.value = true
  try {
    await client.post('/content-jobs', {
      topic: form.topic,
      account_id: form.account_id || null,
      content_type: 'article',
      approval_mode: form.approval_mode,
      idempotency_key: crypto.randomUUID(),
      footer_template: form.footer_template || undefined,
    })
    showForm.value = false
    ElMessage.success('内容任务已创建')
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
  }
}

async function confirmDelete(job: ContentJob) {
  try {
    await ElMessageBox.confirm(
      `确定删除任务「${job.latest_version?.title || job.topic}」？`,
      '确认删除',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await client.delete(`/content-jobs/${job.id}`)
    ElMessage.success('已删除')
    await load()
  } catch {
    // cancelled
  }
}

async function transition(job: ContentJob, action: string) {
  try {
    const { data } = await client.post<ContentJob>(`/content-jobs/${job.id}/transition`, {
      action,
    })
    Object.assign(job, data)
    ElMessage.success(action === 'queue' ? '任务已进入生成队列' : '任务已取消')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '操作失败')
  }
}

async function viewArticles(job: ContentJob) {
  selected.value = job
  ElMessage.info('文章详情功能开发中')
  try {
    // Try to fetch articles from versions endpoint
    const res = await client.get(`/content-jobs/${job.id}/versions`)
    selectedArticles.value = Array.isArray(res.data) ? res.data : []
    showArticles.value = true
  } catch {
    ElMessage.warning('文章列表接口暂时不可用')
  }
}

function statusType(status: string): string {
  if (['published', 'draft_saved'].includes(status)) return 'success'
  if (['failed', 'cancelled'].includes(status)) return 'danger'
  if (['awaiting_review'].includes(status)) return 'warning'
  if (['generating', 'publishing', 'queued'].includes(status)) return 'primary'
  return 'info'
}

const contentTypeLabels: Record<string, string> = {
  image_text: '图文', video: '视频', pure_image: '纯图片',
}

onMounted(load)
</script>

<template>
  <div class="content-jobs-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">CONTENT PIPELINE</p>
        <h1>内容任务</h1>
        <p class="lead">管理所有 AI 内容生成任务，从草稿到发布的全流程追踪。</p>
      </div>
      <el-button v-if="canCreate" type="primary" @click="openForm">创建内容任务</el-button>
    </div>

    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="jobs.length === 0" class="empty-state">
      <el-empty description="还没有内容任务">
        <el-button v-if="canCreate" type="primary" @click="openForm">创建第一个任务</el-button>
      </el-empty>
    </div>

    <el-table v-else :data="jobs" stripe style="width: 100%">
      <el-table-column label="文章/主题" min-width="240">
        <template #default="{ row }">
          <div class="job-title-cell">
            <strong>{{ row.latest_version?.title || row.topic }}</strong>
            <span class="job-summary">{{ row.latest_version?.summary || row.topic }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status) as any" size="small">
            {{ labels[row.status] || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="170">
        <template #default="{ row }">
          {{ new Date(row.updated_at).toLocaleString('zh-CN') }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'draft'"
            size="small"
            type="primary"
            @click="transition(row, 'queue')"
          >
            开始生成
          </el-button>
          <el-button
            v-if="['draft', 'queued', 'awaiting_review', 'approved', 'scheduled'].includes(row.status)"
            size="small"
            @click="transition(row, 'cancel')"
          >
            取消
          </el-button>
          <el-button size="small" @click="viewArticles(row)">文章</el-button>
          <el-button size="small" type="danger" @click="confirmDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- Create Dialog -->
    <el-dialog v-model="showForm" title="创建内容任务" width="580px">
      <el-form label-position="top">
        <el-form-item label="文章主题" required>
          <el-input
            v-model="form.topic"
            type="textarea"
            :rows="3"
            placeholder="例如：人工智能如何改变教育行业"
          />
        </el-form-item>

        <el-form-item label="目标公众号">
          <el-select v-model="form.account_id" style="width: 100%" clearable>
            <el-option value="" label="暂不指定" />
            <el-option
              v-for="account in accounts"
              :key="account.id"
              :value="account.id.toString()"
              :label="account.name"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="发布方式">
          <el-select v-model="form.approval_mode" style="width: 100%">
            <el-option value="manual" label="人工审核后发布" />
            <el-option value="auto" label="自动存微信草稿箱" />
          </el-select>
        </el-form-item>

        <el-form-item>
          <el-checkbox v-model="form.use_multi_article">
            多文章模式（一次生成多篇不同风格的文章）
          </el-checkbox>
        </el-form-item>

        <div v-if="form.use_multi_article" class="slots-section">
          <div v-for="(slot, i) in form.articles" :key="i" class="slot-row">
            <span class="slot-index">#{{ i + 1 }}</span>
            <el-select v-model="slot.content_type" style="width: 100px">
              <el-option v-for="opt in contentTypeOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
            </el-select>
            <el-select v-model="slot.publish_domain" style="width: 80px">
              <el-option value="public" label="公域" />
              <el-option value="private" label="私域" />
            </el-select>
            <el-input v-model="slot.topic" placeholder="自定义主题（可选）" style="flex: 1" />
            <el-button size="small" type="danger" @click="removeArticleSlot(i)">删除</el-button>
          </div>
          <el-button size="small" @click="addArticleSlot">+ 添加文章槽</el-button>
        </div>

        <el-form-item label="文章底部固定内容（可选）">
          <el-input
            v-model="form.footer_template"
            type="textarea"
            :rows="2"
            placeholder="例如联系方式、二维码说明等"
          />
        </el-form-item>

        <el-form-item v-if="feedSources.length > 0" label="参考投喂源">
          <el-checkbox-group v-model="form.feed_source_ids">
            <el-checkbox v-for="fs in feedSources" :key="fs.id" :value="fs.id.toString()">
              {{ fs.name }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">保存为草稿</el-button>
      </template>
    </el-dialog>

    <!-- Articles Dialog -->
    <el-dialog v-model="showArticles" title="文章列表" width="650px">
      <div v-if="selectedArticles.length === 0" class="empty-state">
        <el-empty description="暂无文章槽配置" />
      </div>
      <el-table v-else :data="selectedArticles" stripe style="width: 100%">
        <el-table-column label="#" width="60">
          <template #default="{ row }"> #{{ row.sort_order + 1 }} </template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ contentTypeLabels[row.content_type] || row.content_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="域" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.publish_domain === 'public' ? '' : 'warning'">
              {{ row.publish_domain === 'public' ? '公域' : '私域' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status) as any">
              {{ labels[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="showArticles = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.content-jobs-page {
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

.empty-state {
  padding: 60px 0;
}

.job-title-cell {
  display: grid;
  gap: 4px;
}

.job-title-cell strong {
  font-size: 15px;
  font-weight: 600;
}

.job-summary {
  color: #909399;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.slots-section {
  margin-bottom: 18px;
}

.slot-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.slot-index {
  font-size: 12px;
  color: #909399;
  min-width: 24px;
}
</style>
