<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import client from '@/api/client'
import type { KnowledgeBase, KbDocument } from '@/api/types'

const kbs = ref<KnowledgeBase[]>([])
const loading = ref(true)
const showForm = ref(false)
const selectedKb = ref<KnowledgeBase | null>(null)
const documents = ref<KbDocument[]>([])
const loadingDocs = ref(false)
const showUpload = ref(false)
const uploadFile = ref<File | null>(null)
const uploading = ref(false)

const searchQuery = ref('')
const searchResults = ref<any[]>([])
const searching = ref(false)

const form = reactive({
  name: '',
  kb_type: 'article',
  description: '',
})

const kbTypeNames: Record<string, string> = {
  article: '通用知识库',
  brand: '品牌知识库',
  customer: '客服知识库',
  faq: 'FAQ知识库',
}

async function load() {
  loading.value = true
  try {
    const res = await client.get<{ total: number; items: KnowledgeBase[] }>('/knowledge-bases')
    kbs.value = res.data.items || res.data || []
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

function openForm() {
  Object.assign(form, { name: '', kb_type: 'article', description: '' })
  showForm.value = true
}

async function create() {
  if (!form.name) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  try {
    await client.post('/knowledge-bases', form)
    showForm.value = false
    ElMessage.success('知识库已创建')
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '创建失败')
  }
}

async function selectKb(kb: KnowledgeBase) {
  selectedKb.value = kb
  loadingDocs.value = true
  documents.value = []
  try {
    const res = await client.get<{ total: number; items: KbDocument[] }>(`/knowledge-bases/${kb.id}/documents`)
    documents.value = res.data.items || res.data || []
  } catch {
    ElMessage.error('加载文档失败')
  } finally {
    loadingDocs.value = false
  }
}

async function deleteKb(kb: KnowledgeBase) {
  try {
    await ElMessageBox.confirm(`确定删除「${kb.name}」？文档将被一并标记删除。`, '确认删除')
    await client.delete(`/knowledge-bases/${kb.id}`)
    ElMessage.success('知识库已删除')
    if (selectedKb.value?.id === kb.id) {
      selectedKb.value = null
      documents.value = []
    }
    await load()
  } catch {
    // cancelled
  }
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    uploadFile.value = input.files[0]
  }
}

