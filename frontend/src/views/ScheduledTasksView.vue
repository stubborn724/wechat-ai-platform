<script setup lang="ts">
import { onMounted, computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { Account, FeedSource } from '@/api/types'

interface ArticleSlot {
  content_type: string
  publish_domain: string
}

interface ScheduledTask {
  id: number
  tenant_id: number
  name: string
  is_active: boolean
  writing_mode: string
  topic: string | null
  feed_source_ids: number[] | null
  style: string | null
  knowledge_base_ids: number[] | null
  day_of_week: number
  publish_times: string[]
  article_slots: ArticleSlot[] | null
  articles_per_day: number
  public_count: number
  private_count: number
  approval_mode: string
  account_ids: number[] | null
  publish_mode: string
  image_source: string
  footer_template: string | null
  total_generated: number
  last_run_at: string | null
  created_at: string
  updated_at: string
}

const loading = ref(false)
const saving = ref(false)
const uploadingQr = ref(false)
const footerQrUrl = ref('')
const qrFileInput = ref<HTMLInputElement | null>(null)
const tasks = ref<ScheduledTask[]>([])
const accounts = ref<Account[]>([])
const feedSources = ref<FeedSource[]>([])
const knowledgeBases = ref<any[]>([])
const showForm = ref(false)
const editing = ref(false)
const currentId = ref<number | null>(null)

const form = reactive({
  name: '',
  writing_mode: 'free',
  topic: '',
  feed_source_ids: [] as number[],
  style: '',
  knowledge_base_ids: [] as number[],
  day_of_week: -1,
  publish_times: [] as string[],
  article_slots: [] as ArticleSlot[],
  articles_per_day: 1,
  public_count: 1,
  private_count: 0,
  approval_mode: 'auto',
  account_ids: [] as number[],
  publish_mode: 'draft',
  image_source: 'pexels',
  footer_template: '',
})

const dayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const dayOptions = [
  { value: -1, label: '每天' },
  ...dayLabels.map((label, i) => ({ value: i, label })),
]

const contentTypeOptions = [
  { value: 'image_text', label: '图文' },
  { value: 'video', label: '视频' },
  { value: 'pure_image', label: '纯图片' },
]
const domainOptions = [
  { value: 'public', label: '公域' },
  { value: 'private', label: '私域' },
]

const writingModeLabel: Record<string, string> = {
  free: '自由写作',
  feed: '投喂源仿写',
  kb: '知识库',
}

const styleOptions = [
  { value: '', label: '默认风格' },
  { value: 'tech', label: '科技风格' },
  { value: 'emotional', label: '情感风格' },
  { value: 'educational', label: '教育风格' },
  { value: 'humorous', label: '幽默风格' },
]

function getAccountName(ids: number[] | null): string {
  if (!ids || ids.length === 0) return '未指定'
  return ids.map(id => accounts.value.find(a => a.id === id)?.name || `#${id}`).join(', ')
}

function getFeedSourceNames(ids: number[] | null): string {
  if (!ids || ids.length === 0) return '-'
  return ids.map(id => feedSources.value.find(f => f.id === id)?.name || `#${id}`).join(', ')
}

async function load() {
  loading.value = true
  try {
    const [t, a, f, k] = await Promise.all([
      client.get<{ total: number; items: ScheduledTask[] }>('/scheduled-tasks'),
      client.get<{ items: Account[] }>('/accounts'),
      client.get<{ total: number; items: FeedSource[] }>('/feed-sources').catch(() => ({ data: { items: [] } })),
      client.get<{ items: any[] }>('/knowledge-bases').catch(() => ({ data: { items: [] } })),
    ])
    tasks.value = t.data.items || []
    accounts.value = a.data.items || []
    feedSources.value = f.data.items || []
    knowledgeBases.value = k.data.items || []
  } catch { ElMessage.error('加载失败') }
  finally { loading.value = false }
}

function resetForm() {
  form.name = ''
  form.writing_mode = 'free'
  form.topic = ''
  form.feed_source_ids = []
  form.style = ''
  form.knowledge_base_ids = []
  form.day_of_week = -1
  form.publish_times = []
  form.article_slots = []
  form.articles_per_day = 1
  form.public_count = 1
  form.private_count = 0
  form.approval_mode = 'auto'
  form.account_ids = []
  form.publish_mode = 'draft'
  form.image_source = 'pexels'
  form.footer_template = ''
  footerQrUrl.value = ''
  editing.value = false
  currentId.value = null
}

function openCreate() { resetForm(); showForm.value = true }

function openEdit(task: ScheduledTask) {
  editing.value = true
  currentId.value = task.id
  form.name = task.name
  form.writing_mode = task.writing_mode
  form.topic = task.topic || ''
  form.feed_source_ids = task.feed_source_ids || []
  form.style = task.style || ''
  form.knowledge_base_ids = task.knowledge_base_ids || []
  form.day_of_week = task.day_of_week
  form.publish_times = [...task.publish_times]
  form.article_slots = task.article_slots ? task.article_slots.map(s => ({ ...s })) : []
  form.articles_per_day = task.articles_per_day
  form.public_count = task.public_count
  form.private_count = task.private_count
  form.approval_mode = task.approval_mode
  form.account_ids = task.account_ids || []
  form.publish_mode = task.publish_mode || 'draft'
  form.image_source = task.image_source || 'pexels'
  form.footer_template = task.footer_template || ''
  // 从 footer_template 中提取二维码 URL 用于预览
  const qrMatch = form.footer_template.match(/!\[二维码\]\(([^)]+)\)/)
  footerQrUrl.value = qrMatch ? qrMatch[1] : ''
  showForm.value = true
}

