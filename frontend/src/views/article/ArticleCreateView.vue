<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import {
  createArticle,
  getArticle,
  getExecutionLogs,
  publishDraft,
} from '@/api/article'
import {
  importErpProductImage,
  listErpProductSources,
  searchErpProducts,
  type ErpProduct,
  type ErpProductSource,
} from '@/api/erpProducts'
import client from '@/api/client'
import type { Article, Account, KnowledgeBase, FeedSource } from '@/api/types'
import { marked } from 'marked'
import { sanitizeHtml } from '@/utils/sanitizer'

const router = useRouter()

// ==================== State ====================
const topic = ref('')
const style = ref('')
const contentType = ref('article')
type CoverImageSource = 'local' | 'DASHSCOPE' | 'erp'
type BodyImageSource = 'DASHSCOPE' | 'LOCAL' | 'ERP'
type ImagePickerTarget = 'cover' | 'body'

// 封面来源与正文来源属于两条独立数据流，不能共享选择状态。
const imageSource = ref<CoverImageSource>('DASHSCOPE')
const enabledImageMethods = ref<BodyImageSource[]>(['DASHSCOPE'])
const articleCount = ref(1)
const loading = ref(false)
const currentTaskId = ref('')
const currentArticle = ref<Article | null>(null)
const agentLogs = ref<any[]>([])

// Phase tracking
type Phase =
  | 'INPUT'
  | 'CONTENT_GENERATING'
  | 'COMPLETED'
  | 'FAILED'

const currentPhase = ref<Phase>('INPUT')

// 视频配置
const videoAspectRatio = ref('9:16')

// Image progress

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

// 本地素材弹窗同时服务封面单选与正文多选，目标由显式上下文决定。
const localAssets = ref<any[]>([])
const loadingAssets = ref(false)
const selectedCoverImageUrl = ref('')
const selectedBodyImageUrls = ref<string[]>([])
const showAssetPicker = ref(false)
const assetPickerTarget = ref<ImagePickerTarget>('cover')

// ERP 弹窗同样区分封面和正文；ERP 凭证只由后端保管，前端只接收规范化产品数据。
const showErpProductPicker = ref(false)
const erpPickerTarget = ref<ImagePickerTarget>('cover')
const erpProductSources = ref<ErpProductSource[]>([])
const selectedErpProductSource = ref('')
const erpProductModel = ref('')
const erpProductSeries = ref('')
const erpProducts = ref<ErpProduct[]>([])
const erpProductPageNo = ref(1)
const erpProductPageSize = 30
const erpProductTotal = ref(0)
const loadingErpProducts = ref(false)
const importingErpImageUrl = ref('')

async function loadErpProductSources() {
  try {
    erpProductSources.value = await listErpProductSources()
    if (!selectedErpProductSource.value && erpProductSources.value.length > 0) {
      selectedErpProductSource.value = erpProductSources.value[0].key
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || 'ERP 产品素材源未配置或加载失败')
  }
}

/**
 * 打开 ERP 产品选图器并记录用途。
 *
 * 用途必须在打开弹窗前固定，避免异步查询完成后把正文图片误写入封面字段。
 */
async function openErpProductPicker(target: ImagePickerTarget) {
  erpPickerTarget.value = target
  showErpProductPicker.value = true
  if (erpProductSources.value.length === 0) {
    await loadErpProductSources()
  }
  if (selectedErpProductSource.value && erpProducts.value.length === 0) {
    await searchProductsFromErp()
  }
}

async function searchProductsFromErp(pageNo = 1) {
  if (!selectedErpProductSource.value) {
    ElMessage.warning('请先选择 ERP 产品品牌')
    return
  }
  loadingErpProducts.value = true
  erpProductPageNo.value = pageNo
  try {
    const page = await searchErpProducts(selectedErpProductSource.value, {
      pageNo,
      pageSize: erpProductPageSize,
      productModel: erpProductModel.value.trim() || undefined,
      series: erpProductSeries.value.trim() || undefined,
    })
    erpProducts.value = page.items || []
    erpProductTotal.value = page.total || 0
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '查询 ERP 产品失败')
  } finally {
    loadingErpProducts.value = false
  }
}

