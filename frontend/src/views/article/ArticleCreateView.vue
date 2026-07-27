<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import {
  createArticle,
  confirmTitle,
  confirmOutline,
  aiModifyOutline,
  getArticle,
  getExecutionLogs,
  publishDraft,
} from '@/api/article'
import client from '@/api/client'
import type { TitleOption, Article, Account, KnowledgeBase, FeedSource } from '@/api/types'
import { SseConnection } from '@/utils/sse'
import { marked } from 'marked'
import { sanitizeHtml } from '@/utils/sanitizer'

const router = useRouter()

// ==================== State ====================
const topic = ref('')
const style = ref('')
const contentType = ref('article')
const imageSource = ref<'local' | 'pexels'>('pexels')
const enabledImageMethods = ref<string[]>(['PEXELS', 'DASHSCOPE'])
const userDescription = ref('')
const mode = ref<'manual' | 'auto'>('manual')
const articleCount = ref(1)
const loading = ref(false)
const currentTaskId = ref('')
const currentArticle = ref<Article | null>(null)
const agentLogs = ref<any[]>([])

// Phase tracking
type Phase =
  | 'INPUT'
  | 'TITLE_GENERATING'
  | 'TITLE_SELECTING'
  | 'OUTLINE_GENERATING'
  | 'OUTLINE_EDITING'
  | 'CONTENT_GENERATING'
  | 'COMPLETED'
  | 'FAILED'

const currentPhase = ref<Phase>('INPUT')

// Title options
const titleOptions = ref<TitleOption[]>([])
const selectedTitle = ref<TitleOption | null>(null)

// Outline
const outline = ref<any>(null)
const outlineRaw = ref('')
const outlineEditMode = ref(false)
const modifySuggestion = ref('')
const aiModifyingOutline = ref(false)

// Content streaming
const streamedContent = ref('')
const isStreaming = ref(false)

// 视频配置
const videoAspectRatio = ref('9:16')

// Image progress
const imageProgress = ref<{ total: number; completed: number; items: any[] }>({
  total: 0,
  completed: 0,
  items: [],
})

// WeChat drafts — 支持多选公众号
const accounts = ref<Account[]>([])
const selectedAccountIds = ref<number[]>([])
const savingDraft = ref(false)

// Knowledge base selector
const knowledgeBases = ref<KnowledgeBase[]>([])
const selectedKbIds = ref<number[]>([])

async function loadKnowledgeBases() {
  try {
    const res = await client.get<{ total: number; items: KnowledgeBase[] }>('/knowledge-bases')
    knowledgeBases.value = res.data.items || res.data || []
  } catch {
    // ignore - KB is optional
  }
}

// Footer template + QR code
const footerTemplate = ref('')
const footerQrUrl = ref('')
const uploadingQr = ref(false)
const qrFileInput = ref<HTMLInputElement | null>(null)

// Publish mode: "draft" = 存草稿箱, "direct" = 直接发布
const publishMode = ref('draft')

// Scheduling option
const enableSchedule = ref(false)
const enableWatermark = ref(true)
const watermarkConfigLoaded = ref(false)

async function loadWatermarkConfig() {
  try {
    const res = await client.get('/watermark-config')
    enableWatermark.value = res.data.enabled
    watermarkConfigLoaded.value = true
  } catch {
    watermarkConfigLoaded.value = true
  }
}
const scheduleForm = reactive({
  day_of_week: -1,
  publish_times: ['08:00'] as string[],
  articles_per_day: 1,
  publish_mode: 'draft',
})

const dayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const dayOptions = [
  { value: -1, label: '每天' },
  ...dayLabels.map((label, i) => ({ value: i, label })),
]
const newScheduleTime = ref('')

function addScheduleTime() {
  if (newScheduleTime.value && !scheduleForm.publish_times.includes(newScheduleTime.value)) {
    scheduleForm.publish_times.push(newScheduleTime.value)
    newScheduleTime.value = ''
  }
}

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
    // Auto-generate footer with QR code + existing text
    const qrMd = footerQrUrl.value ? `![二维码](${footerQrUrl.value})` : ''
    footerTemplate.value = qrMd ? `${qrMd}\n\n${footerTemplate.value.replace(/!\[二维码\]\(.*?\)\n\n/, '')}` : footerTemplate.value
    ElMessage.success('二维码已上传')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '二维码上传失败')
  } finally {
    uploadingQr.value = false
    input.value = '' as any
  }
}

// Feed source selector (imitation mode)
const feedSources = ref<FeedSource[]>([])
const selectedFeedSourceId = ref<number | null>(null)

// Feed source article selection for imitation
const feedSourceArticles = ref<any[]>([])
const selectedFeedArticleIds = ref<number[]>([])
const loadingFeedArticles = ref(false)
const showFeedArticlePicker = ref(false)

async function loadFeedSources() {
  try {
    const res = await client.get<{ total: number; items: FeedSource[] }>('/feed-sources')
    feedSources.value = res.data.items || res.data || []
  } catch {
    // ignore - feed sources are optional
  }
}

async function handleFeedSourceChange() {
  selectedFeedArticleIds.value = []
  feedSourceArticles.value = []
  if (!selectedFeedSourceId.value) return
  loadingFeedArticles.value = true
  try {
    const res = await client.get(`/feed-sources/${selectedFeedSourceId.value}/articles`, {
      params: { page: 1, page_size: 50 },
    })
    feedSourceArticles.value = res.data.items || res.data || []
  } catch {
    ElMessage.warning('加载投喂源文章失败')
  } finally {
    loadingFeedArticles.value = false
  }
}

function toggleFeedArticle(id: number) {
  const idx = selectedFeedArticleIds.value.indexOf(id)
  if (idx >= 0) {
    selectedFeedArticleIds.value.splice(idx, 1)
  } else {
    selectedFeedArticleIds.value.push(id)
  }
}

// Local image selector
const localAssets = ref<any[]>([])
const loadingAssets = ref(false)
const selectedImageUrls = ref<string[]>([])
const imageSelectionMode = ref<'auto' | 'manual'>('auto')
const showAssetPicker = ref(false)

async function loadLocalAssets() {
  loadingAssets.value = true
  try {
    const res = await client.get('/assets', {
      params: { page: 1, page_size: 100, type: 'image' },
    })
    localAssets.value = res.data.items || []
  } catch {
    ElMessage.warning('加载本地素材失败')
  } finally {
    loadingAssets.value = false
  }
}

function toggleAsset(url: string) {
  const idx = selectedImageUrls.value.indexOf(url)
  if (idx >= 0) {
    selectedImageUrls.value.splice(idx, 1)
  } else {
    selectedImageUrls.value.push(url)
  }
}

function handleImageSourceChange() {
  if (imageSource.value === 'local') {
    imageSelectionMode.value = 'auto'
    selectedImageUrls.value = []
    loadLocalAssets()
  } else if (imageSource.value === 'ai') {
    // AI 生图模式：只启用 DASHSCOPE
    enabledImageMethods.value = ['DASHSCOPE']
    imageSelectionMode.value = 'auto'
    selectedImageUrls.value = []
  } else {
    enabledImageMethods.value = ['PEXELS', 'DASHSCOPE']
    imageSelectionMode.value = 'auto'
    selectedImageUrls.value = []
  }
}

