<script setup lang="ts">
import { onMounted, computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { Account, FeedSource } from '@/api/types'
import { listErpProductSources, type ErpProductSource } from '@/api/erpProducts'

interface ScheduledTask {
  id: number
  tenant_id: number
  name: string
  is_active: boolean
  writing_mode: string
  topic: string | null
  feed_source_ids: number[] | null
  feed_source_id: number | null
  feed_article_ids: number[] | null
  style: string | null
  knowledge_base_ids: number[] | null
  day_of_week: number
  publish_times: string[]
  article_slots: ArticleSlot[] | null
  articles_per_day: number
  public_count: number
  private_count: number
  account_ids: number[] | null
  publish_mode: string
  image_source: string
  enabled_image_methods: string[] | null
  erp_image_config?: {
    source_key: string
    commodity_category?: string | null
    repeat_after_days: number
    image_count: number
  } | null
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
const erpProductSources = ref<ErpProductSource[]>([])
const showForm = ref(false)
const editing = ref(false)
const currentId = ref<number | null>(null)

const form = reactive({
  name: '',
  writing_mode: 'free',
  topic: '',
  feed_source_ids: [] as number[],
  feed_source_id: null as number | null,
  feed_article_ids: [] as number[],
  style: '',
  knowledge_base_ids: [] as number[],
  day_of_week: -1,
  publish_times: ['08:00'] as string[],
  articles_per_day: 1,
  account_ids: [] as number[],
  publish_mode: 'draft',
  image_source: 'DASHSCOPE',
  erp_source_key: '',
  erp_commodity_category: '',
  erp_repeat_after_days: 3,
  erp_image_count: 8,
  footer_template: '',
  content_type: 'article',
  enabled_image_methods: ['DASHSCOPE'],
  enable_watermark: false,
})

const dayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const dayOptions = [
  { value: -1, label: '每天' },
  ...dayLabels.map((label, i) => ({ value: i, label })),
]

const imageMethodOptions = [
  { value: 'DASHSCOPE', label: 'AI 生图（通义万相）' },
  { value: 'LOCAL', label: '本地素材库' },
  { value: 'ERP', label: 'ERP 产品库' },
]

// Feed source article picker
const feedSourceArticles = ref<any[]>([])
const showFeedArticlePicker = ref(false)
const loadingFeedArticles = ref(false)

async function handleFeedSourceChange(preserveSelectedArticles = false) {
  if (!preserveSelectedArticles) form.feed_article_ids = []
  feedSourceArticles.value = []
  if (!form.feed_source_id) return
  loadingFeedArticles.value = true
  try {
    const res = await client.get(`/feed-sources/${form.feed_source_id}/articles`, {
      params: { page: 1, page_size: 50 },
    })
    feedSourceArticles.value = res.data.items || res.data || []
  } catch {
    ElMessage.warning('加载投喂源文章失败')
  } finally {
    loadingFeedArticles.value = false
  }
}

/** 只加载允许公开的 ERP 来源，前端永远不持有 ERP 凭证。 */
async function loadErpProductSources() {
  try {
    erpProductSources.value = await listErpProductSources()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || 'ERP 产品来源加载失败')
  }
}

function toggleFeedArticle(id: number) {
  const idx = form.feed_article_ids.indexOf(id)
  if (idx >= 0) {
    form.feed_article_ids.splice(idx, 1)
  } else {
    form.feed_article_ids.push(id)
  }
}

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

function getErpSourceName(sourceKey: string): string {
  return erpProductSources.value.find(source => source.key === sourceKey)?.name || sourceKey
}

async function load() {
  loading.value = true
  try {
    const [t, a, f, k, erpSources] = await Promise.all([
      client.get<{ total: number; items: ScheduledTask[] }>('/scheduled-tasks'),
      client.get<{ items: Account[] }>('/accounts'),
      client.get<{ total: number; items: FeedSource[] }>('/feed-sources').catch(() => ({ data: { items: [] } })),
      client.get<{ items: any[] }>('/knowledge-bases').catch(() => ({ data: { items: [] } })),
      listErpProductSources().catch(() => []),
    ])
    tasks.value = t.data.items || []
    accounts.value = a.data.items || []
    feedSources.value = f.data.items || []
    knowledgeBases.value = k.data.items || []
    erpProductSources.value = erpSources
  } catch { ElMessage.error('加载失败') }
  finally { loading.value = false }
}

function resetForm() {
  form.name = ''
  form.content_type = 'article'
  form.writing_mode = 'free'
  form.topic = ''
  form.feed_source_ids = []
  form.feed_source_id = null
  form.feed_article_ids = []
  form.style = ''
  form.knowledge_base_ids = []
  form.day_of_week = -1
  form.publish_times = ['08:00']
  form.articles_per_day = 1
  form.account_ids = []
  form.publish_mode = 'draft'
  form.image_source = 'DASHSCOPE'
  form.erp_source_key = ''
  form.erp_commodity_category = ''
  form.erp_repeat_after_days = 3
  form.erp_image_count = 8
  form.footer_template = ''
  form.enabled_image_methods = ['DASHSCOPE']
  form.enable_watermark = false
  footerQrUrl.value = ''
  editing.value = false
  currentId.value = null
}

function openCreate() { resetForm(); showForm.value = true }

async function openEdit(task: ScheduledTask) {
  editing.value = true
  currentId.value = task.id
  form.name = task.name
  form.content_type = (task as any).content_type || 'article'
  form.writing_mode = task.writing_mode
  form.topic = task.topic || ''
  form.feed_source_ids = task.feed_source_ids || []
  form.feed_source_id = (task as any).feed_source_id || null
  form.feed_article_ids = (task as any).feed_article_ids || []
  form.style = task.style || ''
  form.knowledge_base_ids = task.knowledge_base_ids || []
  form.day_of_week = task.day_of_week
  form.publish_times = task.publish_times?.length ? [...task.publish_times] : ['08:00']
  form.articles_per_day = task.articles_per_day
  form.account_ids = task.account_ids || []
  form.publish_mode = task.publish_mode || 'draft'
  form.image_source = task.image_source || 'DASHSCOPE'
  form.erp_source_key = task.erp_image_config?.source_key || ''
  form.erp_commodity_category = task.erp_image_config?.commodity_category || ''
  form.erp_repeat_after_days = task.erp_image_config?.repeat_after_days || 3
  form.erp_image_count = task.erp_image_config?.image_count || 8
  form.footer_template = task.footer_template || ''
  form.enabled_image_methods = (task as any).enabled_image_methods || ['DASHSCOPE']
  form.enable_watermark = (task as any).enable_watermark || false
  // 从 footer_template 中提取二维码 URL 用于预览
  const qrMatch = form.footer_template.match(/!\[二维码\]\(([^)]+)\)/)
  footerQrUrl.value = qrMatch ? qrMatch[1] : ''
  showForm.value = true
  // 编辑已有投喂任务时也必须加载文章列表，否则用户只能看到来源无法选择文章。
  if (form.feed_source_id) await handleFeedSourceChange(true)
  if (erpProductSources.value.length === 0) await loadErpProductSources()
}

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
      content_type: form.content_type,
      topic: form.topic || null,
      feed_source_ids: form.feed_source_ids.length > 0 ? form.feed_source_ids : null,
      feed_source_id: form.feed_source_id || null,
      feed_article_ids: form.feed_article_ids.length > 0 ? form.feed_article_ids : null,
      style: form.style || null,
      knowledge_base_ids: form.knowledge_base_ids.length > 0 ? form.knowledge_base_ids : null,
      day_of_week: form.day_of_week,
      publish_times: form.publish_times,
      articles_per_day: form.articles_per_day,
      account_ids: form.account_ids.length > 0 ? form.account_ids : null,
      publish_mode: form.publish_mode,
      image_source: form.image_source,
      footer_template: form.footer_template || null,
      enabled_image_methods: form.enabled_image_methods,
      enable_watermark: form.enable_watermark,
      erp_image_config: form.erp_source_key
        ? {
            source_key: form.erp_source_key,
            commodity_category: form.erp_commodity_category.trim() || undefined,
            repeat_after_days: form.erp_repeat_after_days,
            image_count: form.erp_image_count,
          }
        : null,
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
            <span>{{ task.articles_per_day }} 篇</span>
          </div>
          <div class="info-row">
            <span class="label">发布方式</span>
            <span>{{ task.publish_mode === 'direct' ? '直接发布' : '存草稿箱' }}</span>
          </div>
          <div class="info-row">
            <span class="label">封面来源</span>
            <span>{{ { DASHSCOPE: 'AI 生图', local: '本地素材库', erp: 'ERP 产品库' }[task.image_source] || task.image_source }}</span>
          </div>
          <div class="info-row">
            <span class="label">正文配图</span>
            <span>{{ (task.enabled_image_methods || []).map((method: string) => ({ DASHSCOPE: 'AI 生图', LOCAL: '本地素材库', ERP: 'ERP 产品库' }[method] || method)).join('、') || '未设置' }}</span>
          </div>
          <div v-if="task.erp_image_config" class="info-row">
            <span class="label">ERP 配图</span>
            <span>{{ getErpSourceName(task.erp_image_config.source_key) }}，{{ task.erp_image_config.commodity_category || '全部分类' }}，近{{ task.erp_image_config.repeat_after_days }}天不重复</span>
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

        <el-form-item label="文章主题" required>
          <el-input v-model="form.topic" type="textarea" :rows="3" placeholder="请输入文章主题，例如：人工智能如何改变教育行业" />
        </el-form-item>

        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item label="内容类型">
              <el-radio-group v-model="form.content_type">
                <el-radio value="article">图文</el-radio>
                <el-radio value="image">纯图片</el-radio>
                <el-radio value="video">视频</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="写作风格">
              <el-select v-model="form.style" clearable placeholder="选择风格（可选）" class="full-width">
                <el-option v-for="opt in styleOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="封面图片来源">
              <el-radio-group v-model="form.image_source">
                <el-radio value="DASHSCOPE">AI 生图</el-radio>
                <el-radio value="local">本地素材库</el-radio>
                <el-radio value="erp">ERP 产品库</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item v-if="form.content_type === 'article'" label="正文配图来源（可多选）">
          <el-checkbox-group v-model="form.enabled_image_methods">
            <el-checkbox v-for="opt in imageMethodOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </el-checkbox>
          </el-checkbox-group>
          <span class="form-hint">
            选择 ERP 产品库时：投喂源只决定文章结构和文案风格，不使用投喂源图片；ERP 产品图作为生成主体，所选知识库决定产品所在场景与背景。只有未选择 ERP 时，AI 生图才会参考投喂源图片风格。
          </span>
        </el-form-item>

        <el-form-item v-if="form.content_type === 'article' && (form.image_source === 'erp' || form.enabled_image_methods.includes('ERP'))" label="ERP 自动配图规则">
          <div class="erp-rule-grid">
            <el-select v-model="form.erp_source_key" placeholder="选择 ERP 产品来源" style="width:100%">
              <el-option v-for="source in erpProductSources" :key="source.key" :label="source.name" :value="source.key" />
            </el-select>
            <el-input v-model="form.erp_commodity_category" clearable placeholder="产品分类（留空表示全部分类）" />
            <el-input-number v-model="form.erp_repeat_after_days" :min="1" :max="30" controls-position="right" />
            <el-input-number v-model="form.erp_image_count" :min="1" :max="20" controls-position="right" />
          </div>
          <div class="erp-rule-labels"><span>ERP 来源</span><span>产品分类</span><span>防重天数</span><span>每篇上限</span></div>
          <span class="form-hint">留空分类会从当前 ERP 来源的全部产品中随机选择。每次只选一款产品作为整篇图片主体，知识库规则用于生成不同背景；图片使用历史按任务记录，近三天不会重复。</span>
        </el-form-item>

        <el-form-item label="知识库规则（可选）">
          <el-select v-model="form.knowledge_base_ids" multiple clearable collapse-tags collapse-tags-tooltip placeholder="选择知识库（可选）" style="width:100%">
            <el-option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id" :label="kb.name" />
          </el-select>
          <span class="form-hint">
            系统会按知识库章节拆分使用：文章形式、文案和固定联系方式传给文章 Agent；品牌调性、色彩、材质、场景和图片要求传给图片 Agent。ERP 产品任务需要选择知识库作为背景规则。
          </span>
        </el-form-item>

        <el-form-item label="仿写来源（可选，选择后 AI 会按该来源的风格仿写）">
          <div class="feed-source-wrapper">
            <el-select
              v-model="form.feed_source_id"
              clearable
              placeholder="选择投喂源进行仿写（可选）"
              style="width:100%"
              @change="handleFeedSourceChange"
            >
              <el-option v-for="src in feedSources" :key="src.id" :value="src.id" :label="src.name">
                <span>{{ src.name }}</span>
                <span v-if="src.style_profile" style="float:right;font-size:12px;color:#67c23a">✅ 已分析</span>
              </el-option>
            </el-select>
            <div v-if="form.feed_source_id && feedSourceArticles.length > 0" class="feed-articles-banner">
              <el-button size="small" type="primary" plain @click="showFeedArticlePicker = true">
                📄 选择参考文章
              </el-button>
              <span class="selected-count" v-if="form.feed_article_ids.length > 0">
                已选 {{ form.feed_article_ids.length }} 篇
              </span>
              <span v-else class="selected-count muted">仅使用风格分析</span>
            </div>
            <div v-else-if="loadingFeedArticles" class="feed-articles-banner">
              <el-skeleton :rows="1" animated />
            </div>
            <span class="form-hint">需要先在「投喂源」中添加来源并执行「分析」才能获取风格特征；选择具体文章可让 AI 直接仿写原文风格</span>
          </div>
        </el-form-item>

        <el-form-item label="文章底部固定内容（可选）">
          <div style="width:100%">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
              <input ref="qrFileInput" type="file" accept="image/*" style="display:none" @change="uploadFooterQr" />
              <el-button size="small" @click="(qrFileInput as any)?.click()" :loading="uploadingQr">📷 上传二维码</el-button>
              <span v-if="footerQrUrl" style="color:#67c23a;font-size:13px">✅ 已上传</span>
              <span class="form-hint" style="margin-left:8px">上传后自动添加到页脚</span>
            </div>
            <el-input v-model="form.footer_template" type="textarea" :rows="2" placeholder="其他固定内容（如联系方式），二维码会自动加在前面" />
            <div v-if="footerQrUrl" style="margin-top:8px;display:flex;align-items:center;gap:8px">
              <img :src="footerQrUrl" style="width:48px;height:48px;border-radius:4px;object-fit:cover" />
              <span style="font-size:12px;color:#909399">二维码将显示在页脚</span>
            </div>
          </div>
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="16">
            <el-form-item label="发布到公众号（可选，可多选）">
              <el-select v-model="form.account_ids" multiple collapse-tags collapse-tags-tooltip placeholder="选择要发布的公众号（不选则仅生成内容）" style="width:100%">
                <el-option v-for="acct in accounts" :key="acct.id" :value="acct.id" :label="acct.name" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="发布方式">
              <el-radio-group v-model="form.publish_mode" :disabled="form.account_ids.length === 0">
                <el-radio value="draft">存草稿箱</el-radio>
                <el-radio value="direct">直接发布</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>
        <span v-if="form.account_ids.length > 0" class="form-hint" style="display:block;margin-top:-12px;margin-bottom:18px">
          {{ form.publish_mode === 'direct' ? '⚠️ 直接发布将立即推送给订阅用户，请确认内容无误' : '保存到微信草稿箱，可手动检查后再发布' }}
        </span>

        <el-divider />
        <el-form-item label="水印">
          <div class="watermark-toggle-row">
            <el-switch v-model="form.enable_watermark" active-text="添加水印" inactive-text="无水印" />
            <span class="form-hint">在「水印设置」中配置样式</span>
          </div>
        </el-form-item>

        <el-divider />
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="适用日期">
              <el-select v-model="form.day_of_week" style="width:100%">
                <el-option v-for="d in dayOptions" :key="d.value" :value="d.value" :label="d.label" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
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
      </el-form>

      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ editing ? '更新' : '创建' }}</el-button>
      </template>
      <!-- Feed article picker dialog -->
      <el-dialog v-model="showFeedArticlePicker" title="选择参考文章" width="480px" append-to-body>
        <p style="color:#909399;font-size:13px;margin-bottom:12px">
          选择要仿写的参考文章（已选 {{ form.feed_article_ids.length }} 篇）。
          AI 将严格模仿选中文章的写作风格、语气和句式结构。
        </p>
        <div v-if="feedSourceArticles.length === 0" style="padding:24px;text-align:center;color:#909399">
          暂无文章，请先在投喂源中「抓取」文章
        </div>
        <div v-else class="feed-article-list">
          <div v-for="article in feedSourceArticles" :key="article.id"
               class="feed-article-item"
               :class="{ selected: form.feed_article_ids.includes(article.id) }"
               @click="toggleFeedArticle(article.id)">
            <el-checkbox :checked="form.feed_article_ids.includes(article.id)" @click.stop="toggleFeedArticle(article.id)" />
            <div class="article-info">
              <strong>{{ article.title || '无标题' }}</strong>
              <p>{{ (article.summary || article.body_markdown || '').slice(0, 120) }}</p>
            </div>
          </div>
        </div>
        <template #footer>
          <el-button @click="showFeedArticlePicker = false">确定</el-button>
        </template>
      </el-dialog>
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
.feed-source-wrapper { width: 100%; }
.erp-rule-grid { display: grid; grid-template-columns: 2fr 2fr 1fr 1fr; gap: 10px; width: 100%; }
.erp-rule-labels { display: grid; grid-template-columns: 2fr 2fr 1fr 1fr; gap: 10px; width: 100%; margin-top: 5px; color: #909399; font-size: 12px; }
.feed-articles-banner { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.selected-count { font-size: 13px; color: #409eff; font-weight: 500; }
.selected-count.muted { color: #909399; font-weight: 400; }
.feed-article-list { display: grid; gap: 8px; max-height: 440px; overflow-y: auto; }
.feed-article-item { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; border: 1px solid #e4e7ed; border-radius: 6px; cursor: pointer; transition: all 0.2s; }
.feed-article-item:hover { border-color: #409eff; background: #f5f9ff; }
.feed-article-item.selected { border-color: #409eff; background: #ecf5ff; }
.feed-article-item .article-info { flex: 1; min-width: 0; }
.feed-article-item .article-info strong { display: block; font-size: 14px; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; }
.feed-article-item .article-info p { font-size: 12px; color: #909399; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
