<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import { getArticle, getExecutionLogs } from '@/api/article'
import type { Article } from '@/api/types'
import { marked } from 'marked'
import { sanitizeHtml } from '@/utils/sanitizer'

const router = useRouter()
const route = useRoute()

const loading = ref(true)
const article = ref<Article | null>(null)
const notFound = ref(false)
const logs = ref<any[]>([])
const logsLoading = ref(false)
const outlineExpanded = ref(false)

const taskId = computed(() => route.params.taskId as string)

function renderMarkdown(text: string | undefined | null): string {
  if (!text) return ''
  const html = marked.parse(text, { async: false }) as string
  return sanitizeHtml(html)
}

async function loadArticle() {
  loading.value = true
  notFound.value = false
  try {
    article.value = await getArticle(taskId.value)
  } catch (err: any) {
    if (err?.response?.status === 404) {
      notFound.value = true
    } else {
      ElMessage.error(err?.response?.data?.message || '加载文章失败')
    }
  } finally {
    loading.value = false
  }
}

async function loadLogs() {
  logsLoading.value = true
  try {
    const data = await getExecutionLogs(taskId.value)
    logs.value = Array.isArray(data) ? data : []
  } catch {
    logs.value = []
  } finally {
    logsLoading.value = false
  }
}

const hasFullContent = computed(() => {
  return !!article.value?.full_content
})

const images = computed(() => {
  return article.value?.images || []
})

const currentImageIndex = ref(0)
const thumbTrackRef = ref<HTMLElement | null>(null)

const galleryImages = computed(() => {
  return images.value.map((img: any) => ({
    url: img.url || img,
    caption: img.section_title || img.alt || '',
  }))
})

function scrollThumbs(dir: number) {
  if (!thumbTrackRef.value) return
  thumbTrackRef.value.scrollBy({ left: dir * 160, behavior: 'smooth' })
}

const outline = computed(() => {
  return article.value?.outline
})

function goBack() {
  router.push('/articles/list')
}

// ── Metrics & Quality ──
import client from '@/api/client'

const metricsList = ref<any[]>([])
const metricsUpdatedAt = ref('')
const syncing = ref(false)
const qualityScore = ref<number | null>(null)
const evaluating = ref(false)
const qualityIssues = ref<any[]>([])
const qualitySuggestions = ref<string[]>([])
const showOptDlg = ref(false)
const optType = ref('structure_optimize')
const optInstruction = ref('')
const optimizing = ref(false)
const scoreType = computed(() => {
  if (qualityScore.value === null) return 'info'
  if (qualityScore.value >= 70) return 'success'
  if (qualityScore.value >= 50) return 'warning'
  return 'danger'
})

async function loadMetrics() {
  if (!article.value?.id) return
  try {
    const [metricsRes, qualityRes] = await Promise.all([
      client.get(`/articles/${article.value.id}/metrics/latest`),
      client.get(`/articles/${article.value.id}/quality/latest`),
    ])
    const m = metricsRes.data
    metricsList.value = [
      { label: '阅读', value: m.read_count ?? 0 },
      { label: '点赞', value: m.like_count ?? 0 },
      { label: '分享', value: m.share_count ?? 0 },
      { label: '评论', value: m.comment_count ?? 0 },
      { label: '收藏', value: m.fav_count ?? 0 },
    ]
    metricsUpdatedAt = m.updated_at || ''
    const q = qualityRes.data
    if (q.status !== 'not_evaluated') {
      qualityScore.value = q.overall_score
      qualityIssues.value = q.issues || []
      qualitySuggestions.value = q.suggestions || []
    }
  } catch (_) { /* ignore */ }
}

async function syncMetrics() {
  if (!article.value?.id) return
  syncing.value = true
  try {
    await client.post(`/articles/${article.value.id}/metrics/sync`)
    await loadMetrics()
  } finally { syncing.value = false }
}

async function triggerEval() {
  if (!article.value?.id) return
  evaluating.value = true
  try {
    await client.post(`/articles/${article.value.id}/quality-evaluations`)
    // Wait a few seconds for async task to complete
    setTimeout(async () => { await loadMetrics() }, 3000)
  } finally { evaluating.value = false }
}

async function createOptimization() {
  if (!article.value?.id) return
  optimizing.value = true
  try {
    await client.post(`/articles/${article.value.id}/optimization-drafts`, {
      optimization_type: optType.value,
      instruction: optInstruction.value,
    })
    showOptDlg.value = false
    ElMessage.success('优化稿生成中，请稍后在审核页面查看')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '创建失败')
  } finally { optimizing.value = false }
}