async function loadAccounts() {
  try {
    const res = await client.get<{ items: Account[] }>('/accounts')
    accounts.value = (res.data.items || []).filter(a => a.status === 'active')
    // 默认不勾选，让用户手动选择
  } catch {
    // ignore
  }
}

async function handlePublishDraft() {
  if (!currentTaskId.value || selectedAccountIds.value.length === 0) return
  savingDraft.value = true
  let successCount = 0
  const modeLabel = publishMode.value === 'direct' ? '直接发布' : '存草稿箱'
  for (const aid of selectedAccountIds.value) {
    try {
      await publishDraft(currentTaskId.value, aid, publishMode.value)
      successCount++
    } catch (err: any) {
      ElMessage.error(`${modeLabel}到公众号 #${aid} 失败: ${err?.response?.data?.detail || '未知错误'}`)
    }
  }
  if (successCount > 0) {
    ElMessage.success(`✅ 已${modeLabel}到 ${successCount} 个公众号`)
  }
  savingDraft.value = false
}

// SSE
let sseConnection: SseConnection | null = null
const sseConnected = ref(false)
const sseCompleted = ref(false)

// ==================== Computed ====================
const canCreate = computed(() => {
  // 有投喂源参考时，主题可以简短（标题由仿写生成）
  if (selectedFeedArticleIds.value.length > 0) return true
  return topic.value.trim().length >= 5
})

const styleOptions = [
  { value: '', label: '默认风格' },
  { value: 'tech', label: '科技风格' },
  { value: 'emotional', label: '情感风格' },
  { value: 'educational', label: '教育风格' },
  { value: 'humorous', label: '幽默风格' },
]

const imageMethodOptions = [
  { value: 'PEXELS', label: 'Pexels 图库' },
  { value: 'DASHSCOPE', label: 'AI 生图（通义万相）' },
  { value: 'LOCAL', label: '素材库' },
]

// ==================== Methods ====================
// 纯图片/视频任务轮询
const contentJobId = ref<number | null>(null)
const contentJobType = ref<string>('')
const contentAssets = ref<any[]>([])
const contentVersion = ref<any>(null)  // gallery HTML from ContentVersion
const pollingTimer = ref<number | null>(null)
const jobError = ref('')
const galleryIndex = ref(0)
const galleryThumbsRef = ref<HTMLElement | null>(null)
const galleryUrls = computed(() => {
  const html = contentVersion.value?.body_html || ''
  const urls: string[] = []
  const re = /src="([^"]+)"/g
  let m
  while ((m = re.exec(html)) !== null) {
    if (!urls.includes(m[1])) urls.push(m[1])
  }
  return urls
})

function downloadFile(url: string, filename: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.target = '_blank'
  a.click()
}

function pollContentAssets(jobId: number) {
  pollingTimer.value = window.setInterval(async () => {
    try {
      const [res, jobRes] = await Promise.all([
        client.get(`/content-assets?job_id=${jobId}`),
        client.get(`/content-jobs/${jobId}`),
      ])
      const items = res.data?.items || []
      const jobStatus = jobRes.data?.status

      // 任务失败
      if (jobStatus === 'failed') {
        jobError.value = jobRes.data?.error_message || '生成失败'
        loading.value = false
        contentAssets.value = items
        currentPhase.value = 'COMPLETED'
        if (pollingTimer.value) clearInterval(pollingTimer.value)
        pollingTimer.value = null
        ElMessage.error(jobError.value)
        return
      }

      // 任务完成（published）才显示结果
      if (jobStatus === 'published') {
        contentAssets.value = items
        try {
          const verRes = await client.get(`/content-jobs/${jobId}/versions`)
          const versions = verRes.data || []
          contentVersion.value = versions.find((v: any) => v.body_html) || versions[0] || null
        } catch { contentVersion.value = null }
        loading.value = false
        currentPhase.value = 'COMPLETED'
        if (pollingTimer.value) clearInterval(pollingTimer.value)
        pollingTimer.value = null
        ElMessage.success('内容生成完成')
        return
      }
    } catch {
      // ignore polling errors
    }
  }, 3000)
}

async function handleCreate() {
  if (!canCreate.value) return
  loading.value = true
  jobError.value = ''
  contentAssets.value = []
  contentJobId.value = null

  try {
    // 构建请求参数
    const payload: Record<string, any> = {
      topic: topic.value,
      content_type: contentType.value,
      style: style.value,
      account_ids: selectedAccountIds.value.length > 0 ? selectedAccountIds.value : undefined,
      publish_mode: publishMode.value,
      footer_template: footerTemplate.value || undefined,
      watermark_enabled: enableWatermark.value || undefined,
    }

    // 图文专属参数
    if (contentType.value === 'article') {
      payload.image_source = imageSource.value === 'ai' ? 'DASHSCOPE' : imageSource.value
      payload.enabled_image_methods = enabledImageMethods.value
      payload.mode = mode.value
      payload.article_count = articleCount.value
      payload.knowledge_base_ids = selectedKbIds.value.length > 0 ? selectedKbIds.value : undefined
      payload.source_feed_id = selectedFeedSourceId.value ?? undefined
      payload.feed_article_ids = selectedFeedArticleIds.value.length > 0 ? selectedFeedArticleIds.value : undefined
      payload.selected_image_urls = imageSelectionMode.value === 'manual' && selectedImageUrls.value.length > 0
        ? selectedImageUrls.value : undefined
    }

    // 纯图片 & 视频共享参数（投喂源参考）
    if (contentType.value === 'image' || contentType.value === 'video') {
      payload.source_feed_id = selectedFeedSourceId.value ?? undefined
      payload.feed_article_ids = selectedFeedArticleIds.value.length > 0 ? selectedFeedArticleIds.value : undefined
    }

    // 视频专属参数
    if (contentType.value === 'video') {
      payload.aspect_ratio = videoAspectRatio.value
      payload.knowledge_base_ids = selectedKbIds.value.length > 0 ? selectedKbIds.value : undefined
      payload.source_feed_id = selectedFeedSourceId.value ?? undefined
      payload.feed_article_ids = selectedFeedArticleIds.value.length > 0 ? selectedFeedArticleIds.value : undefined
    }

    const resp = await client.post('/articles/create', payload)

    const data = resp.data

    // 纯图片/视频：检查是否已完成（同步处理）或需要轮询
    if (data.type === 'content_job') {
      contentJobId.value = data.job_id
      contentJobType.value = data.content_type

      if (data.status === 'published' && data.result?.image_urls?.length) {
        const urls: string[] = data.result.image_urls
        contentAssets.value = urls.map((url, i) => ({
          id: i, asset_type: 'final_image', file_url: url,
        }))
        contentVersion.value = { body_html: urls.map(u => `<img src="${u}" />`).join('\n') }
        currentPhase.value = 'COMPLETED'
        loading.value = false
        ElMessage.success('内容生成完成')
      } else {
        currentPhase.value = 'CONTENT_GENERATING'
        ElMessage.info('内容正在生成，请稍候...')
        pollContentAssets(data.job_id)
      }
      return
    }

    // 图文：原有逻辑
    const article = data
    currentTaskId.value = article.task_id
    currentArticle.value = article
    currentArticle.value = article
    console.log('[DEBUG] createArticle response:', JSON.stringify(article).slice(0, 500))

    // If scheduling is enabled, create a scheduled task
    if (enableSchedule.value) {
      try {
        const schedulePayload: Record<string, any> = {
          name: `定时: ${topic.value.slice(0, 40)}`,
          writing_mode: selectedFeedSourceId.value ? 'feed' : 'free',
          topic: topic.value,
          style: style.value || null,
          feed_source_ids: selectedFeedSourceId.value ? [selectedFeedSourceId.value] : null,
          knowledge_base_ids: selectedKbIds.value.length > 0 ? selectedKbIds.value : null,
          day_of_week: scheduleForm.day_of_week,
          publish_times: scheduleForm.publish_times,
          articles_per_day: scheduleForm.articles_per_day,
          account_ids: selectedAccountIds.value.length > 0 ? selectedAccountIds.value : null,
          publish_mode: scheduleForm.publish_mode,
          footer_template: footerTemplate.value || null,
        }
        await client.post('/scheduled-tasks', schedulePayload)
        ElMessage.success('定时任务已创建！')
      } catch (scheduleErr: any) {
        ElMessage.warning('文章创建成功，但定时任务创建失败')
      }
    }

    if (mode.value === 'auto') {
      // 全自动模式：后端已同步完成所有流程（含自动保存草稿）
      currentPhase.value = 'COMPLETED'
      sseCompleted.value = true
      ElMessage.success('文章生成完成！')
      loadArticle()
    } else {
      currentPhase.value = 'TITLE_GENERATING'
      ElMessage.success('文章创建成功，正在生成标题方案...')
      // Connect SSE
      connectSSE(article.task_id)
    }
  } catch (err: any) {
    console.error('[DEBUG] handleCreate error:', err)
    console.error('[DEBUG] response:', err?.response?.data)
    console.error('[DEBUG] status:', err?.response?.status)
    const errMsg = err?.response?.data?.message || err?.response?.data?.detail || err?.message || '创建失败'
    ElMessage.error(errMsg)
    currentPhase.value = 'FAILED'
  } finally {
    loading.value = false
  }
}

