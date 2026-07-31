import client from './client'
import type { Article } from './types'

export interface CreateArticleRequest {
  topic: string
  style?: string
  /** 封面图片来源；ERP 图片导入本地后按 local 提交。 */
  image_source?: 'local' | 'DASHSCOPE'
  enabled_image_methods?: string[]
  user_description?: string
  mode?: 'auto'
  article_count?: number
  account_ids?: number[]
  publish_mode?: string
  knowledge_base_ids?: number[]
  source_feed_id?: number
  feed_article_ids?: number[]
  /** 正文预选图片，和封面字段严格分离。 */
  selected_image_urls?: string[]
  /** 用户明确选定的文章封面，本地与 ERP 入口共用此字段。 */
  selected_cover_image_url?: string
}

export async function createArticle(data: CreateArticleRequest): Promise<Article> {
  const res = await client.post('/articles/create', data)
  return res.data.data || res.data
}

export async function getArticle(taskId: string): Promise<Article> {
  const res = await client.get(`/articles/${taskId}`)
  return res.data.data || res.data
}

export async function listArticles(params: { page?: number; page_size?: number; status?: string }) {
  const res = await client.get('/articles', { params })
  return res.data
}

export async function deleteArticle(id: number) {
  await client.delete(`/articles/${id}`)
}

export async function getExecutionLogs(taskId: string) {
  const res = await client.get(`/articles/${taskId}/logs`)
  return res.data.data || res.data
}

export async function publishDraft(taskId: string, accountId: number, mode: string = 'draft') {
  const res = await client.post(`/articles/${taskId}/publish-draft`, null, {
    params: { account_id: accountId, mode },
  })
  return res.data
}
