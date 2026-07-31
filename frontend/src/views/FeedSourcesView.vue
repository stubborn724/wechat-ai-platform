<script setup lang="ts">
import { onMounted, reactive, ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { FeedSource, FeedSourceArticle } from '@/api/types'
import { sanitizeHtml } from '@/utils/sanitizer'

const loading = ref(false)
const sources = ref<FeedSource[]>([])
const showForm = ref(false)
const saving = ref(false)
const form = reactive({
  name: '',
  slug: '',
  source_type: 'url',
  source_identifier: '',
  feed_url: '',
})

const selectedSource = ref<FeedSource | null>(null)
const showArticles = ref(false)
const articles = ref<FeedSourceArticle[]>([])
const articlesLoading = ref(false)

// Manual article creation
const showManualForm = ref(false)
const manualSaving = ref(false)
const manualForm = reactive({
  title: '',
  body_markdown: '',
  summary: '',
  cover_image_url: '',
})

// Fetch result
const fetchResult = ref<any>(null)
const showFetchResult = ref(false)

// Article preview
const previewArticle = ref<FeedSourceArticle | null>(null)
const showPreview = ref(false)
const currentImageIndex = ref(0)

// Extract image URLs from markdown for gallery view
const imageUrls = computed(() => {
  const sourceHtml = previewArticle.value?.body_html || ''
  if (sourceHtml) {
    return Array.from(sourceHtml.matchAll(/<img[^>]+src=["']([^"']+)["']/gi), match => match[1])
  }
  const md = previewArticle.value?.body_markdown || ''
  const urls: string[] = []
  const regex = /!\[.*?\]\((.*?)\)/g
  let m
  while ((m = regex.exec(md)) !== null) {
    urls.push(m[1])
  }
  return urls
})
const isImageOnly = computed(() => {
  if (!previewArticle.value?.body_markdown) return false
  const lines = previewArticle.value.body_markdown.trim().split('\n\n')
  return lines.length > 0 && lines.every(l => /^!\[.*?\]\(.*?\)$/.test(l.trim()))
})

const thumbTrackRef = ref<HTMLElement | null>(null)
function scrollThumbs(dir: number) {
  if (!thumbTrackRef.value) return
  thumbTrackRef.value.scrollBy({ left: dir * 160, behavior: 'smooth' })
}

const sourceTypeLabels: Record<string, string> = {
  official_account: '公众号',
  url: 'URL',
  manual: '手动',
}

const sourceTypeOptions = [
  { value: 'url', label: 'URL 文章' },
  { value: 'official_account', label: '公众号' },
  { value: 'manual', label: '手动输入' },
]

async function load() {
  loading.value = true
  try {
    const res = await client.get<{ total: number; items: FeedSource[] }>('/feed-sources')
    sources.value = res.data.items || []
  } catch {
    ElMessage.error('加载投喂源失败')
  } finally {
    loading.value = false
  }
}

function openForm() {
  form.name = ''
  form.slug = ''
  form.source_type = 'url'
  form.source_identifier = ''
  form.feed_url = ''
  showForm.value = true
}

async function create() {
  saving.value = true
  try {
    await client.post('/feed-sources', form)
    ElMessage.success('投喂源创建成功')
    showForm.value = false
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
  }
}

async function confirmDelete(source: FeedSource) {
  try {
    await ElMessageBox.confirm(`确定删除投喂源「${source.name}」吗？`, '确认删除')
    await client.delete(`/feed-sources/${source.id}`)
    ElMessage.success('已删除')
    await load()
  } catch {
    // cancelled
  }
}

async function triggerFetch(source: FeedSource) {
  try {
    const res = await client.post(`/feed-sources/${source.id}/fetch`)
    fetchResult.value = res.data
    showFetchResult.value = true
    ElMessage.success('抓取成功')
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '抓取失败')
  }
}

async function triggerAnalyze(source: FeedSource) {
  try {
    const res = await client.post(`/feed-sources/${source.id}/analyze`)
    const profile = res.data.profile || {}
    ElMessage.success(`分析完成，共 ${Object.keys(profile).length} 个特征维度`)
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '分析失败')
  }
}