onMounted(async () => {
  await loadArticle()
  if (article.value) {
    await loadLogs()
    await loadMetrics()
  }
})
</script>

<template>
  <div class="article-detail">
    <!-- Header -->
    <div class="detail-header">
      <el-button text :icon="ArrowLeft" @click="goBack" class="back-btn">
        返回文章列表
      </el-button>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="5" animated />
    </div>

    <!-- Not Found State -->
    <div v-else-if="notFound" class="not-found-state">
      <el-result
        icon="warning"
        title="文章未找到"
        sub-title="该文章不存在或已被删除"
      >
        <template #extra>
          <el-button type="primary" @click="goBack">返回文章列表</el-button>
        </template>
      </el-result>
    </div>

    <!-- Article Content -->
    <template v-else-if="article">
      <!-- Title Section -->
      <el-card shadow="never" class="title-card">
        <h1 class="main-title">{{ article.main_title || '无标题' }}</h1>
        <p v-if="article.sub_title" class="sub-title">{{ article.sub_title }}</p>
        <div class="meta-row">
          <el-tag
            :type="article.status === 'COMPLETED' ? 'success' : article.status === 'FAILED' ? 'danger' : 'warning'"
            size="small"
          >
            {{ article.status }}
          </el-tag>
          <span class="meta-item">主题: {{ article.topic }}</span>
          <span v-if="article.style" class="meta-item">风格: {{ article.style }}</span>
          <span class="meta-item">创建时间: {{ article.created_at }}</span>
        </div>
      </el-card>

      <!-- Cover Image -->
      <el-card v-if="article.cover_image" shadow="never" class="cover-card">
        <template #header>
          <span class="section-title">封面图片</span>
        </template>
        <div class="cover-wrapper">
          <el-image
            :src="article.cover_image"
            fit="contain"
            :preview-src-list="[article.cover_image]"
            preview-teleported
            class="cover-image"
          />
        </div>
      </el-card>

      <!-- Full Content (rendered markdown) -->
      <el-card v-if="hasFullContent" shadow="never" class="content-card">
        <template #header>
          <span class="section-title">文章内容</span>
        </template>
        <div
          class="rendered-content"
          v-html="renderMarkdown(article.full_content)"
        />
      </el-card>

      <!-- Image Gallery (when no full_content) -->
      <el-card v-if="!hasFullContent && images.length > 0" shadow="never" class="gallery-card">
        <template #header>
          <span class="section-title">配图列表 ({{ images.length }})</span>
        </template>
        <div class="image-gallery-wrapper">
          <div class="gallery-main">
            <el-image
              :src="galleryImages[currentImageIndex].url"
              fit="contain"
              :preview-src-list="galleryImages.map(i => i.url)"
              preview-teleported
              class="gallery-main-image"
            />
            <p v-if="galleryImages[currentImageIndex].caption" class="gallery-main-caption">
              {{ galleryImages[currentImageIndex].caption }}
            </p>
          </div>
          <div class="gallery-thumbs">
            <button class="thumb-scroll thumb-prev" @click="scrollThumbs(-1)">‹</button>
            <div class="thumb-track" ref="thumbTrackRef">
              <div
                v-for="(img, idx) in galleryImages"
                :key="idx"
                class="thumb-item"
                :class="{ active: idx === currentImageIndex }"
                @click="currentImageIndex = idx"
              >
                <el-image :src="img.url" fit="cover" />
              </div>
            </div>
            <button class="thumb-scroll thumb-next" @click="scrollThumbs(1)">›</button>
          </div>
        </div>
      </el-card>

      <!-- Outline (collapsible) -->
      <el-card v-if="outline" shadow="never" class="outline-card">
        <div class="outline-header" @click="outlineExpanded = !outlineExpanded">
          <span class="section-title">文章大纲</span>
          <el-button text :icon="outlineExpanded ? ArrowUp : ArrowDown">
            {{ outlineExpanded ? '收起' : '展开' }}
          </el-button>
        </div>
        <el-collapse-transition>
          <div v-show="outlineExpanded" class="outline-body">
            <div
              v-for="section in (outline.sections || [])"
              :key="section.section"
              class="outline-section"
            >
              <div class="section-header">
                <span class="section-number">第{{ section.section }}部分</span>
                <span class="section-title-text">{{ section.title }}</span>
              </div>
              <ul class="section-points">
                <li v-for="(point, i) in (section.points || [])" :key="i">
                  {{ point }}
                </li>
              </ul>
            </div>
            <p v-if="!outline.sections || outline.sections.length === 0" class="empty-hint">
              暂无大纲数据
            </p>
          </div>
        </el-collapse-transition>
      </el-card>

      <!-- Execution Logs -->
      <el-card shadow="never" class="logs-card">
        <template #header>
          <span class="section-title">执行日志</span>
        </template>
        <el-table
          :data="logs"
          v-loading="logsLoading"
          size="small"
          stripe
          empty-text="暂无日志数据"
          style="width: 100%"
        >
          <el-table-column prop="agent_name" label="智能体" min-width="160" />
          <el-table-column prop="status" label="状态" width="110">
            <template #default="{ row }">
              <el-tag
                :type="row.status === 'SUCCESS' ? 'success' : row.status === 'FAILED' ? 'danger' : 'warning'"
                size="small"
              >
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="duration_ms" label="耗时 (ms)" width="110" align="right" />
          <el-table-column prop="error_message" label="错误信息" min-width="200" show-overflow-tooltip />
        </el-table>
      </el-card>

      <!-- Data & Optimization -->
      <el-card v-if="article?.status === 'published'" shadow="never" class="metrics-card" style="margin-top:16px;">
        <template #header>
          <span class="section-title">数据与优化</span>
        </template>
        <el-row :gutter="12" style="margin-bottom:12px;">
          <el-col :span="4" v-for="m in metricsList" :key="m.label">
            <el-statistic :value="m.value" :title="m.label" />
          </el-col>
        </el-row>
        <div style="font-size:12px;color:#999;margin-bottom:12px;">
          更新时间：{{ metricsUpdatedAt || '暂无数据' }}
          <el-button size="small" text @click="syncMetrics" :loading="syncing">同步</el-button>
        </div>
        <el-divider />
        <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px;">
          <span style="font-weight:600;">AI 质量评分：</span>
          <el-tag v-if="qualityScore !== null" :type="scoreType" size="large">{{ qualityScore }}</el-tag>
          <el-tag v-else type="info" size="large">未评估</el-tag>
          <el-button size="small" text @click="triggerEval" :loading="evaluating">
            {{ qualityScore !== null ? '重新评分' : '开始评分' }}
          </el-button>
        </div>
        <div v-if="qualityIssues?.length" style="margin-bottom:12px;">
          <div v-for="(issue, i) in qualityIssues" :key="i" style="font-size:13px;color:#666;margin-bottom:4px;">
            • <el-tag :type="issue.severity==='high'?'danger':'warning'" size="small">{{ issue.type }}</el-tag>
            {{ issue.description }}
          </div>
        </div>
        <div v-if="qualitySuggestions?.length">
          <p style="font-weight:600;font-size:13px;">优化建议：</p>
          <div v-for="(s, i) in qualitySuggestions" :key="i" style="font-size:13px;color:#409eff;margin-bottom:4px;">• {{ s }}</div>
        </div>
        <el-button type="primary" size="small" style="margin-top:8px;" @click="showOptDlg=true">创建优化稿</el-button>
      </el-card>

      <el-dialog v-model="showOptDlg" title="创建优化稿" width="500px">
        <el-form label-position="top">
          <el-form-item label="优化类型">
            <el-select v-model="optType" style="width:100%;">
              <el-option label="标题优化" value="title_optimize" />
              <el-option label="开头优化" value="opening_optimize" />
              <el-option label="结构优化" value="structure_optimize" />
              <el-option label="可读性优化" value="readability_optimize" />
              <el-option label="内容扩充" value="content_expand" />
              <el-option label="内容精简" value="content_condense" />
              <el-option label="用户价值强化" value="value_enhance" />
              <el-option label="事实修正" value="fact_correct" />
              <el-option label="全文重写" value="full_rewrite" />
              <el-option label="风格转换" value="style_transform" />
            </el-select>
          </el-form-item>
          <el-form-item label="额外指令（可选）">
            <el-input v-model="optInstruction" type="textarea" placeholder="保持专业风格/增加数据案例" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="showOptDlg=false">取消</el-button>
          <el-button type="primary" @click="createOptimization" :loading="optimizing">生成</el-button>
        </template>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.article-detail {
  max-width: 920px;
  margin: 0 auto;
  padding: 20px;
}