function connectSSE(taskId: string) {
  const baseUrl = window.location.origin
  const sseUrl = `${baseUrl}/api/v1/articles/${taskId}/progress`

  sseConnection = new SseConnection(sseUrl, {
    onMessage: handleSSEMessage,
    onStatusChange: (connected) => {
      sseConnected.value = connected
    },
    onError: () => {
      if (!sseCompleted.value) {
        ElMessage.warning('SSE 连接断开，尝试重连...')
      }
    },
  })
  sseConnection.connect()
}

function reconnectSSE() {
  if (currentTaskId.value) {
    sseConnection?.disconnect()
    connectSSE(currentTaskId.value)
  }
}

function handleSSEMessage(event: string, data: string) {
  console.log('[SSE] event:', event, 'data.length:', data?.length || 0, 'data:', data?.slice(0, 100))
  switch (event) {
    case 'AGENT1_COMPLETE':
      try {
        const status = JSON.parse(data)
        if (status.status === 'pending' || status.phase === 'pending') {
          ElMessage.info('文章已加入生成队列，正在等待 AI 处理...')
        }
      } catch {
        // ignore
      }
      break

    case 'TITLES_GENERATED':
      try {
        const parsed = JSON.parse(data)
        titleOptions.value = parsed.title_options || []
        currentPhase.value = 'TITLE_SELECTING'
        ElMessage.success('标题方案生成完成，请选择一个')
      } catch {
        ElMessage.error('标题方案解析失败')
      }
      break

    case 'AGENT2_STREAMING':
      // Stream outline tokens
      outlineRaw.value += data
      break

    case 'OUTLINE_GENERATED':
      // Try event data first (SSE reconnect path), fall back to outlineRaw (streaming path)
      try {
        const parsed = JSON.parse(data)
        outline.value = parsed.sections ? parsed : JSON.parse(outlineRaw.value)
        currentPhase.value = 'OUTLINE_EDITING'
        ElMessage.success('大纲生成完成，您可以编辑或直接确认')
      } catch {
        try {
          outline.value = JSON.parse(outlineRaw.value)
          currentPhase.value = 'OUTLINE_EDITING'
          ElMessage.success('大纲生成完成，您可以编辑或直接确认')
        } catch {
          // Try to extract JSON from the raw text
          const raw = outlineRaw.value
          const jsonMatch = raw.match(/\{[\s\S]*\}/)
          if (jsonMatch) {
            try {
              outline.value = JSON.parse(jsonMatch[0])
              currentPhase.value = 'OUTLINE_EDITING'
              ElMessage.success('大纲生成完成')
            } catch {
              ElMessage.error('大纲解析失败，请重试')
            }
          }
        }
      }
      break

    case 'AGENT3_STREAMING':
      isStreaming.value = true
      // Try JSON (reconnect path), fall back to raw text (streaming path)
      try {
        const parsed = JSON.parse(data)
        if (parsed.content) {
          streamedContent.value = parsed.content
        } else {
          streamedContent.value += data
        }
      } catch {
        streamedContent.value += data
      }
      break

    case 'AGENT3_COMPLETE':
      isStreaming.value = false
      break

    case 'AGENT4_COMPLETE':
      ElMessage.info('配图需求分析完成')
      break

    case 'IMAGE_COMPLETE':
      try {
        const imgData = JSON.parse(data)
        imageProgress.value.completed++
        imageProgress.value.items.push(imgData)
      } catch {
        // ignore
      }
      break

    case 'AGENT5_COMPLETE':
      ElMessage.success('配图生成完成')
      break

    case 'MERGE_COMPLETE':
      ElMessage.success('图文合成完成')
      break

    case 'ALL_COMPLETE':
      sseCompleted.value = true
      currentPhase.value = 'COMPLETED'
      ElMessage.success('文章生成完成！')
      loadArticle()
      break

    case 'ERROR':
      sseCompleted.value = true
      ElMessage.error(data || '生成过程出错')
      currentPhase.value = 'FAILED'
      break
  }
}

async function handleSelectTitle(option: TitleOption) {
  selectedTitle.value = option
  loading.value = true

  try {
    await confirmTitle(currentTaskId.value, {
      main_title: option.main_title,
      sub_title: option.sub_title,
      user_description: userDescription.value,
    })
    currentPhase.value = 'OUTLINE_GENERATING'
    outlineRaw.value = ''
    ElMessage.info('正在生成大纲...')
    // Reconnect SSE to pick up the generated outline
    reconnectSSE()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.message || '确认标题失败')
  } finally {
    loading.value = false
  }
}

