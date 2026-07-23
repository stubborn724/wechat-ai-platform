<template>
  <div class="synced-article-detail" v-loading="loading">
    <el-page-header @back="$router.push('/articles/list')" content="文章详情" style="margin-bottom: 16px" />

    <el-card v-if="article">
      <div class="article-header">
        <h2>{{ article.title || '无标题' }}</h2>
        <div class="meta">
          <span v-if="article.author">作者：{{ article.author }}</span>
          <span v-if="article.publish_time">发布时间：{{ formatTime(article.publish_time) }}</span>
          <el-tag size="small" :type="article.article_type === 'published' ? 'success' : 'warning'">
            {{ article.article_type === 'published' ? '已发布' : '草稿' }}
          </el-tag>
          <el-tag v-if="article.need_open_comment" size="small" type="info">已开启评论</el-tag>
        </div>
      </div>

      <el-divider />

      <!-- 摘要 -->
      <div v-if="article.digest" class="digest">
        <h4>摘要</h4>
        <p>{{ article.digest }}</p>
      </div>

      <!-- 正文 -->
      <div v-if="article.content" class="content">
        <h4>正文</h4>
        <div class="html-content" v-html="article.content" />
      </div>
      <div v-else class="no-content">
        <p>正文未缓存</p>
        <el-button type="primary" :loading="fetchingContent" @click="fetchContent">
          从微信拉取正文
        </el-button>
      </div>

      <el-divider />

      <!-- 封面 -->
      <div v-if="article.cover_url" class="cover">
        <h4>封面</h4>
        <img :src="article.cover_url" alt="封面" style="max-width: 300px; border-radius: 6px;" />
      </div>

      <!-- 原文链接 -->
      <div v-if="article.wechat_url" class="wechat-link">
        <el-button type="success" link @click="openWechat">查看原文</el-button>
      </div>

      <!-- 原始数据 -->
      <el-collapse style="margin-top: 16px">
        <el-collapse-item title="微信原始数据">
          <pre class="raw-json">{{ JSON.stringify(article, null, 2) }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getSyncedArticle } from '@/api/wechat'
import type { SyncedArticle } from '@/api/wechat'

const route = useRoute()
const article = ref<SyncedArticle | null>(null)
const loading = ref(false)
const fetchingContent = ref(false)

function formatTime(t?: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function openWechat() {
  if (article.value?.wechat_url) window.open(article.value.wechat_url, '_blank')
}

async function fetchContent() {
  fetchingContent.value = true
  try {
    const res = await getSyncedArticle(article.value!.id, true)
    article.value = res
    ElMessage.success('正文已获取')
  } catch (e: any) {
    ElMessage.error('获取正文失败：' + (e.response?.data?.detail || e.message || ''))
  } finally {
    fetchingContent.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const id = Number(route.params.id)
    const res = await getSyncedArticle(id)
    article.value = res
  } catch (e: any) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message || ''))
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.synced-article-detail { padding: 20px; }
.article-header h2 { margin: 0 0 8px 0; }
.meta { display: flex; gap: 16px; align-items: center; color: #909399; font-size: 13px; flex-wrap: wrap; }
.digest { margin: 16px 0; }
.digest p { color: #606266; line-height: 1.6; }
.content { margin: 16px 0; }
.html-content { line-height: 1.8; }
.html-content img { max-width: 100%; border-radius: 6px; margin: 8px 0; }
.no-content { text-align: center; padding: 40px; color: #909399; }
.cover { margin: 16px 0; }
.wechat-link { margin: 16px 0; }
.raw-json { font-size: 11px; background: #f5f7fa; padding: 12px; border-radius: 4px; max-height: 300px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
</style>
