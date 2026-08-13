<script setup lang="ts">
import { onMounted, computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import client from '@/api/client'
import type { Account, ArticleFormatProfile, FeedSource, WritingStyleTemplateOption } from '@/api/types'
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
  format_profile_id: number | null
  template_rotation_config?: {
    enabled: boolean
    profile_ids: number[]
    basis: 'publish_day' | 'publish_run'
    uses_per_template: number
  } | null
  style: string | null
  knowledge_base_ids: number[] | null
  day_of_week: number
  publish_times: string[]
  article_slots: ArticleSlot[] | null
  articles_per_day: number
  html_image_count: number
  layout_mode: 'standard' | 'seamless_poster'
  public_count: number
  private_count: number
  account_ids: number[] | null
  publish_mode: string
  publish_domain: 'public' | 'private'
  image_source: string
  enabled_image_methods: string[] | null
  enable_watermark: boolean
  watermark_config?: {
    enabled: boolean
    type: 'text' | 'logo'
    content?: string | null
    font_size?: number
    locked?: boolean
  } | null
  erp_image_config?: {
    source_key: string
    commodity_category?: string | null
    repeat_after_days: number
    image_count: number
    selection_scope?: string | null
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
const formatProfiles = ref<ArticleFormatProfile[]>([])
const erpProductSources = ref<ErpProductSource[]>([])
/**
 * 写作模板由后端目录接口统一提供。开发服务尚未重启或接口暂不可用时保留内置
 * 她格模板，确保运营人员仍能选择已部署在 Worker 中的稳定模板编号。
 */
const builtinWritingStyleTemplates: WritingStyleTemplateOption[] = [
  {
    identifier: 'zhongxiwujie_east_west_living',
    label: '中西无界 - 东方奢雅生活',
    description: '用产品承接东方神韵与当代奢雅生活，标题更有文化感和画面感。',
  },
  {
    identifier: 'xiehuai_oriental_living',
    label: '写怀 - 东方留白生活',
    description: '围绕产品与安静居住感写作，标题以温润、留白的完整短句呈现。',
  },
  {
    identifier: 'jianzhi_artful_living',
    label: '剪纸系列 - 当代艺术生活',
    description: '从产品、光影与剪纸艺术感切入，形成温暖有画面的生活标题。',
  },
  {
    identifier: 'shege_enterprise_ai_service',
    label: '她格 - 企业 AI 服务',
    description: '围绕中小企业经营问题，输出可落地的 AI 转型建议。',
  },
]
const writingStyleTemplates = ref<WritingStyleTemplateOption[]>([...builtinWritingStyleTemplates])
const showForm = ref(false)
const editing = ref(false)
const currentId = ref<number | null>(null)
const fixedWatermarkLocked = ref(false)
const rotationConfigTouched = ref(false)
type FooterTemplateMode = 'none' | 'consultation_card' | 'custom'

interface ConsultationQrCode {
  label: string
  url: string
}

/**
 * 固定底部只在界面层展示业务字段，JSON 是后端渲染协议，不暴露给运营人员填写。
 * 保留原始 footer_template 字段是为了兼容所有历史任务和已有发布链路。
 */
const footerTemplateMode = ref<FooterTemplateMode>('none')
const customFooterTemplate = ref('')
const consultationCard = reactive({
  brand: '',
  phone: '',
  qrcodes: [] as ConsultationQrCode[],
})
const qrUploadTargetIndex = ref<number | null>(null)

function createDefaultTemplateRotationConfig() {
  return {
    enabled: false,
    profile_ids: [] as number[],
    basis: 'publish_day' as 'publish_day' | 'publish_run',
    uses_per_template: 1,
  }
}

const form = reactive({
  name: '',
  writing_mode: 'free',
  topic: '',
  feed_source_ids: [] as number[],
  feed_source_id: null as number | null,
  feed_article_ids: [] as number[],
  format_profile_id: null as number | null,
  template_rotation_config: createDefaultTemplateRotationConfig(),
  style: '',
  knowledge_base_ids: [] as number[],
  day_of_week: -1,
  publish_times: ['08:00'] as string[],
  articles_per_day: 1,
  html_image_count: 5,
  layout_mode: 'standard' as 'standard' | 'seamless_poster',
  account_ids: [] as number[],
  publish_mode: 'draft',
  publish_domain: 'public' as 'public' | 'private',
  image_source: 'DASHSCOPE',
  erp_source_key: '',
  erp_commodity_category: '',
  erp_repeat_after_days: 3,
  erp_image_count: 8,
  // 品牌级防重范围由初始化脚本写入；编辑任务时原样回传，避免运营保存表单
  // 时意外丢失公域/私域共享选品规则。
  erp_selection_scope: '',
  footer_template: '',
  content_type: 'article',
  enabled_image_methods: ['DASHSCOPE'],
  enable_watermark: false,
})

// 轮换顺序以表单数组为唯一数据源；派生值仅供模板渲染，避免重排时出现副本不同步。
const rotationProfileIds = computed(() => form.template_rotation_config.profile_ids)

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
  if (!preserveSelectedArticles) {
    form.feed_article_ids = []
    // 投喂源切换后旧模板可能属于另一篇文章；交给后端按新来源自动绑定，
    // 用户仍可在“格式模板覆盖”中主动选择特定来源文章版本。
    form.format_profile_id = null
  }
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

/** 当前已选的公共模板；未知值按历史配置保留，避免编辑旧任务时丢失数据。 */
const selectedWritingStyleTemplate = computed(() =>
  writingStyleTemplates.value.find(item => item.identifier === form.style) || null,
)

const hasLegacyWritingStyle = computed(() => Boolean(
  form.style && !selectedWritingStyleTemplate.value,
))

function getWritingStyleTemplateLabel(style: string | null): string {
  if (!style) return '自动匹配内容来源'
  return writingStyleTemplates.value.find(item => item.identifier === style)?.label || '历史任务风格'
}

function getAccountName(ids: number[] | null): string {
  if (!ids || ids.length === 0) return '未指定'
  return ids.map(id => accounts.value.find(a => a.id === id)?.name || `#${id}`).join(', ')
}

function getFeedSourceNames(ids: number[] | null): string {
  if (!ids || ids.length === 0) return '-'
  return ids.map(id => feedSources.value.find(f => f.id === id)?.name || `#${id}`).join(', ')
}

function getFormatProfileLabel(profileId: number | null): string {
  if (!profileId) return '自动匹配（未绑定时沿用历史流程）'
  const profile = formatProfiles.value.find(item => item.id === profileId)
  return profile ? `${profile.name} v${profile.version}` : `模板 #${profileId}`
}

function getRotationProfileLabel(profileId: number): string {
  const profile = formatProfiles.value.find(item => item.id === profileId)
  if (!profile) return `模板 #${profileId}`
  const source = profile.source_name || '未知投喂源'
  const article = profile.source_article_title || profile.name
  return `${source} / ${article} v${profile.version}`
}

function markRotationConfigTouched() {
  rotationConfigTouched.value = true
}

function moveRotationProfile(index: number, direction: -1 | 1) {
  const targetIndex = index + direction
  const profileIds = form.template_rotation_config.profile_ids
  if (targetIndex < 0 || targetIndex >= profileIds.length) return
  const currentId = profileIds[index]
  profileIds[index] = profileIds[targetIndex]
  profileIds[targetIndex] = currentId
  markRotationConfigTouched()
}

function getErpSourceName(sourceKey: string): string {
  return erpProductSources.value.find(source => source.key === sourceKey)?.name || sourceKey
}

async function load() {
  loading.value = true
  try {
    const [t, a, f, k, profiles, erpSources, styleTemplates] = await Promise.all([
      client.get<{ total: number; items: ScheduledTask[] }>('/scheduled-tasks'),
      client.get<{ items: Account[] }>('/accounts'),
      client.get<{ total: number; items: FeedSource[] }>('/feed-sources').catch(() => ({ data: { items: [] } })),
      client.get<{ items: any[] }>('/knowledge-bases').catch(() => ({ data: { items: [] } })),
      client.get<any[]>('/format-profiles').catch(() => ({ data: [] })),
      listErpProductSources().catch(() => []),
      client
        .get<WritingStyleTemplateOption[]>('/scheduled-tasks/writing-style-templates')
        .catch(() => ({ data: builtinWritingStyleTemplates })),
    ])
    tasks.value = t.data.items || []
    accounts.value = a.data.items || []
    feedSources.value = f.data.items || []
    knowledgeBases.value = k.data.items || []
    formatProfiles.value = profiles.data || []
    erpProductSources.value = erpSources
    // 后端可用时以服务端目录为准；旧 API 尚未重启时保持内置兜底，选择模板和
    // 保存任务不受影响。
    writingStyleTemplates.value = styleTemplates.data?.length
      ? styleTemplates.data
      : [...builtinWritingStyleTemplates]
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
  form.format_profile_id = null
  form.template_rotation_config = createDefaultTemplateRotationConfig()
  form.style = ''
  form.knowledge_base_ids = []
  form.day_of_week = -1
  form.publish_times = ['08:00']
  form.articles_per_day = 1
  form.html_image_count = 5
  form.layout_mode = 'standard'
  form.account_ids = []
  form.publish_mode = 'draft'
  form.publish_domain = 'public'
  form.image_source = 'DASHSCOPE'
  form.erp_source_key = ''
  form.erp_commodity_category = ''
  form.erp_repeat_after_days = 3
  form.erp_image_count = 8
  form.footer_template = ''
  form.enabled_image_methods = ['DASHSCOPE']
  form.enable_watermark = false
  footerQrUrl.value = ''
  footerTemplateMode.value = 'none'
  customFooterTemplate.value = ''
  consultationCard.brand = ''
  consultationCard.phone = ''
  consultationCard.qrcodes = []
  qrUploadTargetIndex.value = null
  editing.value = false
  currentId.value = null
  fixedWatermarkLocked.value = false
  rotationConfigTouched.value = false
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
  form.format_profile_id = task.format_profile_id || null
  form.template_rotation_config = task.template_rotation_config
    ? {
        enabled: Boolean(task.template_rotation_config.enabled),
        profile_ids: [...(task.template_rotation_config.profile_ids || [])],
        basis: task.template_rotation_config.basis || 'publish_day',
        uses_per_template: task.template_rotation_config.uses_per_template || 1,
      }
    : createDefaultTemplateRotationConfig()
  rotationConfigTouched.value = false
  form.style = task.style || ''
  form.knowledge_base_ids = task.knowledge_base_ids || []
  form.day_of_week = task.day_of_week
  form.publish_times = task.publish_times?.length ? [...task.publish_times] : ['08:00']
  form.articles_per_day = task.articles_per_day
  form.html_image_count = task.html_image_count || 5
  form.layout_mode = task.layout_mode || 'standard'
  form.account_ids = task.account_ids || []
  form.publish_mode = task.publish_mode || 'draft'
  form.publish_domain = task.publish_domain || 'public'
  form.image_source = task.image_source || 'DASHSCOPE'
  form.erp_source_key = task.erp_image_config?.source_key || ''
  form.erp_commodity_category = task.erp_image_config?.commodity_category || ''
  form.erp_repeat_after_days = task.erp_image_config?.repeat_after_days || 3
  form.erp_image_count = task.erp_image_config?.image_count || 8
  form.erp_selection_scope = task.erp_image_config?.selection_scope || ''
  hydrateFooterTemplate(task.footer_template || '')
  form.enabled_image_methods = (task as any).enabled_image_methods || ['DASHSCOPE']
  form.enable_watermark = task.enable_watermark || false
  fixedWatermarkLocked.value = Boolean(task.watermark_config?.locked)
  // 自定义旧页脚仍支持 Markdown 二维码；咨询卡则在 hydrate 时直接回填到参数表单。
  const qrMatch = form.footer_template.match(/!\[二维码\]\(([^)]+)\)/)
  footerQrUrl.value = qrMatch ? qrMatch[1] : ''
  showForm.value = true
  // 编辑已有投喂任务时也必须加载文章列表，否则用户只能看到来源无法选择文章。
  if (form.feed_source_id) await handleFeedSourceChange(true)
  if (erpProductSources.value.length === 0) await loadErpProductSources()
}

/** 将历史 Markdown 与结构化咨询卡分别回填到对应表单，避免编辑时丢失已有配置。 */
function hydrateFooterTemplate(template: string) {
  form.footer_template = template
  customFooterTemplate.value = template
  footerTemplateMode.value = template.trim() ? 'custom' : 'none'
  consultationCard.brand = ''
  consultationCard.phone = ''
  consultationCard.qrcodes = []
  qrUploadTargetIndex.value = null

  try {
    const parsed = JSON.parse(template)
    if (parsed?.type !== 'consultation_card_v1') return
    footerTemplateMode.value = 'consultation_card'
    consultationCard.brand = String(parsed.brand || '')
    consultationCard.phone = String(parsed.phone || '')
    consultationCard.qrcodes = Array.isArray(parsed.qrcodes)
      ? parsed.qrcodes
        .filter((item: unknown) => item && typeof item === 'object')
        .map((item: any) => ({ label: String(item.label || '二维码'), url: String(item.url || '') }))
      : []
  } catch {
    // 非 JSON 是历史 Markdown 或自定义文本，继续由自定义内容模式承载。
  }
}

/** 切换模板时只初始化必要默认值，不覆盖用户已经填好的卡片参数。 */
function handleFooterTemplateModeChange(mode: FooterTemplateMode) {
  qrUploadTargetIndex.value = null
  if (mode === 'consultation_card' && consultationCard.qrcodes.length === 0) {
    consultationCard.qrcodes.push({ label: '企业微信', url: '' })
  }
}

function addConsultationQrCode() {
  consultationCard.qrcodes.push({ label: '二维码', url: '' })
}

function removeConsultationQrCode(index: number) {
  consultationCard.qrcodes.splice(index, 1)
  if (qrUploadTargetIndex.value === index) qrUploadTargetIndex.value = null
}

function openQrUploader(targetIndex: number | null = null) {
  qrUploadTargetIndex.value = targetIndex
  ;(qrFileInput.value as HTMLInputElement | null)?.click()
}

/** 保存前统一生成协议 JSON，页面上不会出现或要求用户理解这些内部字段。 */
function resolveFooterTemplateForSave(): string | null | undefined {
  if (footerTemplateMode.value === 'none') return null
  if (footerTemplateMode.value === 'custom') return customFooterTemplate.value.trim() || null

  const brand = consultationCard.brand.trim()
  const phone = consultationCard.phone.trim()
  const qrcodes = consultationCard.qrcodes
    .map(item => ({ label: item.label.trim() || '二维码', url: item.url.trim() }))
    .filter(item => item.url)
  if (!brand || !phone || qrcodes.length === 0) {
    ElMessage.warning('产品咨询卡需要填写品牌、咨询电话和至少一个二维码')
    return undefined
  }
  return JSON.stringify({
    type: 'consultation_card_v1',
    brand,
    headline: '产品咨询',
    phone,
    qrcodes,
  })
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
    if (footerTemplateMode.value === 'consultation_card' && qrUploadTargetIndex.value !== null) {
      const target = consultationCard.qrcodes[qrUploadTargetIndex.value]
      if (target) target.url = footerQrUrl.value
    } else {
      const qrMd = footerQrUrl.value ? `![二维码](${footerQrUrl.value})` : ''
      customFooterTemplate.value = qrMd
        ? `${qrMd}\n\n${customFooterTemplate.value.replace(/!\[二维码\]\(.*?\)\n\n/, '')}`
        : customFooterTemplate.value
    }
    ElMessage.success('二维码已上传')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '二维码上传失败')
  } finally {
    uploadingQr.value = false
    input.value = '' as any
  }
}