async function handleConfirmOutline() {
  if (!outline.value) return
  loading.value = true

  try {
    await confirmOutline(currentTaskId.value, {
      outline: outline.value,
      watermark_enabled: enableWatermark.value,
    })
    currentPhase.value = 'CONTENT_GENERATING'
    streamedContent.value = ''
    imageProgress.value = { total: 0, completed: 0, items: [] }
    ElMessage.info('正在生成正文...')
    // Reconnect SSE to pick up the generated content
    reconnectSSE()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.message || '确认大纲失败')
  } finally {
    loading.value = false
  }
}

async function handleAiModifyOutline() {
  if (!modifySuggestion.value.trim() || !outline.value || !selectedTitle.value) return
  aiModifyingOutline.value = true

  try {
    const result = await aiModifyOutline(currentTaskId.value, {
      main_title: selectedTitle.value.main_title,
      sub_title: selectedTitle.value.sub_title,
      current_outline: outline.value,
      modify_suggestion: modifySuggestion.value,
    })
    outline.value = result
    modifySuggestion.value = ''
    ElMessage.success('大纲已更新')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.message || 'AI 修改大纲失败')
  } finally {
    aiModifyingOutline.value = false
  }
}

async function loadArticle() {
  if (!currentTaskId.value) return
  try {
    currentArticle.value = await getArticle(currentTaskId.value)
    const logs = await getExecutionLogs(currentTaskId.value)
    agentLogs.value = Array.isArray(logs) ? logs : []
  } catch {
    // ignore
  }
}

function handleReset() {
  sseConnection?.disconnect()
  sseCompleted.value = false
  currentPhase.value = 'INPUT'
  topic.value = ''
  style.value = ''
  selectedTitle.value = null
  titleOptions.value = []
  outline.value = null
  outlineRaw.value = ''
  streamedContent.value = ''
  imageProgress.value = { total: 0, completed: 0, items: [] }
  currentTaskId.value = ''
  currentArticle.value = null
  agentLogs.value = []
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  const html = marked.parse(text, { async: false }) as string
  return sanitizeHtml(html)
}

onMounted(() => {
  loadAccounts()
  loadKnowledgeBases()
  loadFeedSources()
  loadWatermarkConfig()
})

onUnmounted(() => {
  sseConnection?.disconnect()
})
</script>