async function viewArticles(source: FeedSource) {
  selectedSource.value = source
  showArticles.value = true
  articlesLoading.value = true
  articles.value = []
  try {
    const res = await client.get(`/feed-sources/${source.id}/articles`, {
      params: { page: 1, page_size: 50 }
    })
    articles.value = res.data.items || res.data || []
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '加载文章失败')
  } finally {
    articlesLoading.value = false
  }
}

function openManualForm() {
  manualForm.title = ''
  manualForm.body_markdown = ''
  manualForm.summary = ''
  manualForm.cover_image_url = ''
  showManualForm.value = true
}

async function saveManualArticle() {
  if (!selectedSource.value || !manualForm.title.trim()) {
    ElMessage.warning('请输入文章标题')
    return
  }
  manualSaving.value = true
  try {
    await client.post(`/feed-sources/${selectedSource.value.id}/articles`, {
      title: manualForm.title,
      body_markdown: manualForm.body_markdown,
      summary: manualForm.summary || undefined,
      cover_image_url: manualForm.cover_image_url || undefined,
    })
    ElMessage.success('文章已添加')
    showManualForm.value = false
    const res = await client.get(`/feed-sources/${selectedSource.value.id}/articles`, {
      params: { page: 1, page_size: 50 }
    })
    articles.value = res.data.items || res.data || []
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '添加失败')
  } finally {
    manualSaving.value = false
  }
}