function addSlot() {
  form.article_slots.push({ content_type: 'image_text', publish_domain: 'public' })
}
function removeSlot(i: number) { form.article_slots.splice(i, 1) }
function addTime() { form.publish_times.push('08:00') }
function removeTime(i: number) { form.publish_times.splice(i, 1) }

async function uploadFooterQr(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  uploadingQr.value = true
  try {
    const formData = new FormData()
    formData.append('file', input.files[0])
    const res = await client.post('/assets/upload', formData, {
      params: { asset_type: 'image' },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const asset = res.data.data || res.data
    footerQrUrl.value = asset.preview_url || ''
    const qrMd = footerQrUrl.value ? `![二维码](${footerQrUrl.value})` : ''
    form.footer_template = qrMd ? `${qrMd}\n\n${form.footer_template.replace(/!\[二维码\]\(.*?\)\n\n/, '')}` : form.footer_template
    ElMessage.success('二维码已上传')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '二维码上传失败')
  } finally {
    uploadingQr.value = false
    input.value = '' as any
  }
}

async function save() {
  saving.value = true
  try {
    const payload: Record<string, any> = {
      name: form.name,
      writing_mode: form.writing_mode,
      topic: form.topic || null,
      feed_source_ids: form.writing_mode === 'feed' && form.feed_source_ids.length > 0 ? form.feed_source_ids : null,
      style: form.style || null,
      knowledge_base_ids: form.knowledge_base_ids.length > 0 ? form.knowledge_base_ids : null,
      day_of_week: form.day_of_week,
      publish_times: form.publish_times,
      article_slots: form.article_slots.length > 0 ? form.article_slots : null,
      articles_per_day: form.articles_per_day,
      public_count: form.public_count,
      private_count: form.private_count,
      approval_mode: form.approval_mode,
      account_ids: form.account_ids.length > 0 ? form.account_ids : null,
      publish_mode: form.publish_mode,
      image_source: form.image_source,
      footer_template: form.footer_template || null,
    }

    if (editing.value && currentId.value) {
      await client.put(`/scheduled-tasks/${currentId.value}`, payload)
    } else {
      await client.post('/scheduled-tasks', payload)
    }
    ElMessage.success(editing.value ? '已更新' : '已创建')
    showForm.value = false
    await load()
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '保存失败') }
  finally { saving.value = false }
}