<template>
  <div class="article-create">
    <!-- ======== Phase: INPUT ======== -->
    <div v-if="currentPhase === 'INPUT'" class="phase-input">
      <el-card class="input-card">
        <template #header>
          <span class="card-title">AI 文章创作</span>
        </template>

        <el-form label-position="top">
          <el-form-item label="文章主题" required>
            <el-input
              v-model="topic"
              type="textarea"
              :rows="3"
              placeholder="请输入文章主题，例如：人工智能如何改变教育行业"
            />
          </el-form-item>

          <el-row :gutter="20">
            <el-col :span="6">
              <el-form-item label="内容类型">
                <el-radio-group v-model="contentType">
                  <el-radio value="article">图文</el-radio>
                  <el-radio value="image">纯图片</el-radio>
                  <el-radio value="video">视频</el-radio>
                </el-radio-group>
              </el-form-item>
            </el-col>
            <el-col :span="6">
              <el-form-item label="写作风格">
                <el-select v-model="style" placeholder="选择风格" clearable class="full-width">
                  <el-option
                    v-for="opt in styleOptions"
                    :key="opt.value"
                    :label="opt.label"
                    :value="opt.value"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col v-if="contentType === 'article'" :span="6">
              <el-form-item label="生成模式">
                <el-radio-group v-model="mode">
                  <el-radio value="manual">手动配合</el-radio>
                  <el-radio value="auto">全自动</el-radio>
                </el-radio-group>
                <span class="form-hint">{{ mode === 'auto' ? '自动选标题→生成大纲→写正文' : 'AI生成→您选择→AI确认→AI写正文' }}</span>
              </el-form-item>
            </el-col>
            <el-col v-if="contentType === 'article' || contentType === 'image'" :span="6">
              <el-form-item label="封面图片来源">
                <el-radio-group v-model="imageSource" @change="handleImageSourceChange">
                  <el-radio value="pexels">Pexels 图库</el-radio>
                  <el-radio value="local">素材库</el-radio>
                  <el-radio value="ai">AI 生图</el-radio>
                </el-radio-group>
              </el-form-item>
              <!-- Local asset selection mode -->
              <div v-if="imageSource === 'local'" class="image-source-options">
                <el-radio-group v-model="imageSelectionMode" size="small">
                  <el-radio-button value="auto">AI 自动选择</el-radio-button>
                  <el-radio-button value="manual">手动选择</el-radio-button>
                </el-radio-group>
                <div v-if="imageSelectionMode === 'manual'" class="manual-image-selector">
                  <el-button size="small" @click="showAssetPicker = true" :type="selectedImageUrls.length > 0 ? 'success' : 'default'">
                    {{ selectedImageUrls.length > 0 ? `已选 ${selectedImageUrls.length} 张` : '选择图片' }}
                  </el-button>
                  <div v-if="selectedImageUrls.length > 0" class="selected-previews">
                    <div v-for="(url, idx) in selectedImageUrls.slice(0, 5)" :key="idx" class="mini-preview">
                      <el-image :src="url" fit="cover" style="width: 48px; height: 48px; border-radius: 4px;" />
                    </div>
                    <span v-if="selectedImageUrls.length > 5" class="more-badge">+{{ selectedImageUrls.length - 5 }}</span>
                  </div>
                </div>
              </div>
            </el-col>
          </el-row>

          <!-- 正文配图方式 -->
          <el-form-item v-if="contentType === 'article' || contentType === 'image'" label="正文配图方式（可多选）">
            <el-checkbox-group v-model="enabledImageMethods">
              <el-checkbox v-for="opt in imageMethodOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </el-checkbox>
            </el-checkbox-group>
            <span class="form-hint">正文中插入的图片来源。封面已单独设置，不受此项影响</span>
          </el-form-item>

          <!-- Knowledge Base Selector -->
          <el-form-item label="知识库参考（可选，勾选后 AI 会自动检索相关内容）">
            <el-select
              v-model="selectedKbIds"
              multiple
              clearable
              collapse-tags
              collapse-tags-tooltip
              placeholder="选择知识库（可选）"
              style="width: 100%"
            >
              <el-option
                v-for="kb in knowledgeBases"
                :key="kb.id"
                :value="kb.id"
                :label="kb.name"
              />
            </el-select>
          </el-form-item>

          <!-- Feed Source Selector (Imitation Mode) -->
          <el-form-item label="仿写来源（可选，选择后 AI 会按该来源的风格仿写）">
            <div class="feed-source-wrapper">
              <el-select
                v-model="selectedFeedSourceId"
                clearable
                placeholder="选择投喂源进行仿写（可选）"
                style="width: 100%"
                @change="handleFeedSourceChange"
              >
                <el-option
                  v-for="src in feedSources"
                  :key="src.id"
                  :value="src.id"
                  :label="src.name"
                >
                  <span>{{ src.name }}</span>
                  <span v-if="src.style_profile" style="float: right; font-size: 12px; color: #67c23a;">
                    ✅ 已分析
                  </span>
                </el-option>
              </el-select>
              <!-- Article selection trigger -->
              <div v-if="selectedFeedSourceId && feedSourceArticles.length > 0" class="feed-articles-banner">
                <el-button size="small" type="primary" plain @click="showFeedArticlePicker = true">
                  📄 选择参考文章
                </el-button>
                <span class="selected-count" v-if="selectedFeedArticleIds.length > 0">
                  已选 {{ selectedFeedArticleIds.length }} 篇
                </span>
                <span v-else class="selected-count muted">仅使用风格分析</span>
              </div>
              <div v-else-if="loadingFeedArticles" class="feed-articles-banner">
                <el-skeleton :rows="1" animated />
              </div>
              <span class="form-hint">需要先在「投喂源」中添加来源并执行「分析」才能获取风格特征；选择具体文章可让 AI 直接仿写原文风格</span>
            </div>
          </el-form-item>

          <!-- ===================== 视频专属配置 ===================== -->
          <template v-if="contentType === 'video'">
            <el-divider />
            <p class="section-subtitle">视频设置</p>
            <el-row :gutter="16">
              <el-col :span="8">
                <el-form-item label="画面比例">
                  <el-radio-group v-model="videoAspectRatio">
                    <el-radio value="9:16">9:16 竖屏</el-radio>
                    <el-radio value="16:9">16:9 横屏</el-radio>
                  </el-radio-group>
                </el-form-item>
              </el-col>
            </el-row>
            <el-divider />
          </template>

          <!-- Footer Template + QR Code -->
          <el-form-item label="文章底部固定内容（可选）">
            <div class="footer-tpl-wrapper">
              <div class="footer-qr-row">
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
                <span v-if="footerQrUrl" class="qr-success">✅ 已上传</span>
                <span class="form-hint" style="margin-left:8px">上传后自动添加到页脚</span>
              </div>
              <el-input
                v-model="footerTemplate"
                type="textarea"
                :rows="2"
                placeholder="其他固定内容（如联系方式），二维码会自动加在前面"
              />
              <div v-if="footerQrUrl" class="footer-preview">
                <img :src="footerQrUrl" class="footer-qr-preview" />
                <span class="footer-preview-text">二维码将显示在页脚</span>
              </div>
            </div>
          </el-form-item>

          <!-- Target Accounts (多选) + 发布模式 -->
          <el-row :gutter="16">
            <el-col :span="16">
              <el-form-item label="发布到公众号（可选，可多选）">
                <el-select
                  v-model="selectedAccountIds"
                  multiple
                  collapse-tags
                  collapse-tags-tooltip
                  placeholder="选择要发布的公众号（不选则仅生成内容）"
                  style="width: 100%"
                >
                  <el-option
                    v-for="acct in accounts"
                    :key="acct.id"
                    :value="acct.id"
                    :label="acct.name"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="发布方式">
                <el-radio-group v-model="publishMode" :disabled="selectedAccountIds.length === 0">
                  <el-radio value="draft">存草稿箱</el-radio>
                  <el-radio value="direct">直接发布</el-radio>
                </el-radio-group>
              </el-form-item>
            </el-col>
          </el-row>
          <span v-if="selectedAccountIds.length > 0" class="form-hint" style="display:block;margin-top:-12px;margin-bottom:18px">
            {{ publishMode === 'direct' ? '⚠️ 直接发布将立即推送给订阅用户，请确认内容无误' : '保存到微信草稿箱，可手动检查后再发布' }}
          </span>

          <!-- Watermark Toggle -->
          <el-divider />
          <el-form-item label="水印">
            <div class="watermark-toggle-row">
              <el-switch v-model="enableWatermark" active-text="添加水印" inactive-text="无水印" />
              <span v-if="watermarkConfigLoaded" class="form-hint">
                在「水印设置」中配置样式
              </span>
            </div>
          </el-form-item>

          <!-- Scheduling Section -->
          <el-divider />
          <el-form-item>
            <el-checkbox v-model="enableSchedule" label="同时创建定时任务" border />
            <span class="form-hint" style="margin-left: 8px;">开启后，除了生成文章，还会按设定的时间周期自动生成</span>
          </el-form-item>

          <template v-if="enableSchedule">
            <el-row :gutter="16">
              <el-col :span="8">
                <el-form-item label="执行频率">
                  <el-select v-model="scheduleForm.day_of_week" style="width:100%">
                    <el-option v-for="d in dayOptions" :key="d.value" :value="d.value" :label="d.label" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="每次篇数">
                  <el-input-number v-model="scheduleForm.articles_per_day" :min="1" :max="10" style="width:100%" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-row :gutter="16">
              <el-col :span="8">
                <el-form-item label="发布方式">
                  <el-radio-group v-model="scheduleForm.publish_mode">
                    <el-radio value="draft">存草稿箱</el-radio>
                    <el-radio value="direct">直接发布</el-radio>
                  </el-radio-group>
                </el-form-item>
              </el-col>
            </el-row>

            <el-form-item label="发布时间">
              <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                <el-tag
                  v-for="(t, i) in scheduleForm.publish_times"
                  :key="i"
                  closable
                  @close="scheduleForm.publish_times.splice(i, 1)"
                  style="margin-right: 4px;"
                >
                  {{ t }}
                </el-tag>
                <el-time-picker
                  v-model="newScheduleTime"
                  format="HH:mm"
                  value-format="HH:mm"
                  style="width: 120px"
                  placeholder="添加时间"
                />
                <el-button size="small" @click="addScheduleTime" :disabled="!newScheduleTime">添加</el-button>
              </div>
            </el-form-item>
          </template>

          <el-form-item>
            <el-button
              type="primary"
              size="large"
              :loading="loading"
              :disabled="!canCreate"
              @click="handleCreate"
            >
              开始创作
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
    </div>

    <!-- ======== Phase: AUTO_GENERATING ======== -->
    <div v-if="currentPhase === 'AUTO_GENERATING'" class="phase-generating">
      <el-card>
        <template #header>
          <span class="card-title">全自动生成中...</span>
        </template>
        <div class="generating-placeholder">
          <el-steps :active="1" align-center>
            <el-step title="生成标题" description="AI 正在构思标题方案" />
            <el-step title="生成大纲" description="正在规划文章结构" />
            <el-step title="撰写正文" description="正在生成文章内容" />
            <el-step title="完成" description="即将完成" />
          </el-steps>
          <el-progress :percentage="60" :stroke-width="6" indeterminate style="margin-top: 24px" />
        </div>
      </el-card>
    </div>

    <!-- ======== Phase: TITLE_GENERATING ======== -->
    <div v-if="currentPhase === 'TITLE_GENERATING'" class="phase-generating">
      <el-card>
        <template #header>
          <span class="card-title">AI 正在生成标题方案...</span>
        </template>
        <div class="generating-placeholder">
          <el-progress :percentage="50" :stroke-width="6" indeterminate />
          <p class="hint-text">正在分析主题，生成多个标题方案供您选择</p>
        </div>
      </el-card>
    </div>

    <!-- ======== Phase: TITLE_SELECTING ======== -->
    <div v-if="currentPhase === 'TITLE_SELECTING'" class="phase-select-title">
      <el-card>
        <template #header>
          <span class="card-title">请选择一个标题方案</span>
        </template>

        <div class="title-options">
          <div
            v-for="(opt, index) in titleOptions"
            :key="index"
            class="title-option-card"
            :class="{ selected: selectedTitle === opt }"
            @click="handleSelectTitle(opt)"
          >
            <div class="option-index">方案 {{ index + 1 }}</div>
            <div class="option-main-title">{{ opt.main_title }}</div>
            <div class="option-sub-title">{{ opt.sub_title }}</div>
          </div>
        </div>

        <el-form-item label="补充说明（可选）" class="mt-4">
          <el-input
            v-model="userDescription"
            type="textarea"
            :rows="2"
            placeholder="对文章内容的额外要求或补充说明..."
            maxlength="500"
          />
        </el-form-item>
      </el-card>
    </div>

    <!-- ======== Phase: OUTLINE_GENERATING ======== -->
    <div v-if="currentPhase === 'OUTLINE_GENERATING'" class="phase-generating">
      <el-card>
        <template #header>
          <span class="card-title">AI 正在生成大纲...</span>
        </template>
        <div class="generating-placeholder">
          <el-progress :percentage="50" :stroke-width="6" indeterminate />
          <pre class="streaming-text">{{ outlineRaw }}</pre>
        </div>
      </el-card>
    </div>

    <!-- ======== Phase: OUTLINE_EDITING ======== -->
    <div v-if="currentPhase === 'OUTLINE_EDITING'" class="phase-edit-outline">
      <el-card>
        <template #header>
          <span class="card-title">编辑大纲</span>
        </template>

        <div v-if="outline" class="outline-display">
          <div
            v-for="section in outline.sections"
            :key="section.section"
            class="outline-section"
          >
            <div class="section-header">
              <span class="section-number">第{{ section.section }}部分</span>
              <span class="section-title">{{ section.title }}</span>
            </div>
            <ul class="section-points">
              <li v-for="(point, i) in section.points" :key="i">{{ point }}</li>
            </ul>
          </div>
        </div>

        <!-- AI Modify -->
        <el-divider />
        <el-form-item label="AI 修改大纲">
          <el-input
            v-model="modifySuggestion"
            type="textarea"
            :rows="2"
            placeholder="输入修改建议，如：增加一个关于实际案例的章节..."
          />
        </el-form-item>
        <el-button
          :loading="aiModifyingOutline"
          :disabled="!modifySuggestion.trim()"
          @click="handleAiModifyOutline"
        >
          AI 修改
        </el-button>

        <el-divider />
        <el-button type="primary" size="large" :loading="loading" @click="handleConfirmOutline">
          确认大纲并生成正文
        </el-button>
      </el-card>
    </div>

    <!-- ======== Phase: CONTENT_GENERATING ======== -->
    <div v-if="currentPhase === 'CONTENT_GENERATING'" class="phase-generating">
      <!-- 图片/视频：轮询等待 -->
      <el-card v-if="contentJobId">
        <template #header>
          <span class="card-title">{{ contentJobType === 'image' ? 'AI 正在生成图片...' : 'AI 正在生成视频...' }}</span>
        </template>
        <div style="padding:40px;text-align:center;">
          <el-progress :percentage="50" :stroke-width="6" indeterminate />
          <p style="margin-top:16px;color:#909399;" v-if="!jobError">
            {{ contentJobType === 'image' ? '正在生成素材并保存...' : '正在生成视频...' }}
          </p>
          <p v-if="jobError" style="color:#f56c6c;">{{ jobError }}</p>
        </div>
      </el-card>

      <!-- 图文：流式生成 -->
      <el-card v-if="!contentJobId" class="content-stream-card">
        <template #header>
          <span class="card-title">AI 正在生成正文...</span>
        </template>
        <div class="content-stream">
          <div v-if="streamedContent" class="rendered-content" v-html="renderMarkdown(streamedContent)" />
          <el-progress v-if="isStreaming" :percentage="50" :stroke-width="4" indeterminate />
        </div>
      </el-card>

      <!-- Image Progress -->
      <el-card v-if="imageProgress.completed > 0" class="image-progress-card">
        <template #header>
          <span>配图进度 ({{ imageProgress.completed }})</span>
        </template>
        <div class="image-grid">
          <div v-for="img in imageProgress.items" :key="img.position" class="image-item">
            <el-image :src="img.url" fit="cover" />
            <span class="image-label">{{ img.section_title || img.type }}</span>
          </div>
        </div>
      </el-card>
    </div>

    <!-- ======== Phase: COMPLETED ======== -->
    <div v-if="currentPhase === 'COMPLETED'" class="phase-completed">
      <!-- 纯图片结果 -->
      <template v-if="contentJobType === 'image'">
        <el-alert title="图片生成完成！" type="success" show-icon :closable="false" />
        <el-card class="image-preview-card" v-if="galleryUrls.length">
          <template #header><span>生成结果（画廊模式）</span></template>
          <div class="gallery-widget">
            <div class="gallery-main">
              <img :src="galleryUrls[galleryIndex] || ''" referrerpolicy="no-referrer" />
            </div>
            <div class="gallery-thumbs" ref="galleryThumbsRef">
              <div
                v-for="(url, idx) in galleryUrls"
                :key="idx"
                class="thumb-item"
                :class="{ active: idx === galleryIndex }"
                @click="galleryIndex = idx"
              >
                <img :src="url" referrerpolicy="no-referrer" />
              </div>
            </div>
          </div>
        </el-card>
        <el-card class="image-preview-card" v-else-if="contentAssets.length > 0">
          <template #header><span>生成结果</span></template>
          <div class="poster-gallery" style="display:flex;flex-wrap:wrap;gap:16px;">
            <div v-for="(asset, idx) in contentAssets.filter(a => a.asset_type === 'final_image')" :key="asset.id" style="flex:0 0 auto;">
              <el-image :src="asset.file_url" fit="contain" style="max-width:380px;max-height:500px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.1);display:block;" :preview-src-list="contentAssets.filter(a => a.asset_type === 'final_image').map(a => a.file_url)" :initial-index="idx" preview-teleported />
              <div style="margin-top:8px;text-align:center;">
                <el-button size="small" @click="downloadFile(asset.file_url, `image_${idx+1}.png`)">下载</el-button>
                <span style="font-size:12px;color:#999;margin-left:8px;">图片 {{ idx + 1 }}</span>
              </div>
            </div>
          </div>
        </el-card>
      </template>

      <!-- 视频结果 -->
      <template v-else-if="contentJobType === 'video'">
        <el-alert title="视频生成完成！" type="success" show-icon :closable="false" />
        <el-card class="video-preview-card" v-if="contentAssets.length > 0">
          <template #header><span>生成结果</span></template>
          <div v-for="asset in contentAssets.filter(a => a.asset_type === 'video')" :key="asset.id">
            <video :src="asset.file_url" controls style="max-width:100%;max-height:500px;border-radius:8px;">您的浏览器不支持视频播放</video>
            <div style="margin-top:12px;text-align:center;">
              <el-button size="small" @click="downloadFile(asset.file_url, 'video.mp4')">下载视频</el-button>
            </div>
          </div>
        </el-card>
      </template>

      <!-- 图文结果 -->
      <el-alert v-if="contentJobType !== 'image' && contentJobType !== 'video'" title="文章生成完成！" type="success" show-icon :closable="false" />

      <el-card class="article-preview" v-if="currentArticle || (streamedContent && !contentJobId)">
        <template #header>
          <span class="card-title">{{ currentArticle?.main_title || '文章完成' }}</span>
          <span class="card-subtitle">{{ currentArticle?.sub_title }}</span>
        </template>

        <div v-if="currentArticle?.cover_image" class="article-cover">
          <el-image :src="currentArticle.cover_image" fit="cover" />
        </div>

        <div
          class="article-content"
          v-html="renderMarkdown(currentArticle?.full_content || streamedContent)"
        />
      </el-card>

      <!-- Execution Logs -->
      <el-card v-if="agentLogs.length > 0" class="logs-card">
        <template #header>
          <span>执行日志</span>
        </template>
        <el-table :data="agentLogs" size="small">
          <el-table-column prop="agent_name" label="智能体" width="180" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'SUCCESS' ? 'success' : row.status === 'FAILED' ? 'danger' : 'warning'">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="duration_ms" label="耗时(ms)" width="100" />
          <el-table-column prop="error_message" label="错误信息" />
        </el-table>
      </el-card>

      <!-- Publish to WeChat (支持多选) -->
      <el-card v-if="accounts.length > 0" class="publish-card">
        <template #header>
          <span>发布到微信公众号</span>
        </template>
        <div class="publish-row">
          <el-select
            v-model="selectedAccountIds"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择要发布到的公众号（可多选）"
            style="width: 320px"
          >
            <el-option
              v-for="acct in accounts"
              :key="acct.id"
              :value="acct.id"
              :label="acct.name"
            />
          </el-select>
          <el-radio-group v-model="publishMode" size="small" style="margin-right: 12px">
            <el-radio value="draft">存草稿箱</el-radio>
            <el-radio value="direct">直接发布</el-radio>
          </el-radio-group>
          <el-button
            type="success"
            :loading="savingDraft"
            :disabled="selectedAccountIds.length === 0"
            @click="handlePublishDraft"
          >
            {{ savingDraft ? '处理中...' : publishMode === 'direct' ? '立即发布' : '保存到草稿箱' }}
          </el-button>
        </div>
        <span v-if="selectedAccountIds.length > 1" class="form-hint" style="display:block;margin-top:8px">
          将依次{{ publishMode === 'direct' ? '发布' : '保存' }}到每个选中公众号
        </span>
        <span v-if="publishMode === 'direct'" class="form-hint" style="display:block;margin-top:4px;color:#e6a23c">
          ⚠️ 直接发布将立即推送给订阅用户，请确认内容无误
        </span>
      </el-card>

      <div class="action-bar">
        <el-button @click="handleReset">创建新文章</el-button>
        <el-button
          v-if="currentTaskId"
          type="primary"
          @click="router.push(`/articles/${currentTaskId}`)"
        >
          查看详情
        </el-button>
      </div>
    </div>

    <!-- ======== Phase: FAILED ======== -->
    <div v-if="currentPhase === 'FAILED'" class="phase-failed">
      <el-result icon="error" title="生成失败" sub-title="文章生成过程中出现错误，请重试">
        <template #extra>
          <el-button type="primary" @click="handleReset">重新创建</el-button>
        </template>
      </el-result>
    </div>
  </div>

  <!-- ======== Feed Source Article Picker Dialog ======== -->
  <el-dialog v-model="showFeedArticlePicker" title="选择参考文章" width="720px" top="5vh">
    <div v-if="loadingFeedArticles" class="loading-section" style="padding: 24px; text-align: center;">
      <el-skeleton :rows="3" animated />
    </div>
    <template v-else>
      <p style="color: #909399; font-size: 13px; margin-bottom: 12px;">
        选择要仿写的参考文章（已选 {{ selectedFeedArticleIds.length }} 篇）。
        AI 将严格模仿选中文章的写作风格、语气和句式结构。
      </p>
      <div v-if="feedSourceArticles.length === 0" style="padding: 24px; text-align: center; color: #909399;">
        暂无文章，请先在投喂源中「抓取」文章
      </div>
      <div v-else class="feed-article-list">
        <div
          v-for="article in feedSourceArticles"
          :key="article.id"
          class="feed-article-item"
          :class="{ selected: selectedFeedArticleIds.includes(article.id) }"
          @click="toggleFeedArticle(article.id)"
        >
          <el-checkbox :checked="selectedFeedArticleIds.includes(article.id)" @click.stop="toggleFeedArticle(article.id)" />
          <div class="article-info">
            <strong>{{ article.title || '无标题' }}</strong>
            <div class="article-meta">
              <span v-if="article.word_count">{{ article.word_count }} 字</span>
              <span v-if="article.is_analyzed" style="color: #67c23a;">已分析</span>
              <span v-if="article.summary" class="article-summary">{{ article.summary.slice(0, 100) }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
    <template #footer>
      <el-button @click="showFeedArticlePicker = false">取消</el-button>
      <el-button type="primary" @click="showFeedArticlePicker = false" :disabled="selectedFeedArticleIds.length === 0">
        确定（已选 {{ selectedFeedArticleIds.length }} 篇）
      </el-button>
    </template>
  </el-dialog>

  <!-- ======== Local Asset Picker Dialog ======== -->
  <el-dialog v-model="showAssetPicker" title="选择本地素材图片" width="760px" top="5vh">
    <div v-if="loadingAssets" style="padding: 24px; text-align: center;">
      <el-skeleton :rows="3" animated />
    </div>
    <template v-else>
      <p style="color: #909399; font-size: 13px; margin-bottom: 12px;">
        选择要用在文章中的图片。AI 会根据文章内容自动匹配合适的位置。
        已选 {{ selectedImageUrls.length }} 张。
      </p>
      <div v-if="localAssets.length === 0" style="padding: 24px; text-align: center; color: #909399;">
        暂无本地素材，请先在「素材库」中上传图片
      </div>
      <div v-else class="asset-grid-dialog">
        <div
          v-for="asset in localAssets"
          :key="asset.id"
          class="asset-item-dialog"
          :class="{ selected: selectedImageUrls.includes(asset.preview_url || '') }"
          @click="toggleAsset(asset.preview_url || '')"
        >
          <div class="asset-thumb-dialog">
            <el-image
              v-if="asset.preview_url"
              :src="asset.preview_url"
              fit="cover"
              style="width: 100%; height: 100%;"
            />
            <div v-else class="no-preview">无预览</div>
          </div>
          <div class="asset-label-dialog">
            <span class="asset-name-dialog">{{ asset.original_filename || asset.filename }}</span>
          </div>
          <div v-if="selectedImageUrls.includes(asset.preview_url || '')" class="asset-checked-badge">✓</div>
        </div>
      </div>
    </template>
    <template #footer>
      <el-button @click="showAssetPicker = false">取消</el-button>
      <el-button type="primary" @click="showAssetPicker = false" :disabled="selectedImageUrls.length === 0">
        确定（已选 {{ selectedImageUrls.length }} 张）
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.article-create {
  max-width: 960px;
  margin: 0 auto;
  padding: 20px;
}

.full-width {
  width: 100%;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
}

.card-subtitle {
  font-size: 14px;
  color: #909399;
  margin-left: 8px;
}

.hint-text {
  color: #909399;
  text-align: center;
  margin-top: 12px;
}

.mt-4 {
  margin-top: 16px;
}

/* Title options */
.title-options {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.title-option-card {
  border: 2px solid #e4e7ed;
  border-radius: 8px;
  padding: 20px;
  cursor: pointer;
  transition: all 0.3s;
}

.title-option-card:hover {
  border-color: #409eff;
  box-shadow: 0 2px 12px rgba(64, 158, 255, 0.1);
}

.title-option-card.selected {
  border-color: #409eff;
  background: #ecf5ff;
}

.option-index {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
}

.option-main-title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 6px;
  color: #303133;
}

.option-sub-title {
  font-size: 14px;
  color: #606266;
}

/* Outline */
.outline-display {
  padding: 0 8px;
}

.outline-section {
  margin-bottom: 20px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 6px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.section-number {
  font-size: 12px;
  color: #909399;
  background: #e4e7ed;
  padding: 2px 8px;
  border-radius: 4px;
}

.section-title {
  font-size: 16px;
  font-weight: 500;
  color: #303133;
}

.section-points {
  margin: 0;
  padding-left: 20px;
}

.section-points li {
  margin-bottom: 4px;
  color: #606266;
  line-height: 1.6;
}

/* Content streaming */
.streaming-text {
  white-space: pre-wrap;
  word-break: break-word;
  color: #606266;
  font-size: 13px;
  max-height: 400px;
  overflow-y: auto;
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  margin-top: 12px;
}

.content-stream {
  min-height: 200px;
}

.rendered-content {
  line-height: 1.8;
  color: #303133;
}

.rendered-content :deep(h2) {
  margin: 20px 0 12px;
  font-size: 20px;
  border-bottom: 1px solid #e4e7ed;
  padding-bottom: 8px;
}

.rendered-content :deep(p) {
  margin-bottom: 12px;
}

.rendered-content :deep(img) {
  max-width: 100%;
  border-radius: 4px;
  margin: 12px 0;
}

.generating-placeholder {
  text-align: center;
  padding: 40px 20px;
}

/* Image grid */
.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px;
}

.image-item {
  text-align: center;
}

.image-item .el-image {
  width: 120px;
  height: 80px;
  border-radius: 4px;
}

.image-label {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

/* Completed */
.article-cover {
  margin-bottom: 20px;
}

.article-cover .el-image {
  width: 100%;
  max-height: 400px;
  border-radius: 8px;
}

.article-content {
  line-height: 1.8;
  font-size: 15px;
}

.article-content :deep(h2) {
  margin: 24px 0 12px;
  font-size: 22px;
}

.article-content :deep(p) {
  margin-bottom: 16px;
}

.article-content :deep(img) {
  max-width: 100%;
  border-radius: 6px;
  margin: 16px 0;
}

.action-bar {
  margin-top: 20px;
  display: flex;
  gap: 12px;
  justify-content: center;
}

.logs-card {
  margin-top: 16px;
}

.image-progress-card {
  margin-top: 16px;
}

.publish-card {
  margin-top: 16px;
}

.watermark-toggle-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.publish-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* Feed source article picker */
.feed-source-wrapper {
  width: 100%;
}

.feed-articles-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.selected-count {
  font-size: 13px;
  color: #409eff;
  font-weight: 500;
}

.selected-count.muted {
  color: #909399;
  font-weight: 400;
}

.feed-article-list {
  display: grid;
  gap: 8px;
  max-height: 440px;
  overflow-y: auto;
}

.feed-article-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
}

.feed-article-item:hover {
  border-color: #409eff;
  background: #f5f9ff;
}

.feed-article-item.selected {
  border-color: #409eff;
  background: #ecf5ff;
}

.feed-article-item .article-info {
  flex: 1;
  min-width: 0;
}

.feed-article-item .article-info strong {
  display: block;
  font-size: 14px;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.article-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #909399;
}

/* Local image source options */
.image-source-options {
  margin-top: 8px;
}

.manual-image-selector {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.selected-previews {
  display: flex;
  align-items: center;
  gap: 4px;
}

.mini-preview {
  width: 48px;
  height: 48px;
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid #e4e7ed;
}

.more-badge {
  font-size: 11px;
  color: #909399;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
}

/* Asset picker dialog */
.asset-grid-dialog {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
  max-height: 460px;
  overflow-y: auto;
}

.asset-item-dialog {
  position: relative;
  border: 2px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
}

.asset-item-dialog:hover {
  border-color: #409eff;
}

.asset-item-dialog.selected {
  border-color: #409eff;
  background: #ecf5ff;
}

.asset-thumb-dialog {
  height: 100px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  overflow: hidden;
}

.no-preview {
  color: #909399;
  font-size: 12px;
}

.asset-label-dialog {
  padding: 6px 8px;
}

.asset-name-dialog {
  display: block;
  font-size: 11px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.asset-checked-badge {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 700;
}

/* Gallery card renders */
.image-preview-card .gallery-main {
  width: 100%;
  background: #f0f0f0;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}
.image-preview-card .gallery-main img {
  max-width: 100%;
  max-height: 65vh;
  width: auto;
  height: auto;
  object-fit: contain;
}

/* Gallery widget */
.gallery-widget { width: 100%; }
.gallery-widget .gallery-main {
  width: 100%; background: #f0f0f0; border-radius: 8px; overflow: hidden;
  display: flex; align-items: center; justify-content: center; min-height: 300px;
}
.gallery-widget .gallery-main img {
  max-width: 100%; max-height: 65vh; width: auto; height: auto; object-fit: contain;
}
.gallery-widget .gallery-thumbs {
  display: flex; gap: 8px; margin-top: 12px; overflow-x: auto; padding: 4px 0;
}
.gallery-widget .thumb-item {
  flex: 0 0 80px; height: 60px; border-radius: 6px; overflow: hidden;
  cursor: pointer; border: 2px solid transparent; opacity: 0.6; transition: all .2s;
}
.gallery-widget .thumb-item.active {
  border-color: #07c160; opacity: 1;
}
.gallery-widget .thumb-item img {
  width: 100%; height: 100%; object-fit: cover; display: block;
}
</style>
