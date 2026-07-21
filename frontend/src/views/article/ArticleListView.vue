<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listArticles, deleteArticle } from '@/api/article'
import type { Article } from '@/api/types'

const router = useRouter()

// Search & filter
const keyword = ref('')
const statusFilter = ref('')
const loading = ref(false)

// Pagination
const articles = ref<Article[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)

const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'COMPLETED', label: '已完成' },
  { value: 'FAILED', label: '失败' },
  { value: 'PROCESSING', label: '处理中' },
  { value: 'PENDING', label: '待处理' },
]

const statusTagTypeMap: Record<string, string> = {
  COMPLETED: 'success',
  FAILED: 'danger',
  PROCESSING: 'warning',
  PENDING: 'info',
}

function getStatusTagType(status: string): string {
  return statusTagTypeMap[status] || 'info'
}

const phaseLabels: Record<string, string> = {
  INPUT: '输入主题',
  TITLE_GENERATING: '生成标题中',
  TITLE_SELECTING: '选择标题',
  OUTLINE_GENERATING: '生成大纲中',
  OUTLINE_EDITING: '编辑大纲',
  CONTENT_GENERATING: '生成正文中',
  MERGE_COMPLETE: '合成完成',
  ALL_COMPLETE: '全部完成',
  COMPLETED: '已完成',
  FAILED: '失败',
}

function getPhaseLabel(phase: string | undefined): string {
  if (!phase) return '-'
  return phaseLabels[phase] || phase
}

async function fetchArticles() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (statusFilter.value) {
      params.status = statusFilter.value
    }
    if (keyword.value.trim()) {
      params.keyword = keyword.value.trim()
    }

    const res = await listArticles(params)
    // Handle both nested and flat response shapes
    const data = res.data || res
    articles.value = data.items || []
    total.value = data.total || 0
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.message || '加载文章列表失败')
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  page.value = 1
  fetchArticles()
}

function handlePageChange(newPage: number) {
  page.value = newPage
  fetchArticles()
}

function handleSizeChange(newSize: number) {
  pageSize.value = newSize
  page.value = 1
  fetchArticles()
}

async function handleDelete(row: Article) {
  try {
    await ElMessageBox.confirm('确定要删除该文章吗？此操作不可恢复。', '删除确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deleteArticle(row.id)
    ElMessage.success('文章已删除')
    fetchArticles()
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err?.response?.data?.message || '删除失败')
    }
  }
}

function handleView(row: Article) {
  router.push(`/articles/${row.task_id}`)
}

function handleRetry(row: Article) {
  router.push(`/articles?retry=${row.task_id}`)
}

onMounted(() => {
  fetchArticles()
})
</script>

<template>
  <div class="article-list">
    <!-- Search & Filter Bar -->
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="keyword"
          placeholder="搜索文章主题..."
          clearable
          class="search-input"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <el-select
          v-model="statusFilter"
          placeholder="状态筛选"
          clearable
          class="status-select"
          @change="handleSearch"
        >
          <el-option
            v-for="opt in statusOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-button type="primary" @click="handleSearch">搜索</el-button>
        <el-button type="success" @click="router.push('/articles')">新建文章</el-button>
      </div>
    </el-card>

    <!-- Article Table -->
    <el-card shadow="never" class="table-card">
      <el-table
        :data="articles"
        v-loading="loading"
        stripe
        style="width: 100%"
        empty-text="暂无文章数据"
      >
        <el-table-column prop="topic" label="文章主题" min-width="220" show-overflow-tooltip />

        <el-table-column label="风格" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.style" size="small" effect="plain">
              {{ row.style }}
            </el-tag>
            <span v-else class="no-style">默认</span>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="getStatusTagType(row.status) as any" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="阶段" width="140">
          <template #default="{ row }">
            <el-tag type="info" size="small" effect="plain">
              {{ getPhaseLabel(row.phase) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="created_at" label="创建时间" width="180" />

        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleView(row)">
              查看
            </el-button>
            <el-button
              v-if="row.status === 'FAILED'"
              type="warning"
              link
              size="small"
              @click="handleRetry(row)"
            >
              重试
            </el-button>
            <el-button type="danger" link size="small" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- Pagination -->
      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.article-list {
  padding: 20px;
}

.filter-card {
  margin-bottom: 16px;
  border-radius: 8px;
}

.filter-row {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.search-input {
  width: 300px;
}

.status-select {
  width: 160px;
}

.table-card {
  border-radius: 8px;
}

.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 20px;
}

.no-style {
  color: #909399;
  font-size: 12px;
}
</style>