/* Header */
.detail-header {
  margin-bottom: 16px;
}

.back-btn {
  font-size: 14px;
  color: #606266;
}

/* Loading & Not Found */
.loading-state {
  padding: 40px 20px;
}

.not-found-state {
  padding: 60px 20px;
}

/* Title Card */
.title-card {
  margin-bottom: 16px;
  border-radius: 8px;
}

.main-title {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
  margin: 0 0 8px;
  line-height: 1.4;
}

.sub-title {
  font-size: 16px;
  color: #606266;
  margin: 0 0 16px;
  line-height: 1.5;
}

.meta-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.meta-item {
  font-size: 13px;
  color: #909399;
}

/* Cover */
.cover-card {
  margin-bottom: 16px;
  border-radius: 8px;
}

.cover-wrapper {
  display: flex;
  justify-content: center;
}

.cover-image {
  max-width: 100%;
  max-height: 420px;
  border-radius: 6px;
}

/* Content */
.content-card {
  margin-bottom: 16px;
  border-radius: 8px;
}

.rendered-content {
  line-height: 1.8;
  font-size: 15px;
  color: #303133;
}

.rendered-content :deep(h1),
.rendered-content :deep(h2),
.rendered-content :deep(h3) {
  margin: 24px 0 12px;
  color: #303133;
  font-weight: 600;
}