async function toggleTask(task: ScheduledTask) {
  try {
    const { data } = await client.post<ScheduledTask>(`/scheduled-tasks/${task.id}/toggle`)
    Object.assign(task, data)
  } catch { ElMessage.error('操作失败') }
}

async function confirmDelete(task: ScheduledTask) {
  try {
    await ElMessageBox.confirm(`确定删除「${task.name}」？`, '确认删除')
    await client.delete(`/scheduled-tasks/${task.id}`)
    ElMessage.success('已删除')
    await load()
  } catch { /* cancelled */ }
}

onMounted(load)
</script>

<template>
  <div class="scheduled-tasks-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">SCHEDULED TASKS</p>
        <h1>定时任务</h1>
        <p class="lead">统一管理所有定时写作任务 — 自由写作 / 投喂源仿写 / 知识库</p>
      </div>
      <el-button type="primary" @click="openCreate">+ 新建任务</el-button>
    </div>

    <div v-if="loading" class="loading-section"><el-skeleton :rows="3" animated /></div>

    <div v-else-if="tasks.length === 0" class="empty-state">
      <el-empty description="暂无定时任务">
        <el-button type="primary" @click="openCreate">创建第一个任务</el-button>
      </el-empty>
    </div>

    <div v-else class="task-grid">
      <div v-for="task in tasks" :key="task.id" class="task-card">
        <div class="card-header">
          <div class="card-title">
            <strong>{{ task.name }}</strong>
            <el-tag size="small" :type="task.writing_mode === 'feed' ? 'warning' : task.writing_mode === 'kb' ? 'success' : ''">
              {{ writingModeLabel[task.writing_mode] || task.writing_mode }}
            </el-tag>
          </div>
          <el-tag :type="task.is_active ? 'success' : 'info'" size="small">
            {{ task.is_active ? '启用' : '停用' }}
          </el-tag>
        </div>

        <div class="card-body">
          <div class="info-row">
            <span class="label">目标公众号</span>
            <span>{{ getAccountName(task.account_ids) }}</span>
          </div>
          <div v-if="task.topic" class="info-row">
            <span class="label">主题</span>
            <span class="topic-preview">{{ task.topic.slice(0, 60) }}{{ task.topic.length > 60 ? '…' : '' }}</span>
          </div>
          <div v-if="task.writing_mode === 'feed'" class="info-row">
            <span class="label">投喂源</span>
            <span>{{ getFeedSourceNames(task.feed_source_ids) }}</span>
          </div>
          <div v-if="task.style" class="info-row">
            <span class="label">写作风格</span>
            <span>{{ task.style }}</span>
          </div>
          <div class="info-row">
            <span class="label">日程</span>
            <span>{{ dayOptions.find(d => d.value === task.day_of_week)?.label || task.day_of_week }}</span>
          </div>
          <div class="info-row">
            <span class="label">发布时间</span>
            <span>
              <el-tag v-for="t in task.publish_times" :key="t" size="small" style="margin-right:4px">{{ t }}</el-tag>
            </span>
          </div>
          <div class="info-row">
            <span class="label">篇数/天</span>
            <span>{{ task.articles_per_day }} 篇 (公域 {{ task.public_count }} / 私域 {{ task.private_count }})</span>
          </div>
          <div class="info-row">
            <span class="label">发布方式</span>
            <span>{{ task.approval_mode === 'auto' ? '自动发布' : '人工审核' }}</span>
          </div>
          <div class="info-row">
            <span class="label">发布到</span>
            <span>{{ task.publish_mode === 'direct' ? '直接发布' : '存草稿箱' }}</span>
          </div>
          <div class="info-row">
            <span class="label">图片来源</span>
            <span>{{ { pexels: 'Pexels 图库', local: '本地素材', DASHSCOPE: 'AI 生图' }[task.image_source] || task.image_source }}</span>
          </div>
          <div class="info-row">
            <span class="label">已生成</span>
            <span>{{ task.total_generated }} 篇</span>
          </div>
        </div>

        <div class="card-actions">
          <el-button size="small" @click="openEdit(task)">编辑</el-button>
          <el-button size="small" :type="task.is_active ? 'warning' : 'success'" plain @click="toggleTask(task)">
            {{ task.is_active ? '停用' : '启用' }}
          </el-button>
          <el-button size="small" type="danger" plain @click="confirmDelete(task)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- Create/Edit Dialog -->
    <el-dialog v-model="showForm" :title="editing ? '编辑定时任务' : '新建定时任务'" width="640px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="任务名称" required>
          <el-input v-model="form.name" placeholder="例如：每日科技资讯" />
        </el-form-item>

        <el-form-item label="写作模式">
          <el-radio-group v-model="form.writing_mode">
            <el-radio value="free">自由写作</el-radio>
            <el-radio value="feed">投喂源仿写</el-radio>
            <el-radio value="kb">知识库</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- Topic (for all modes) -->
        <el-form-item label="写作主题" required>
          <el-input v-model="form.topic" type="textarea" :rows="3" placeholder="例如：2026年米兰展最新流行趋势分析" />
        </el-form-item>

        <!-- Style selector (like article creation) -->
        <el-form-item label="写作风格">
          <el-select v-model="form.style" clearable placeholder="选择风格（可选）" style="width:100%">
            <el-option v-for="opt in styleOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
          </el-select>
        </el-form-item>

        <!-- Feed mode: feed source selector (replaces imitation pool) -->
        <template v-if="form.writing_mode === 'feed'">
          <el-form-item label="投喂源" required>
            <el-select v-model="form.feed_source_ids" style="width:100%" multiple filterable>
              <el-option v-for="src in feedSources" :key="src.id" :value="src.id" :label="src.name" />
            </el-select>
          </el-form-item>
        </template>

        <!-- KB mode: knowledge base selector -->
        <el-form-item v-if="form.writing_mode === 'kb'" label="知识库" required>
          <el-select v-model="form.knowledge_base_ids" style="width:100%" multiple filterable>
            <el-option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id" :label="kb.name" />
          </el-select>
        </el-form-item>

        <!-- KB supplement (for free & feed modes) -->
        <el-form-item v-if="form.writing_mode !== 'kb'" label="补充知识库（可选）">
          <el-select v-model="form.knowledge_base_ids" style="width:100%" multiple filterable clearable placeholder="选择知识库补充内容">
            <el-option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id" :label="kb.name" />
          </el-select>
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="适用日期">
              <el-select v-model="form.day_of_week" style="width:100%">
                <el-option v-for="d in dayOptions" :key="d.value" :value="d.value" :label="d.label" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="每天篇数">
              <el-input-number v-model="form.articles_per_day" :min="1" :max="50" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="发布时间">
          <div class="time-container">
            <div v-for="(t, i) in form.publish_times" :key="i" class="time-row">
              <el-time-picker v-model="form.publish_times[i]" format="HH:mm" value-format="HH:mm" style="width:140px" />
              <el-button size="small" type="danger" plain @click="removeTime(i)">删除</el-button>
            </div>
            <el-button size="small" @click="addTime">+ 添加时间</el-button>
          </div>
        </el-form-item>

        <el-form-item label="文章槽（内容类型 + 发布域）">
          <div class="slots-container">
            <div v-for="(slot, i) in form.article_slots" :key="i" class="slot-row">
              <span class="slot-index">#{{ i + 1 }}</span>
              <el-select v-model="slot.content_type" style="width:120px">
                <el-option v-for="opt in contentTypeOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
              </el-select>
              <el-select v-model="slot.publish_domain" style="width:100px">
                <el-option v-for="opt in domainOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
              </el-select>
              <el-button size="small" type="danger" plain @click="removeSlot(i)">删除</el-button>
            </div>
            <el-button size="small" @click="addSlot">+ 添加文章槽</el-button>
          </div>
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="公域篇数">
              <el-input-number v-model="form.public_count" :min="0" :max="50" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="私域篇数">
              <el-input-number v-model="form.private_count" :min="0" :max="50" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="发布方式">
          <el-radio-group v-model="form.approval_mode">
            <el-radio value="auto">自动存微信草稿箱</el-radio>
            <el-radio value="manual">人工审核后发布</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="目标公众号">
          <el-select v-model="form.account_ids" multiple collapse-tags collapse-tags-tooltip style="width:100%" filterable clearable placeholder="选择公众号（可多选）">
            <el-option v-for="acct in accounts" :key="acct.id" :value="acct.id" :label="acct.name" />
          </el-select>
        </el-form-item>

        <el-form-item label="发布方式">
          <el-radio-group v-model="form.publish_mode" :disabled="form.account_ids.length === 0">
            <el-radio value="draft">存草稿箱</el-radio>
            <el-radio value="direct">直接发布</el-radio>
          </el-radio-group>
          <span v-if="form.account_ids.length > 0 && form.publish_mode === 'direct'" class="form-hint" style="display:block;margin-top:4px">
            ⚠️ 直接发布将立即推送给订阅用户
          </span>
          <span v-else class="form-hint" style="display:block;margin-top:4px">保存到微信草稿箱，可手动检查后再发布</span>
        </el-form-item>

        <el-form-item label="图片来源">
          <el-radio-group v-model="form.image_source">
            <el-radio value="pexels">Pexels 图库</el-radio>
            <el-radio value="local">本地素材</el-radio>
            <el-radio value="DASHSCOPE">AI 生图（通义万相）</el-radio>
          </el-radio-group>
          <span v-if="form.image_source === 'DASHSCOPE'" class="form-hint" style="display:block;margin-top:4px">
            AI 根据文章内容生成配图，不会包含文字
          </span>
        </el-form-item>

        <el-form-item label="文章底部固定内容（可选）">
          <div style="width: 100%">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
              <input
                ref="qrFileInput"
                type="file"
                accept="image/*"
                style="display:none"
                @change="uploadFooterQr"
              />
              <el-button size="small" @click="(qrFileInput as any)?.click()" :loading="uploadingQr">
                📷 上传二维码
              </el-button>
              <span v-if="footerQrUrl" style="color: #67c23a; font-size: 13px;">✅ 已上传</span>
              <span class="form-hint">上传后自动添加到页脚</span>
            </div>
            <el-input
              v-model="form.footer_template"
              type="textarea"
              :rows="2"
              placeholder="其他固定内容（如联系方式），二维码会自动加在前面"
            />
            <div v-if="footerQrUrl" style="margin-top: 8px; display: flex; align-items: center; gap: 8px;">
              <img :src="footerQrUrl" style="width: 48px; height: 48px; border-radius: 4px; object-fit: cover;" />
              <span style="font-size: 12px; color: #909399;">二维码将显示在页脚</span>
            </div>
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ editing ? '更新' : '创建' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.scheduled-tasks-page { max-width: 1200px; }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 28px; }
.eyebrow { font-size: 11px; letter-spacing: 0.15em; color: #909399; margin-bottom: 6px; }
.page-heading h1 { font-size: 24px; font-weight: 700; color: #303133; margin-bottom: 6px; }
.lead { color: #909399; font-size: 14px; }
.loading-section { padding: 40px 0; }
.empty-state { padding: 60px 0; }
.task-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; }
.task-card { border: 1px solid #e4e7ed; border-radius: 8px; padding: 16px; background: #fff; transition: box-shadow 0.2s; }
.task-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.card-title { font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.card-body { margin-bottom: 12px; }
.info-row { display: flex; font-size: 13px; padding: 3px 0; color: #606266; }
.info-row .label { color: #909399; min-width: 80px; flex-shrink: 0; }
.topic-preview { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-actions { display: flex; gap: 8px; padding-top: 8px; border-top: 1px solid #f0f0f0; }
.slots-container, .time-container { width: 100%; }
.slot-row, .time-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.slot-index { min-width: 24px; font-size: 13px; color: #909399; }
</style>
