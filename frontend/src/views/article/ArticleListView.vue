<template>
  <div class="wechat-article-list">
    <!-- 顶部操作栏 -->
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-select v-model="filterAccountId" placeholder="选择公众号" clearable style="width:220px" @change="onAccountChange">
          <el-option v-for="a in accounts" :key="a.id" :label="a.label" :value="a.id" />
        </el-select>

        <el-button type="primary" :loading="syncing" @click="handleSyncAll">
          同步全部
        </el-button>
        <el-button @click="handleSyncDrafts" :loading="syncingDrafts" :disabled="!filterAccountId">
          同步草稿箱
        </el-button>
        <el-button @click="handleSyncPublished" :loading="syncingPublished" :disabled="!filterAccountId">
          同步已发布
        </el-button>

        <el-button type="success" @click="$router.push('/articles')" style="margin-left:auto">
          新建文章
        </el-button>
      </div>
    </el-card>

    <!-- Tab: 草稿箱 / 已发布 -->
    <el-card shadow="never" class="table-card">
      <el-tabs v-model="activeTab" @tab-change="handleTabChange">
        <el-tab-pane label="草稿箱" name="draft">
          <el-table :data="articles" v-loading="loading" stripe style="width:100%" empty-text="暂无数据，请先同步">
            <el-table-column label="标题" min-width="280" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="article-title" @click="viewDetail(row)">{{ row.title || '无标题' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="作者" width="120" prop="author" />
            <el-table-column label="摘要" min-width="200" show-overflow-tooltip prop="digest" />
            <el-table-column label="同步时间" width="170">
              <template #default="{ row }">{{ formatTime(row.last_synced_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button type="primary" link size="small" @click="viewDetail(row)">查看</el-button>
                <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="已发布" name="published">
          <el-table :data="articles" v-loading="loading" stripe style="width:100%" empty-text="暂无数据，请先同步">
            <el-table-column label="标题" min-width="280" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="article-title" @click="viewDetail(row)">{{ row.title || '无标题' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="作者" width="120" prop="author" />
            <el-table-column label="摘要" min-width="200" show-overflow-tooltip prop="digest" />
            <el-table-column label="发布时间" width="170">
              <template #default="{ row }">{{ formatTime(row.publish_time) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button type="primary" link size="small" @click="viewDetail(row)">查看</el-button>
                <el-button type="success" link size="small" @click="openWechat(row)" v-if="row.wechat_url">打开原文</el-button>
                <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="fetchArticles"
          @size-change="(s: number) => { pageSize = s; page = 1; fetchArticles() }"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listSyncedArticles, syncDrafts, syncPublished, deleteSyncedArticle } from '@/api/wechat'
import type { SyncedArticle } from '@/api/wechat'
import client from '@/api/client'

const router = useRouter()

const accounts = ref<any[]>([])
const filterAccountId = ref<number | undefined>()
const activeTab = ref('draft')

const articles = ref<SyncedArticle[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const syncing = ref(false)
const syncingDrafts = ref(false)
const syncingPublished = ref(false)

function formatTime(t?: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

async function fetchAccounts() {
  try {
    const res = await client.get('/accounts')
    const items: any[] = res.data?.items || res.data || []
    accounts.value = items.filter((a: any) => a.id != null).map((a: any) => ({
      ...a, label: a.name || a.app_id,
    }))
  } catch { /* ignore */ }
}

async function fetchArticles() {
  loading.value = true
  try {
    const res = await listSyncedArticles({
      page: page.value,
      page_size: pageSize.value,
      account_id: filterAccountId.value,
      article_type: activeTab.value,
    })
    articles.value = res.items
    total.value = res.total
  } catch (e: any) {
    ElMessage.error('加载失败：' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

function onAccountChange() {
  page.value = 1
  fetchArticles()
}

function handleTabChange() {
  page.value = 1
  fetchArticles()
}

async function handleSyncDrafts() {
  if (!filterAccountId.value) { ElMessage.warning('请先选择公众号'); return }
  syncingDrafts.value = true
  try {
    const res = await syncDrafts(filterAccountId.value)
    ElMessage.success(`同步完成：新增 ${res.synced} 条，共 ${res.total} 条`)
    fetchArticles()
  } catch (e: any) {
    const detail = e.response?.data?.detail || e.message || ''
    if (detail.includes('48001')) {
      ElMessage.warning('当前公众号类型不支持同步草稿箱（仅认证服务号可用）')
    } else {
      ElMessage.error('同步失败：' + detail)
    }
  } finally {
    syncingDrafts.value = false
  }
}

async function handleSyncPublished() {
  if (!filterAccountId.value) { ElMessage.warning('请先选择公众号'); return }
  syncingPublished.value = true
  try {
    const res = await syncPublished(filterAccountId.value)
    ElMessage.success(`同步完成：新增 ${res.synced} 条，共 ${res.total} 条`)
    fetchArticles()
  } catch (e: any) {
    const detail = e.response?.data?.detail || e.message || ''
    if (detail.includes('48001')) {
      ElMessage.warning('当前公众号类型不支持同步已发布文章（仅认证服务号可用）')
    } else {
      ElMessage.error('同步失败：' + detail)
    }
  } finally {
    syncingPublished.value = false
  }
}

async function handleSyncAll() {
  if (!filterAccountId.value) { ElMessage.warning('请先选择公众号'); return }
  syncing.value = true
  try {
    const [draftRes, pubRes] = await Promise.all([
      syncDrafts(filterAccountId.value),
      syncPublished(filterAccountId.value),
    ])
    ElMessage.success(`草稿箱: ${draftRes.synced} 条 / 已发布: ${pubRes.synced} 条`)
    fetchArticles()
  } catch (e: any) {
    const detail = e.response?.data?.detail || e.message || ''
    if (detail.includes('48001')) {
      ElMessage.warning('当前公众号不支持同步（仅认证服务号可用），但评论管理仍可正常使用')
    } else {
      ElMessage.error('同步失败：' + detail)
    }
  } finally {
    syncing.value = false
  }
}

function viewDetail(row: SyncedArticle) {
  router.push(`/articles/synced/${row.id}`)
}

function openWechat(row: SyncedArticle) {
  if (row.wechat_url) window.open(row.wechat_url, '_blank')
}

async function handleDelete(row: SyncedArticle) {
  try {
    await ElMessageBox.confirm('确定删除本地同步记录？不会影响微信端的文章。', '确认', {
      confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning',
    })
    await deleteSyncedArticle(row.id)
    ElMessage.success('已删除')
    fetchArticles()
  } catch { /* ignore */ }
}

onMounted(() => {
  fetchAccounts()
  fetchArticles()
})
</script>

<style scoped>
.wechat-article-list { padding: 20px; }
.filter-card { margin-bottom: 16px; border-radius: 8px; }
.filter-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.table-card { border-radius: 8px; }
.article-title { color: #409eff; cursor: pointer; }
.article-title:hover { text-decoration: underline; }
.pagination-wrapper { display: flex; justify-content: flex-end; margin-top: 20px; }
</style>
