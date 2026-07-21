import client from './client'
import type { Article, TitleOption } from './types'

export interface CreateArticleRequest {
  topic: string
  style?: string
  image_source?: 'local' | 'pexels'
  enabled_image_methods?: string[]
  user_description?: string
  mode?: string
  article_count?: number
  account_id?: number
  knowledge_base_ids?: number[]
  source_feed_id?: number
  feed_article_ids?: number[]
  selected_image_urls?: string[]
}

export interface ConfirmTitleRequest {
  main_title: string
  sub_title: string
  user_description?: string
}

export interface ConfirmOutlineRequest {
  outline: any
}

export interface AiModifyOutlineRequest {
  main_title: string
  sub_title: string
  current_outline: any
  modify_suggestion: string
}

export async function createArticle(data: CreateArticleRequest): Promise<Article> {
  const res = await client.post('/articles/create', data)
  return res.data.data || res.data
}

export async function confirmTitle(taskId: string, data: ConfirmTitleRequest): Promise<Article> {
  const res = await client.post(`/articles/${taskId}/confirm-title`, data)
  return res.data.data || res.data
}

export async function confirmOutline(taskId: string, data: ConfirmOutlineRequest): Promise<Article> {
  const res = await client.post(`/articles/${taskId}/confirm-outline`, data)
  return res.data.data || res.data
}

export async function aiModifyOutline(taskId: string, data: AiModifyOutlineRequest): Promise<any> {
  const res = await client.post(`/articles/${taskId}/ai-modify-outline`, data)
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

export async function publishDraft(taskId: string, accountId: number) {
  const res = await client.post(`/articles/${taskId}/publish-draft`, null, {
    params: { account_id: accountId },
  })
  return res.data
}