/** 用户切换来源或筛选条件时从首页开始，防止旧页码落到空结果页。 */
function resetErpProductSearch() {
  void searchProductsFromErp(1)
}

async function importSelectedErpProduct(product: ErpProduct) {
  if (!selectedErpProductSource.value || importingErpImageUrl.value) return
  importingErpImageUrl.value = product.image_url
  try {
    const imported = await importErpProductImage(selectedErpProductSource.value, product)
    if (erpPickerTarget.value === 'cover') {
      selectedCoverImageUrl.value = imported.preview_url
      // 保留 ERP 作为封面入口；后端实际接收的是导入后的本地素材 URL。
      imageSource.value = 'erp'
      ElMessage.success(`已导入「${product.name}」并设为文章封面`)
    } else {
      // ERP 正文图导入后与本地图片使用相同 URL 契约，同时按 URL 去重。
      if (!selectedBodyImageUrls.value.includes(imported.preview_url)) {
        selectedBodyImageUrls.value.push(imported.preview_url)
      }
      ElMessage.success(`已导入「${product.name}」并加入正文配图`)
    }
    await loadLocalAssets()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '导入 ERP 产品图片失败')
  } finally {
    importingErpImageUrl.value = ''
  }
}

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

/** 根据当前弹窗用途执行封面单选或正文多选。 */
function selectLocalAsset(url: string) {
  if (!url) return
  if (assetPickerTarget.value === 'cover') {
    selectedCoverImageUrl.value = url
    return
  }

  const selectedIndex = selectedBodyImageUrls.value.indexOf(url)
  if (selectedIndex >= 0) {
    selectedBodyImageUrls.value.splice(selectedIndex, 1)
  } else {
    selectedBodyImageUrls.value.push(url)
  }
}

/** 打开本地素材选择器，正文模式允许多选，封面模式保持单选。 */
async function openLocalAssetPicker(target: ImagePickerTarget) {
  assetPickerTarget.value = target
  showAssetPicker.value = true
  if (localAssets.value.length === 0) {
    await loadLocalAssets()
  }
}

/** 判断素材是否属于当前弹窗的已选集合，用于统一卡片选中态。 */
function isLocalAssetSelected(url: string): boolean {
  if (assetPickerTarget.value === 'cover') return selectedCoverImageUrl.value === url
  return selectedBodyImageUrls.value.includes(url)
}

/** ERP 与本地正文入口最终都映射为后端可识别的 LOCAL 方法。 */
function resolveBodyImageMethods(): string[] {
  const methods = new Set<string>()
  if (enabledImageMethods.value.includes('DASHSCOPE')) methods.add('DASHSCOPE')
  if (enabledImageMethods.value.includes('LOCAL') || enabledImageMethods.value.includes('ERP')) {
    methods.add('LOCAL')
  }
  return Array.from(methods)
}

/**
 * 切换产品素材的选择入口。
 *
 * 本地素材库和 ERP 产品库是并列的选图入口；ERP 图会在用户明确选择后导入本地，
 * 因此不会让远端地址进入生成或发布流程。
 */