.rendered-content :deep(h2) {
  font-size: 22px;
  border-bottom: 1px solid #e4e7ed;
  padding-bottom: 8px;
}

.rendered-content :deep(h3) {
  font-size: 18px;
}

.rendered-content :deep(p) {
  margin-bottom: 16px;
}

.rendered-content :deep(img) {
  max-width: 100%;
  border-radius: 6px;
  margin: 16px 0;
  display: block;
}

.rendered-content :deep(blockquote) {
  border-left: 4px solid #409eff;
  padding: 8px 16px;
  margin: 16px 0;
  background: #f5f7fa;
  color: #606266;
  border-radius: 0 4px 4px 0;
}

.rendered-content :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 13px;
  color: #e6a23c;
}

.rendered-content :deep(pre) {
  background: #f5f7fa;
  padding: 16px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 16px 0;
}

.rendered-content :deep(pre code) {
  background: none;
  padding: 0;
  color: #303133;
}

.rendered-content :deep(ul),
.rendered-content :deep(ol) {
  padding-left: 24px;
  margin-bottom: 16px;
}

.rendered-content :deep(li) {
  margin-bottom: 4px;
}

.rendered-content :deep(a) {
  color: #409eff;
  text-decoration: none;
}

.rendered-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
}

.rendered-content :deep(th),
.rendered-content :deep(td) {
  border: 1px solid #e4e7ed;
  padding: 8px 12px;
  text-align: left;
}

.rendered-content :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}

/* Image Gallery */
.gallery-card {
  margin-bottom: 16px;
  border-radius: 8px;
}

.image-gallery-wrapper {
  width: 100%;
}

.gallery-main {
  width: 100%;
  background: #f0f0f0;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}

.gallery-main-image {
  max-width: 100%;
  max-height: 65vh;
  width: auto;
  height: auto;
  display: block;
}

.gallery-main-caption {
  font-size: 13px;
  color: #606266;
  margin: 8px 0;
  padding: 0 16px;
  text-align: center;
  line-height: 1.5;
}

.gallery-thumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 8px 0;
}

.thumb-track {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  scroll-behavior: smooth;
  flex: 1;
  padding: 4px 0;
}

.thumb-track::-webkit-scrollbar {
  height: 4px;
}
.thumb-track::-webkit-scrollbar-thumb {
  background: #ccc;
  border-radius: 2px;
}

.thumb-item {
  flex: 0 0 80px;
  height: 60px;
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  border: 2px solid transparent;
  transition: border-color 0.2s;
  opacity: 0.6;
}

.thumb-item.active {
  border-color: #07c160;
  opacity: 1;
}

.thumb-item .el-image {
  width: 100%;
  height: 100%;
  display: block;
}

.thumb-scroll {
  flex: 0 0 32px;
  height: 32px;
  border-radius: 50%;
  border: 1px solid #ddd;
  background: #fff;
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #666;
  transition: all 0.2s;
}

.thumb-scroll:hover {
  background: #f5f5f5;
  border-color: #bbb;
}

/* Outline */
.outline-card {
  margin-bottom: 16px;
  border-radius: 8px;
}

.outline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.outline-body {
  padding-top: 8px;
}

.outline-section {
  margin-bottom: 16px;
  padding: 14px;
  background: #f5f7fa;
  border-radius: 6px;
}

.section-number {
  font-size: 12px;
  color: #909399;
  background: #e4e7ed;
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
}

.section-title-text {
  font-size: 15px;
  font-weight: 500;
  color: #303133;
}

.section-points {
  margin: 10px 0 0;
  padding-left: 20px;
}

.section-points li {
  margin-bottom: 4px;
  color: #606266;
  line-height: 1.6;
  font-size: 14px;
}

.empty-hint {
  color: #909399;
  text-align: center;
  padding: 20px;
  margin: 0;
}

/* Logs */
.logs-card {
  margin-bottom: 16px;
  border-radius: 8px;
}

/* Reused Section Title */
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
</style>