async function uploadDocument() {
  if (!uploadFile.value) {
    ElMessage.warning('请选择要上传的文件')
    return
  }
  if (!selectedKb.value) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', uploadFile.value)

    await client.post(`/knowledge-bases/${selectedKb.value.id}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    showUpload.value = false
    uploadFile.value = null
    ElMessage.success('文档上传成功，正在解析中...')
    await selectKb(selectedKb.value)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function deleteDocument(doc: KbDocument) {
  if (!selectedKb.value) return
  try {
    await ElMessageBox.confirm(`确定删除文档「${doc.filename}」？`, '确认删除')
    await client.delete(`/knowledge-bases/${selectedKb.value.id}/documents/${doc.id}`)
    ElMessage.success('文档已删除')
    await selectKb(selectedKb.value)
  } catch {
    // cancelled
  }
}

async function search() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  searchResults.value = []
  try {
    const res = await client.get<{ results: any[] }>('/knowledge-bases/search/all', {
      params: { q: searchQuery.value, top_k: 5 },
    })
    searchResults.value = res.data.results || []
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '搜索失败')
  } finally {
    searching.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="kb-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">KNOWLEDGE BASE</p>
        <h1>知识库</h1>
        <p class="lead">上传文档构建知识库，AI 生成文章时会自动检索引用相关内容。</p>
      </div>
      <el-button type="primary" @click="openForm">+ 新建知识库</el-button>
    </div>

    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="kbs.length === 0" class="empty-state">
      <el-empty description="还没有知识库">
        <el-button type="primary" @click="openForm">创建第一个知识库</el-button>
      </el-empty>
    </div>

    <div v-else class="kb-grid">
      <div
        v-for="kb in kbs"
        :key="kb.id"
        class="kb-card"
        :class="{ active: selectedKb?.id === kb.id }"
        @click="selectKb(kb)"
      >
        <div class="kb-type-badge">{{ kb.kb_type === 'brand' ? '品' : kb.kb_type === 'customer' ? '客' : '通' }}</div>
        <h3>{{ kb.name }}</h3>
        <p>{{ kb.description || kb.slug }}</p>
        <div class="kb-card-footer">
          <el-tag size="small" type="info">{{ kbTypeNames[kb.kb_type || ''] || kb.kb_type }}</el-tag>
          <el-button size="small" text type="danger" @click.stop="deleteKb(kb)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- KB Detail -->
    <div v-if="selectedKb" class="kb-detail">
      <div class="kb-detail-header">
        <h2>{{ selectedKb.name }} - 文档列表</h2>
        <el-button size="small" type="primary" @click="showUpload = true">+ 上传文档</el-button>
      </div>

      <div v-if="loadingDocs" class="loading-section">
        <el-skeleton :rows="3" animated />
      </div>

      <div v-else-if="documents.length === 0" class="empty-state" style="padding: 24px 0">
        <el-empty description="还没有文档，点击上方按钮上传" />
      </div>

      <el-table v-else :data="documents" stripe style="width: 100%">
        <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small">{{ (row.file_type || 'unknown').toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag
              :type="row.status === 'ready' ? 'success' : row.status === 'error' ? 'danger' : row.status === 'duplicate' ? 'warning' : 'info'"
              size="small"
            >
              {{ row.status === 'ready' ? '就绪' : row.status === 'processing' ? '处理中' : row.status === 'error' ? '失败' : row.status === 'duplicate' ? '重复' : row.status }}
            </el-tag>
            <el-tooltip v-if="row.error_message" :content="row.error_message">
              <span style="margin-left: 4px; cursor: help; color: #e6a23c;">⚠</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="切片数" width="80" align="center" />
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">
            {{ new Date(row.created_at).toLocaleString('zh-CN') }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="deleteDocument(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Search -->
    <div class="kb-search">
      <h2>知识库检索测试</h2>
      <p class="search-hint">搜索所有知识库，验证向量检索效果</p>
      <div class="search-bar">
        <el-input v-model="searchQuery" placeholder="输入搜索关键词..." @keyup.enter="search" />
        <el-button type="primary" :loading="searching" @click="search" :disabled="!searchQuery.trim()">搜索</el-button>
      </div>
      <div v-if="searching" class="loading-section">
        <el-skeleton :rows="2" animated />
      </div>
      <div v-else-if="searchResults.length" class="search-results">
        <div v-for="r in searchResults" :key="r.id" class="search-hit">
          <div class="hit-meta">
            <span>相似度: {{ (r.score * 100).toFixed(1) }}%</span>
            <span>切片 #{{ r.chunk_index }}</span>
            <span v-if="r.kb_type">类型: {{ kbTypeNames[r.kb_type] || r.kb_type }}</span>
          </div>
          <p>{{ r.content }}</p>
        </div>
      </div>
      <div v-else-if="searchResults.length === 0 && searchQuery && !searching" class="empty-state" style="padding: 20px">
        <el-empty description="未找到相关内容" />
      </div>
    </div>

    <!-- Create Dialog -->
    <el-dialog v-model="showForm" title="新建知识库" width="460px">
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：公司品牌知识库" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.kb_type" style="width: 100%">
            <el-option value="article" label="通用知识库" />
            <el-option value="brand" label="品牌知识库" />
            <el-option value="customer" label="客服知识库" />
            <el-option value="faq" label="FAQ知识库" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="知识库用途描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :disabled="!form.name" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- Upload Dialog -->
    <el-dialog v-model="showUpload" title="上传文档" width="520px">
      <p style="margin-bottom: 12px; color: #909399; font-size: 13px;">
        支持 PDF、DOCX、Markdown、TXT 格式。上传后会自动解析、分块并向量化。
      </p>
      <el-upload
        drag
        :auto-upload="false"
        :show-file-list="true"
        :limit="1"
        @change="(u) => { uploadFile = u.raw || null }"
      >
        <el-icon class="upload-icon" :size="48"><UploadFilled /></el-icon>
        <div class="upload-text">将文件拖到此处，或<em>点击选择文件</em></div>
      </el-upload>
      <template #footer>
        <el-button @click="showUpload = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadFile" @click="uploadDocument">
          上传并解析
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-page {
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

.kb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.kb-card {
  padding: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
}

.kb-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

.kb-card.active {
  border-color: #409eff;
  background: #ecf5ff;
}

.kb-type-badge {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 6px;
  color: #fff;
  background: #409eff;
  font-weight: 700;
  font-size: 14px;
  margin-bottom: 12px;
}

.kb-card h3 {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 6px;
}

.kb-card p {
  color: #909399;
  font-size: 13px;
  margin: 0 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.kb-detail {
  margin-bottom: 24px;
  padding: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafcfb;
}

.kb-detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.kb-detail-header h2 {
  font-size: 16px;
  font-weight: 600;
}

.kb-search {
  padding: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}

.kb-search h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px;
}

.search-hint {
  color: #909399;
  font-size: 13px;
  margin-bottom: 12px;
}

.search-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.search-results {
  display: grid;
  gap: 12px;
}

.search-hit {
  padding: 14px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafcfb;
}

.hit-meta {
  display: flex;
  gap: 16px;
  color: #909399;
  font-size: 11px;
  margin-bottom: 6px;
}

.search-hit p {
  line-height: 1.6;
  font-size: 13px;
  margin: 0;
}

.upload-icon {
  margin-bottom: 8px;
  color: #c0c4cc;
}

.upload-text {
  color: #606266;
  font-size: 13px;
}

.upload-text em {
  color: #409eff;
  font-style: normal;
}
</style>
