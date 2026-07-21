<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import { getArticle, getExecutionLogs } from '@/api/article'
import type { Article } from '@/api/types'
import { marked } from 'marked'

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
  return marked.parse(text, { async: false }) as string
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

const outline = computed(() => {
  return article.value?.outline
})

function goBack() {
  router.push('/articles/list')
}

onMounted(async () => {
  await loadArticle()
  if (article.value) {
    await loadLogs()
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
        <div class="image-gallery">
          <div v-for="(img, index) in images" :key="index" class="gallery-item">
            <el-image
              :src="img.url || img"
              fit="cover"
              :preview-src-list="[img.url || img]"
              preview-teleported
              class="gallery-image"
            />
            <p v-if="img.section_title || img.alt" class="gallery-caption">
              {{ img.section_title || img.alt }}
            </p>
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

.image-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px;
}

.gallery-item {
  text-align: center;
}

.gallery-image {
  width: 100%;
  height: 140px;
  border-radius: 6px;
  object-fit: cover;
}

.gallery-caption {
  font-size: 12px;
  color: #909399;
  margin-top: 6px;
  line-height: 1.4;
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
