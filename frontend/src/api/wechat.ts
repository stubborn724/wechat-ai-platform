import client from './client'

export interface Comment {
  id: number
  account_id?: number
  msg_id: string
  comment_id: string
  openid?: string
  nickname?: string
  content: string
  create_time?: string
  reply_content?: string
  reply_create_time?: string
  is_favorited: boolean
  status: string
}

export interface CommentListResponse {
  total: number
  page: number
  page_size: number
  items: Comment[]
}

export interface SyncCommentsRequest {
  account_id: number
  msg_data_id: string
}

export interface ReplyCommentRequest {
  account_id: number
  comment_id: number
  msg_data_id: string
  content: string
}

export interface ToggleFavoriteRequest {
  account_id: number
  comment_id: number
  msg_data_id: string
  favorited: boolean
}

export interface SendTextMessageRequest {
  account_id: number
  openid: string
  text: string
}

export interface SendImageMessageRequest {
  account_id: number
  openid: string
  media_id: string
  media_url?: string
}

export interface SendContactRequest {
  account_id: number
  openid: string
  contact_text: string
  qr_code_media_id: string
}

export interface MessageRecord {
  id: number
  account_id?: number
  openid: string
  msg_type: string
  content?: string
  media_id?: string
  media_url?: string
  status: string
  error_message?: string
  sent_at?: string
  created_at: string
}

export interface MessageListResponse {
  total: number
  page: number
  page_size: number
  items: MessageRecord[]
}

// --- Comments ---

export async function listComments(params: {
  page?: number, page_size?: number, status?: string,
  account_id?: number, article_id?: number,
}): Promise<CommentListResponse> {
  const res = await client.get('/comments', { params })
  return res.data
}

export async function getComment(commentId: number): Promise<Comment> {
  const res = await client.get(`/comments/${commentId}`)
  return res.data
}

export async function syncComments(data: SyncCommentsRequest): Promise<any> {
  const res = await client.post('/comments/sync', data)
  return res.data
}

export async function replyComment(data: ReplyCommentRequest): Promise<any> {
  const res = await client.post('/comments/reply', data)
  return res.data
}

export async function toggleFavorite(data: ToggleFavoriteRequest): Promise<any> {
  const res = await client.post('/comments/toggle-favorite', data)
  return res.data
}

// --- Messages ---

export async function listMessages(params: {
  page?: number, page_size?: number, openid?: string,
  msg_type?: string, account_id?: number,
}): Promise<MessageListResponse> {
  const res = await client.get('/messages', { params })
  return res.data
}

export async function sendTextMessage(data: SendTextMessageRequest): Promise<any> {
  const res = await client.post('/messages/send-text', data)
  return res.data
}

export async function sendImageMessage(data: SendImageMessageRequest): Promise<any> {
  const res = await client.post('/messages/send-image', data)
  return res.data
}

export async function sendContact(data: SendContactRequest): Promise<any> {
  const res = await client.post('/messages/send-contact', data)
  return res.data
}