function renderMarkdown(md: string): string {
  if (!md) return ''
  let html = md
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
  // Convert markdown images first
  html = html.replace(
    /!\[(.*?)\]\((.*?)\)/g,
    '<figure class="preview-figure" style="margin:12px auto;text-align:center;background:#f8f8f8;padding:8px;border-radius:8px;max-width:640px;overflow:hidden;"><img src="$2" alt="$1" loading="lazy" referrerpolicy="no-referrer" style="max-width:640px;width:100%;height:auto;border-radius:4px;display:block;margin:0 auto;"/><figcaption style="margin-top:6px;font-size:12px;color:#909399;">$1</figcaption></figure>'
  )
  // Headers
  html = html
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
  // Links (but not inside already-generated HTML tags)
  html = html.replace(
    /\[([^\[\]]+?)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>'
  )
  // Split into blocks by double newlines
  const blocks = html.split(/\n\n+/).map(block => {
    const t = block.trim()
    if (!t) return ''
    if (t.startsWith('<h') || t.startsWith('<figure')) return t
    if (t.startsWith('- ')) {
      const items = t.split('\n').map(l => `<li>${l.replace(/^- /, '')}</li>`).join('')
      return `<ul>${items}</ul>`
    }
    return `<p>${t.replace(/\n/g, '<br/>')}</p>`
  }).join('\n')
  return sanitizeHtml(blocks)
}

function renderFeedArticle(article: FeedSourceArticle): string {
  // 抓取源保存了原始 HTML 时优先展示它，便于用户判断后续仿写会继承的真实版式。
  if (article.body_html?.trim()) return sanitizeHtml(article.body_html)
  return renderMarkdown(article.body_markdown || '')
}

onMounted(load)
</script>

<template>
  <div class="feed-sources-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">FEED SOURCES</p>
        <h1>投喂源</h1>
        <p class="lead">管理外部文章来源，AI 可参考风格和内容进行仿写。</p>
      </div>
      <el-button type="primary" @click="openForm">+ 添加投喂源</el-button>
    </div>

    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="sources.length === 0" class="empty-state">
      <el-empty description="暂无投喂源">
        <el-button type="primary" @click="openForm">添加第一个投喂源</el-button>
      </el-empty>
    </div>

    <div v-else class="source-grid">
      <div v-for="src in sources" :key="src.id" class="source-card">
        <div class="card-header">
          <div class="card-title">{{ src.name }}</div>
          <el-tag :type="src.is_active ? 'success' : 'info'" size="small">
            {{ src.is_active ? '启用' : '停用' }}
          </el-tag>
        </div>
        <div class="card-body">
          <div class="info-row">
            <span class="label">类型</span>
            <span>{{ sourceTypeLabels[src.source_type] || src.source_type }}</span>
          </div>
          <div class="info-row">
            <span class="label">标识</span>
            <span class="identifier">{{ src.source_identifier || '-' }}</span>
          </div>
          <div class="info-row">
            <span class="label">文章数</span>
            <span>{{ src.article_count || 0 }}</span>
          </div>
          <div class="info-row">
            <span class="label">上次抓取</span>
            <span>{{ src.last_fetched_at ? new Date(src.last_fetched_at).toLocaleString() : '未抓取' }}</span>
          </div>
          <div v-if="src.style_profile && Object.keys(src.style_profile).length > 0" class="info-row">
            <span class="label">风格</span>
            <el-tag size="small" type="info">{{ (src.style_profile as any).tone }}</el-tag>
          </div>
        </div>
        <div class="card-actions">
          <el-button size="small" @click="viewArticles(src)">文章</el-button>
          <el-button size="small" type="warning" plain @click="triggerFetch(src)">抓取</el-button>
          <el-button size="small" type="info" plain @click="triggerAnalyze(src)">分析</el-button>
          <el-button size="small" type="danger" plain @click="confirmDelete(src)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- Create Dialog -->
    <el-dialog v-model="showForm" title="添加投喂源" width="480px">
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如：科技媒体 XX" />
        </el-form-item>
        <el-form-item label="标识(slug)" required>
          <el-input v-model="form.slug" placeholder="例如：tech-xx" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="form.source_type" style="width: 100%">
            <el-option v-for="opt in sourceTypeOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="源标识(URL或公众号biz)" required>
          <el-input v-model="form.source_identifier" type="textarea" :rows="2" placeholder="文章URL 或 公众号biz" />
        </el-form-item>
        <el-form-item label="Feed URL(可选)">
          <el-input v-model="form.feed_url" placeholder="RSS链接（如有）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- Articles Dialog -->
    <el-dialog v-model="showArticles" title="文章列表" width="700px">
      <div v-if="selectedSource" class="articles-toolbar">
        <span>来源：{{ selectedSource.name }}</span>
        <el-button size="small" type="primary" plain @click="openManualForm">+ 手动添加文章</el-button>
      </div>
      <div v-if="articlesLoading" class="loading-section">
        <el-skeleton :rows="3" animated />
      </div>
      <div v-else-if="articles.length === 0" class="empty-state" style="padding: 24px 0">
        <el-empty description="暂无文章" />
      </div>
      <el-table v-else :data="articles" stripe style="width: 100%">
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="word_count" label="字数" width="80" />
        <el-table-column label="已分析" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_analyzed ? 'success' : 'info'" size="small">
              {{ row.is_analyzed ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="previewArticle = row; showPreview = true">
              预览
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- Manual Article Dialog -->
    <el-dialog v-model="showManualForm" title="手动添加文章" width="650px">
      <el-form label-position="top">
        <el-form-item label="文章标题" required>
          <el-input v-model="manualForm.title" placeholder="输入文章标题" />
        </el-form-item>
        <el-form-item label="文章内容（Markdown）">
          <el-input v-model="manualForm.body_markdown" type="textarea" :rows="10" placeholder="粘贴文章内容（Markdown格式）" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="manualForm.summary" type="textarea" :rows="2" placeholder="文章一句话摘要（可选）" />
        </el-form-item>
        <el-form-item label="封面图URL">
          <el-input v-model="manualForm.cover_image_url" placeholder="https://...（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showManualForm = false">取消</el-button>
        <el-button type="primary" :loading="manualSaving" @click="saveManualArticle">保存</el-button>
      </template>
    </el-dialog>

    <!-- Preview Dialog -->
    <el-dialog v-model="showPreview" title="文章预览" width="720px" top="3vh" @opened="currentImageIndex = 0">
      <div v-if="previewArticle" class="preview-content">
        <h1 class="preview-title">{{ previewArticle.title }}</h1>
        <div v-if="previewArticle.summary" class="preview-summary">{{ previewArticle.summary }}</div>
        <div v-if="previewArticle.word_count && !isImageOnly" class="preview-meta">{{ previewArticle.word_count }} 字</div>

        <!-- Gallery view for image-only articles -->
        <div v-if="isImageOnly && imageUrls.length" class="image-gallery">
          <div class="gallery-main">
            <img :src="imageUrls[currentImageIndex]" alt="preview" referrerpolicy="no-referrer" />
          </div>
          <div class="gallery-thumbs">
            <button class="thumb-scroll thumb-prev" @click="scrollThumbs(-1)">‹</button>
            <div class="thumb-track" ref="thumbTrackRef">
              <div
                v-for="(url, idx) in imageUrls"
                :key="idx"
                class="thumb-item"
                :class="{ active: idx === currentImageIndex }"
                @click="currentImageIndex = idx"
              >
                <img :src="url" alt="" referrerpolicy="no-referrer" />
              </div>
            </div>
            <button class="thumb-scroll thumb-next" @click="scrollThumbs(1)">›</button>
          </div>
        </div>

        <!-- Normal markdown render for text articles -->
        <div v-else class="rendered-content" v-html="renderFeedArticle(previewArticle)"></div>
      </div>
    </el-dialog>

    <!-- Fetch Result Dialog -->
    <el-dialog v-model="showFetchResult" title="抓取结果" width="500px">
      <div v-if="fetchResult" class="fetch-result">
        <div style="margin-bottom: 12px;">
          <el-tag type="success" style="font-size: 14px; padding: 4px 12px;">
            成功抓取 {{ fetchResult.articles_fetched || 0 }} 篇文章（新增 {{ fetchResult.articles_saved || 0 }} 篇）
          </el-tag>
        </div>
        <div v-if="fetchResult.errors && fetchResult.errors.length" class="error-section">
          <p style="color: #e6a23c; font-weight: 600;">⚠ 警告（{{ fetchResult.errors.length }}）</p>
          <p v-for="(err, i) in fetchResult.errors" :key="i" style="color: #e6a23c; font-size: 13px;">{{ err }}</p>
        </div>
        <p v-if="!fetchResult.errors || fetchResult.errors.length === 0" style="color: #67c23a;">✅ 抓取完成，无错误</p>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.feed-sources-page {
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

.source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.source-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  background: #fff;
  transition: box-shadow 0.2s;
}

.source-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
}

.card-body {
  margin-bottom: 12px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;
}

.info-row .label {
  color: #909399;
}

.identifier {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
}

.articles-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-size: 14px;
  color: #606266;
}

.preview-content {
  padding: 4px 0;
}

.preview-title {
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 12px;
}

.preview-summary {
  color: #606266;
  font-size: 14px;
  padding: 10px 14px;
  background: #f5f7fa;
  border-radius: 6px;
  margin-bottom: 12px;
  border-left: 3px solid #409eff;
}

.preview-meta {
  font-size: 12px;
  color: #909399;
  margin-bottom: 16px;
}

.fetch-result {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.result-row {
  display: flex;
  gap: 12px;
  font-size: 14px;
}

.result-label {
  color: #909399;
  min-width: 60px;
}

.rendered-content {
  font-size: 15px;
  line-height: 1.8;
  color: #333;
  padding: 16px;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
}

.rendered-content h1,
.rendered-content h2,
.rendered-content h3 {
  margin: 18px 0 10px;
  font-weight: 600;
}

.rendered-content p {
  margin: 10px 0;
}

.rendered-content ul {
  margin: 8px 0;
  padding-left: 24px;
}

.rendered-content li {
  margin: 4px 0;
}

.rendered-content a {
  color: #409eff;
  text-decoration: none;
}

.rendered-content strong {
  font-weight: 600;
}

.preview-figure {
  margin: 12px auto;
  text-align: center;
  background: #f8f8f8;
  padding: 8px;
  border-radius: 8px;
  max-width: 100%;
  overflow: hidden;
}

.preview-figure img {
  max-width: 640px;
  width: 100%;
  height: auto;
  border-radius: 4px;
  display: block;
  margin: 0 auto;
}

.preview-figure figcaption {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

/* Gallery view for image-only articles */
.image-gallery {
  width: 100%;
}

.gallery-main {
  width: 100%;
  background: #f0f0f0;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}

.gallery-main img {
  max-width: 100%;
  max-height: 65vh;
  width: auto;
  height: auto;
  display: block;
  object-fit: contain;
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

.thumb-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
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
</style>