function handleImageSourceChange() {
  if (imageSource.value === 'local') {
    selectedCoverImageUrl.value = ''
    loadLocalAssets()
  } else if (imageSource.value === 'erp') {
    // ERP 封面只接受用户明确选择并导入的产品图，不能退化为随机图片。
    selectedCoverImageUrl.value = ''
    void openErpProductPicker('cover')
  } else {
    selectedCoverImageUrl.value = ''
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

// ==================== Computed ====================
const canCreate = computed(() => {
  if (contentType.value === 'article') {
    // 图文文章的封面与正文来源必须分别校验，隐藏字段不能影响纯图片或视频任务。
    if ((imageSource.value === 'local' || imageSource.value === 'erp') && !selectedCoverImageUrl.value) return false
    // 不允许空来源触发后端默认 AI 生图；仅选手动来源时至少要明确选择一张正文图。
    if (enabledImageMethods.value.length === 0) return false
    const manualOnly = !enabledImageMethods.value.includes('DASHSCOPE')
    if (manualOnly && selectedBodyImageUrls.value.length === 0) return false
  }
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
  { value: 'DASHSCOPE', label: 'AI 生图（通义万相）' },
  { value: 'LOCAL', label: '本地素材库' },
  { value: 'ERP', label: 'ERP 产品库' },
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
      // 封面来源与正文配图方式独立提交，不能再把封面图片误传给正文图片 Agent。
      payload.image_source = imageSource.value === 'erp' ? 'local' : imageSource.value
      payload.enabled_image_methods = resolveBodyImageMethods()
      payload.mode = 'auto'
      payload.article_count = articleCount.value
      payload.knowledge_base_ids = selectedKbIds.value.length > 0 ? selectedKbIds.value : undefined
      payload.source_feed_id = selectedFeedSourceId.value ?? undefined
      payload.feed_article_ids = selectedFeedArticleIds.value.length > 0 ? selectedFeedArticleIds.value : undefined
      payload.selected_cover_image_url = selectedCoverImageUrl.value || undefined
      // 正文仅接收正文选图集合，封面 URL 永远不会混入该字段。
      const manualBodySourceEnabled = enabledImageMethods.value.includes('LOCAL') || enabledImageMethods.value.includes('ERP')
      if (manualBodySourceEnabled && selectedBodyImageUrls.value.length > 0) {
        payload.selected_image_urls = selectedBodyImageUrls.value
      }
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
    console.log('[DEBUG] createArticle response:', JSON.stringify(article).slice(0, 500))

    // 后端会把生成阶段错误写入文章状态；不能将失败任务误展示为已完成。
    if (article.status === 'failed') {
      currentPhase.value = 'FAILED'
      ElMessage.error(article.error_message || '文章生成失败，请查看执行日志')
      return
    }

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

    // 后端创建接口完成后返回完整文章，全程不再需要人工确认标题或大纲。
    currentPhase.value = 'COMPLETED'
    ElMessage.success('文章生成完成！')
    loadArticle()
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
  currentPhase.value = 'INPUT'
  topic.value = ''
  style.value = ''
  currentTaskId.value = ''
  currentArticle.value = null
  agentLogs.value = []
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  // 投喂仿写完成后返回的是 HTML；直接清洗并预览才能保留原有节点层级与样式。
  if (/^\s*</.test(text)) return sanitizeHtml(text)
  const html = marked.parse(text, { async: false }) as string
  return sanitizeHtml(html)
}

onMounted(() => {
  loadAccounts()
  loadKnowledgeBases()
  loadFeedSources()
  loadWatermarkConfig()
  loadErpProductSources()
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
            <el-col v-if="contentType === 'article'" :span="12">
              <el-form-item label="封面图片来源">
                <!-- 封面来源与下方正文配图方式完全独立。 -->
                <el-radio-group v-model="imageSource" @change="handleImageSourceChange">
                  <el-radio value="DASHSCOPE">AI 生图</el-radio>
                  <el-radio value="local">本地素材库</el-radio>
                  <el-radio value="erp">ERP 产品库</el-radio>
                </el-radio-group>
              </el-form-item>
              <!-- 本地素材库封面必须明确选择一张，避免自动使用不相关图片。 -->
              <div v-if="imageSource === 'local'" class="image-source-options">
                <div class="manual-image-selector">
                  <el-button size="small" @click="openLocalAssetPicker('cover')" :type="selectedCoverImageUrl ? 'success' : 'default'">
                    {{ selectedCoverImageUrl ? '已选择封面' : '选择一张封面' }}
                  </el-button>
                  <div v-if="selectedCoverImageUrl" class="selected-previews">
                    <div class="mini-preview">
                      <el-image :src="selectedCoverImageUrl" fit="cover" style="width: 48px; height: 48px; border-radius: 4px;" />
                    </div>
                  </div>
                </div>
              </div>
              <div v-else-if="imageSource === 'erp'" class="image-source-options">
                <div class="manual-image-selector">
                  <el-button size="small" type="primary" @click="openErpProductPicker('cover')">
                    查询并选择封面
                  </el-button>
                  <span v-if="!selectedCoverImageUrl" class="form-hint">请选择一张 ERP 产品图片作为封面</span>
                  <div v-else class="selected-previews">
                    <div class="mini-preview">
                      <el-image :src="selectedCoverImageUrl" fit="cover" style="width: 48px; height: 48px; border-radius: 4px;" />
                    </div>
                  </div>
                </div>
              </div>
            </el-col>
          </el-row>

          <!-- 正文配图来源只属于图文文章，不能与封面或纯图片任务复用。 -->
          <el-form-item v-if="contentType === 'article'" label="正文配图来源（可多选）">
            <div class="body-image-source-control">
              <el-checkbox-group v-model="enabledImageMethods">
                <el-checkbox v-for="opt in imageMethodOptions" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </el-checkbox>
              </el-checkbox-group>

              <div v-if="enabledImageMethods.includes('LOCAL')" class="manual-image-selector">
                <el-button size="small" @click="openLocalAssetPicker('body')">正文使用本地素材库</el-button>
              </div>
              <div v-if="enabledImageMethods.includes('ERP')" class="manual-image-selector">
                <el-button size="small" type="primary" @click="openErpProductPicker('body')">正文使用 ERP 产品库</el-button>
              </div>

              <div v-if="selectedBodyImageUrls.length > 0" class="body-image-selection-summary">
                <span>正文已选 {{ selectedBodyImageUrls.length }} 张</span>
                <div class="selected-previews">
                  <div v-for="url in selectedBodyImageUrls.slice(0, 4)" :key="url" class="mini-preview">
                    <el-image :src="url" fit="cover" style="width: 48px; height: 48px; border-radius: 4px;" />
                  </div>
                  <span v-if="selectedBodyImageUrls.length > 4" class="more-badge">+{{ selectedBodyImageUrls.length - 4 }}</span>
                </div>
                <el-button text type="danger" size="small" @click="selectedBodyImageUrls = []">清空正文选图</el-button>
              </div>
              <span class="form-hint">本地与 ERP 图片会作为正文配图使用；ERP 图片会先安全导入本地素材库。</span>
            </div>
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

      <el-card class="article-preview" v-if="currentArticle">
        <template #header>
          <span class="card-title">{{ currentArticle?.main_title || '文章完成' }}</span>
          <span class="card-subtitle">{{ currentArticle?.sub_title }}</span>
        </template>

        <div v-if="currentArticle?.cover_image" class="article-cover">
          <el-image :src="currentArticle.cover_image" fit="cover" />
        </div>

        <div
          class="article-content"
          v-html="renderMarkdown(currentArticle?.full_content || '')"
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
  <el-dialog
    v-model="showAssetPicker"
    :title="assetPickerTarget === 'cover' ? '选择本地封面图片' : '选择本地正文图片（可多选）'"
    width="760px"
    top="5vh"
  >
    <div v-if="loadingAssets" style="padding: 24px; text-align: center;">
      <el-skeleton :rows="3" animated />
    </div>
    <template v-else>
      <p style="color: #909399; font-size: 13px; margin-bottom: 12px;">
        {{ assetPickerTarget === 'cover' ? '选择一张图片作为文章封面。' : '选择一张或多张图片用于正文，已选图片可再次点击取消。' }}
      </p>
      <div v-if="localAssets.length === 0" style="padding: 24px; text-align: center; color: #909399;">
        暂无本地素材，请先在「素材库」中上传图片
      </div>
      <div v-else class="asset-grid-dialog">
        <div
          v-for="asset in localAssets"
          :key="asset.id"
          class="asset-item-dialog"
          :class="{ selected: isLocalAssetSelected(asset.preview_url || '') }"
          @click="selectLocalAsset(asset.preview_url || '')"
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
          <div v-if="isLocalAssetSelected(asset.preview_url || '')" class="asset-checked-badge">✓</div>
        </div>
      </div>
    </template>
    <template #footer>
      <el-button @click="showAssetPicker = false">取消</el-button>
      <el-button
        type="primary"
        @click="showAssetPicker = false"
        :disabled="assetPickerTarget === 'cover' && !selectedCoverImageUrl"
      >
        {{ assetPickerTarget === 'cover' ? '确定使用此封面' : `完成（已选 ${selectedBodyImageUrls.length} 张）` }}
      </el-button>
    </template>
  </el-dialog>

  <!-- ERP 产品图片先复制到本地素材库，再交由文章生成与发布链路使用。 -->
  <el-dialog
    v-model="showErpProductPicker"
    :title="erpPickerTarget === 'cover' ? '从 ERP 产品库选择封面' : '从 ERP 产品库选择正文图片'"
    width="860px"
    top="5vh"
  >
    <div class="erp-search-form">
      <el-select v-model="selectedErpProductSource" placeholder="选择品牌" style="width: 180px" @change="resetErpProductSearch">
        <el-option v-for="source in erpProductSources" :key="source.key" :label="source.name" :value="source.key" />
      </el-select>
      <el-input v-model="erpProductModel" clearable placeholder="按产品型号筛选" @keyup.enter="searchProductsFromErp" />
      <el-input v-model="erpProductSeries" clearable placeholder="按系列筛选" @keyup.enter="searchProductsFromErp" />
      <el-button type="primary" :loading="loadingErpProducts" @click="resetErpProductSearch">查询</el-button>
    </div>
    <p class="form-hint">选择后会复制到本地素材库，再用于文章配图和公众号发布；不会直接引用 ERP 远端图片。</p>
    <div v-if="loadingErpProducts" style="padding: 24px; text-align: center;"><el-skeleton :rows="3" animated /></div>
    <div v-else-if="erpProductSources.length === 0" class="empty-erp-products">尚未配置 ERP 产品来源，请在后端 `.env` 配置后重启服务。</div>
    <div v-else-if="erpProducts.length === 0" class="empty-erp-products">未查询到带报价图片的产品。</div>
    <div v-else class="erp-product-grid">
      <article v-for="product in erpProducts" :key="`${product.name}-${product.image_url}`" class="erp-product-card">
        <el-image :src="product.image_url" fit="cover" class="erp-product-image" />
        <div class="erp-product-info">
          <strong>{{ product.name }}</strong>
          <span v-if="product.series.length">{{ product.series.join(' / ') }}</span>
          <span v-if="product.categories.length">{{ product.categories.join(' / ') }}</span>
        </div>
        <el-button
          type="primary"
          size="small"
          :loading="importingErpImageUrl === product.image_url"
          :disabled="Boolean(importingErpImageUrl) && importingErpImageUrl !== product.image_url"
          @click="importSelectedErpProduct(product)"
        >{{ erpPickerTarget === 'cover' ? '导入并设为封面' : '导入并加入正文' }}</el-button>
      </article>
    </div>
    <div v-if="erpProductTotal > erpProductPageSize" class="erp-product-pagination">
      <el-pagination
        v-model:current-page="erpProductPageNo"
        :page-size="erpProductPageSize"
        :total="erpProductTotal"
        layout="prev, pager, next, jumper, total"
        @current-change="searchProductsFromErp"
      />
    </div>
    <template #footer><el-button @click="showErpProductPicker = false">完成</el-button></template>
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

.body-image-source-control {
  display: grid;
  width: 100%;
  gap: 8px;
}

.body-image-selection-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fafafa;
  color: #606266;
  font-size: 13px;
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

/* ERP 产品查询结果保持与本地素材选择相同的信息密度，图片主体优先。 */
.erp-search-form {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.erp-product-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  max-height: 480px;
  overflow-y: auto;
  padding: 2px;
}

.erp-product-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.erp-product-card {
  display: grid;
  grid-template-rows: 150px auto auto;
  gap: 8px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  padding: 8px;
  background: #fff;
}

.erp-product-image {
  width: 100%;
  height: 150px;
  border-radius: 4px;
  overflow: hidden;
  background: #f5f7fa;
}

.erp-product-info {
  display: grid;
  gap: 3px;
  min-height: 50px;
  font-size: 12px;
  color: #909399;
}

.erp-product-info strong {
  color: #303133;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-erp-products {
  padding: 36px 16px;
  color: #909399;
  text-align: center;
}

@media (max-width: 760px) {
  .erp-search-form { grid-template-columns: 1fr; }
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