async function save() {
  const footerTemplate = resolveFooterTemplateForSave()
  if (footerTemplate === undefined) return
  saving.value = true
  try {
    const payload: Record<string, any> = {
      name: form.name,
      content_type: form.content_type,
      topic: form.topic || null,
      feed_source_ids: form.feed_source_ids.length > 0 ? form.feed_source_ids : null,
      feed_source_id: form.feed_source_id || null,
      feed_article_ids: form.feed_article_ids.length > 0 ? form.feed_article_ids : null,
      format_profile_id: form.format_profile_id,
      style: form.style || null,
      knowledge_base_ids: form.knowledge_base_ids.length > 0 ? form.knowledge_base_ids : null,
      day_of_week: form.day_of_week,
      publish_times: form.publish_times,
      articles_per_day: form.articles_per_day,
      html_image_count: form.html_image_count,
      layout_mode: form.layout_mode,
      account_ids: form.account_ids.length > 0 ? form.account_ids : null,
      publish_mode: form.publish_mode,
      // 发布域是任务级配置，Worker 会在每个运行记录中冻结该值，避免重试串域。
      publish_domain: form.publish_domain,
      image_source: form.image_source,
      footer_template: footerTemplate,
      enabled_image_methods: form.enabled_image_methods,
      enable_watermark: form.enable_watermark,
      erp_image_config: form.erp_source_key
        ? {
            source_key: form.erp_source_key,
            commodity_category: form.erp_commodity_category.trim() || undefined,
            repeat_after_days: form.erp_repeat_after_days,
            image_count: form.erp_image_count,
            ...(form.erp_selection_scope.trim()
              ? { selection_scope: form.erp_selection_scope.trim() }
              : {}),
          }
        : null,
    }

    // 轮换关闭且未被用户改动时不提交新字段，旧任务保存仍保持原请求语义；
    // 用户明确关闭一个原本已启用的轮换任务时才发送 null，触发后端版本隔离。
    if (rotationConfigTouched.value || form.template_rotation_config.enabled) {
      payload.template_rotation_config = form.template_rotation_config.enabled
        ? {
            enabled: true,
            profile_ids: [...form.template_rotation_config.profile_ids],
            basis: form.template_rotation_config.basis,
            uses_per_template: form.template_rotation_config.uses_per_template,
          }
        : null
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
            <span class="label">发布域</span>
            <span>{{ task.publish_mode === 'direct' ? (task.publish_domain === 'private' ? '私域群发' : '公域发布') : '草稿不区分域' }}</span>
          </div>
          <div class="info-row">
            <span class="label">文章版式</span>
            <span>{{ task.layout_mode === 'seamless_poster' ? '无缝海报' : '普通文章 / HTML仿写' }}</span>
          </div>
          <div class="info-row">
            <span class="label">写作模板</span>
            <span>{{ getWritingStyleTemplateLabel(task.style) }}</span>
          </div>
          <div v-if="task.writing_mode === 'feed'" class="info-row">
            <span class="label">格式模板</span>
            <span>{{ getFormatProfileLabel(task.format_profile_id) }}</span>
          </div>
          <div class="info-row">
            <span class="label">水印</span>
            <span v-if="task.watermark_config?.enabled && task.watermark_config.type === 'text'">
              固定文字：{{ task.watermark_config.content || '未设置' }}（{{ task.watermark_config.font_size || 24 }}px）
            </span>
            <span v-else-if="task.watermark_config?.enabled">固定 Logo 水印</span>
            <span v-else>{{ task.enable_watermark ? '启用，跟随任务设置' : '关闭' }}</span>
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
            <el-form-item label="写作模板">
              <el-select v-model="form.style" clearable placeholder="自动匹配内容来源" class="full-width">
                <el-option label="自动匹配内容来源" value="" />
                <el-option
                  v-for="option in writingStyleTemplates"
                  :key="option.identifier"
                  :label="option.label"
                  :value="option.identifier"
                />
                <el-option
                  v-if="hasLegacyWritingStyle"
                  :label="`历史任务风格（${form.style}）`"
                  :value="form.style"
                />
              </el-select>
              <span v-if="selectedWritingStyleTemplate" class="form-hint">
                {{ selectedWritingStyleTemplate.description }}
              </span>
              <span v-else-if="hasLegacyWritingStyle" class="form-hint">
                这是旧任务已有的风格配置；不修改时会原样保留。
              </span>
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

        <el-form-item label="文章版式">
          <el-radio-group v-model="form.layout_mode">
            <el-radio value="standard">普通文章 / HTML仿写</el-radio>
            <el-radio value="seamless_poster">无缝海报</el-radio>
          </el-radio-group>
          <span class="form-hint">
            默认使用普通文章版式。只有明确选择“无缝海报”时，任务才会读取海报格式规则；已有“绣蔓仿写”等任务不会被自动切换。
          </span>
        </el-form-item>

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
            <span class="form-hint">投喂源导入链接后会自动分析文章格式；保存任务时系统会按具体文章或来源最新模板自动绑定。选择具体文章可让 AI 同时参考原文风格。</span>
          </div>
        </el-form-item>

        <el-form-item label="格式模板覆盖（可选）">
          <el-select v-model="form.format_profile_id" clearable placeholder="选择来源模板" style="width:100%">
            <el-option
              v-for="profile in formatProfiles"
              :key="profile.id"
              :value="profile.id"
              :label="`${profile.name} v${profile.version}`"
            >
                <span>{{ profile.name }} v{{ profile.version }}</span>
                <span style="float:right;display:flex;gap:8px;color:#909399;font-size:12px">
                  <span>{{ profile.render_mode === 'poster_gallery' ? '无缝海报' : 'HTML 版式' }}</span>
                  <span>来源模板</span>
                </span>
            </el-option>
          </el-select>
          <span class="form-hint">
            系统仍会自动绑定投喂文章的最新格式模板；手动选择后固定使用该版式。留空时保持原有任务的自动或历史行为。
          </span>
        </el-form-item>

        <el-form-item label="来源模板轮换（可选）">
          <div class="template-rotation-config">
            <div class="rotation-toggle-row">
              <el-switch
                v-model="form.template_rotation_config.enabled"
                active-text="启用模板轮换"
                @change="markRotationConfigTouched"
              />
              <span class="form-hint">关闭时保持当前任务的固定模板逻辑</span>
            </div>

            <div v-if="form.template_rotation_config.enabled" class="rotation-settings">
              <el-form-item label="轮换模板顺序" required>
                <el-select
                  v-model="form.template_rotation_config.profile_ids"
                  multiple
                  collapse-tags
                  collapse-tags-tooltip
                  placeholder="选择至少 2 个来源模板"
                  style="width:100%"
                  @change="markRotationConfigTouched"
                >
                  <el-option
                    v-for="profile in formatProfiles"
                    :key="profile.id"
                    :value="profile.id"
                    :label="getRotationProfileLabel(profile.id)"
                  >
                    <span>{{ getRotationProfileLabel(profile.id) }}</span>
                  </el-option>
                </el-select>
                <div v-if="rotationProfileIds.length" class="rotation-order-list">
                  <div
                    v-for="(profileId, index) in rotationProfileIds"
                    :key="profileId"
                    class="rotation-order-item"
                  >
                    <span class="rotation-order-index">{{ index + 1 }}</span>
                    <span class="rotation-order-name">{{ getRotationProfileLabel(profileId) }}</span>
                    <el-button
                      text
                      circle
                      :icon="ArrowUp"
                      :disabled="index === 0"
                      title="上移模板"
                      @click="moveRotationProfile(index, -1)"
                    />
                    <el-button
                      text
                      circle
                      :icon="ArrowDown"
                      :disabled="index === rotationProfileIds.length - 1"
                      title="下移模板"
                      @click="moveRotationProfile(index, 1)"
                    />
                  </div>
                </div>
              </el-form-item>

              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="切换依据">
                    <el-radio-group
                      v-model="form.template_rotation_config.basis"
                      @change="markRotationConfigTouched"
                    >
                      <el-radio value="publish_day">按发布日</el-radio>
                      <el-radio value="publish_run">按发布次数</el-radio>
                    </el-radio-group>
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="每个模板连续使用次数">
                    <el-input-number
                      v-model="form.template_rotation_config.uses_per_template"
                      :min="1"
                      :max="365"
                      style="width:100%"
                      @change="markRotationConfigTouched"
                    />
                  </el-form-item>
                </el-col>
              </el-row>
              <span class="form-hint">
                按发布日时，同一天的多个发布时间共用一个模板；按发布次数时，每次发布按顺序切换。达到连续使用次数后进入下一个模板。
              </span>
            </div>
          </div>
        </el-form-item>

        <el-form-item label="文章底部固定内容">
          <div class="footer-template-editor">
            <el-radio-group v-model="footerTemplateMode" @change="handleFooterTemplateModeChange">
              <el-radio value="none">不添加</el-radio>
              <el-radio value="consultation_card">产品咨询卡</el-radio>
              <el-radio value="custom">自定义内容</el-radio>
            </el-radio-group>

            <div v-if="footerTemplateMode === 'consultation_card'" class="consultation-card-editor">
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-input v-model="consultationCard.brand" placeholder="品牌名称，例如：剪纸系列" />
                </el-col>
                <el-col :span="12">
                  <el-input v-model="consultationCard.phone" placeholder="咨询电话" />
                </el-col>
              </el-row>
              <div class="consultation-qr-list">
                <div v-for="(qr, index) in consultationCard.qrcodes" :key="index" class="consultation-qr-row">
                  <el-input v-model="qr.label" placeholder="二维码名称，例如：企业微信" />
                  <el-input v-model="qr.url" placeholder="二维码图片地址" />
                  <el-button size="small" :loading="uploadingQr && qrUploadTargetIndex === index" @click="openQrUploader(index)">上传</el-button>
                  <el-button v-if="consultationCard.qrcodes.length > 1" size="small" text type="danger" @click="removeConsultationQrCode(index)">删除</el-button>
                </div>
              </div>
              <el-button size="small" text @click="addConsultationQrCode">+ 添加二维码</el-button>
              <span class="form-hint">自动生成“产品咨询”卡片。绣蔓可填企业微信和抖音两个二维码，其他公众号按需要添加。</span>
            </div>

            <div v-else-if="footerTemplateMode === 'custom'" class="custom-footer-editor">
              <div class="footer-upload-row">
                <el-button size="small" :loading="uploadingQr" @click="openQrUploader()">上传二维码</el-button>
                <span v-if="footerQrUrl" class="upload-success">已上传</span>
                <span class="form-hint">上传后会插入到自定义内容开头</span>
              </div>
              <el-input v-model="customFooterTemplate" type="textarea" :rows="3" placeholder="可填写其他固定内容；二维码使用 Markdown 格式" />
            </div>
            <input ref="qrFileInput" type="file" accept="image/*" style="display:none" @change="uploadFooterQr" />
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

        <el-form-item v-if="form.account_ids.length > 0" label="发布域">
          <el-radio-group v-model="form.publish_domain" :disabled="form.publish_mode !== 'direct'">
            <el-radio value="public">公域发布</el-radio>
            <el-radio value="private">私域群发</el-radio>
          </el-radio-group>
          <span class="form-hint" style="display:block;margin-top:4px">
            {{ form.publish_mode === 'direct' ? '公域发布面向所有用户；私域群发面向公众号粉丝。' : '存草稿箱阶段不会触发公域或私域发送。' }}
          </span>
        </el-form-item>

        <el-divider />
        <el-form-item label="水印">
          <div class="watermark-toggle-row">
            <el-switch v-model="form.enable_watermark" :disabled="fixedWatermarkLocked" active-text="添加水印" inactive-text="无水印" />
            <span v-if="fixedWatermarkLocked" class="form-hint">当前任务已锁定固定水印</span>
            <span v-else class="form-hint">在「水印设置」中配置样式</span>
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
          <el-col v-if="form.writing_mode === 'feed' && form.content_type === 'article'" :span="8">
            <el-form-item label="HTML 仿写图片数">
              <el-input-number v-model="form.html_image_count" :min="1" :max="30" style="width:100%" />
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
.template-rotation-config { width: 100%; }
.rotation-toggle-row { display: flex; align-items: center; gap: 12px; min-height: 32px; }
.rotation-settings { margin-top: 14px; padding: 14px; border: 1px solid #ebeef5; border-radius: 6px; background: #fafafa; }
.rotation-settings :deep(.el-form-item) { margin-bottom: 14px; }
.rotation-order-list { display: grid; gap: 6px; margin-top: 8px; }
.rotation-order-item { display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 4px 8px; border: 1px solid #ebeef5; border-radius: 4px; background: #fff; }
.rotation-order-index { width: 20px; color: #909399; font-size: 12px; text-align: center; }
.rotation-order-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #606266; font-size: 13px; }
.footer-template-editor { width: 100%; }
.consultation-card-editor, .custom-footer-editor { margin-top: 12px; padding: 14px; border: 1px solid #e7e1d9; border-radius: 6px; background: #fcfbf8; }
.consultation-qr-list { display: grid; gap: 8px; margin: 12px 0 8px; }
.consultation-qr-row { display: grid; grid-template-columns: minmax(110px, 0.8fr) minmax(180px, 2fr) auto auto; gap: 8px; align-items: center; }
.footer-upload-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.upload-success { color: #529a72; font-size: 13px; }
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
@media (max-width: 700px) {
  .consultation-qr-row { grid-template-columns: 1fr; }
  .consultation-qr-row .el-button { justify-self: start; }
}
</style>
