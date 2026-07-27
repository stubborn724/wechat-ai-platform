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

export interface AutoConfigResponse {
  id: number
  account_id: number
  auto_reply_enabled: boolean
  auto_reply_content: string | null
  auto_msg_enabled: boolean
  auto_msg_content: string | null
}

export interface UpdateAutoConfigRequest {
  auto_reply_enabled?: boolean
  auto_reply_content?: string
  auto_msg_enabled?: boolean
  auto_msg_content?: string
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

// --- Auto Config ---

export async function getAutoConfig(accountId: number): Promise<AutoConfigResponse> {
  const res = await client.get(`/comments/auto-config/${accountId}`)
  return res.data
}

export async function updateAutoConfig(accountId: number, data: UpdateAutoConfigRequest): Promise<AutoConfigResponse> {
  const res = await client.put(`/comments/auto-config/${accountId}`, data)
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

// --- WeChat Synced Articles ---

export interface SyncedArticle {
  id: number
  account_id: number
  article_type: string  // draft / published
  media_id?: string
  wechat_article_id?: string
  title?: string
  author?: string
  digest?: string
  cover_url?: string
  wechat_url?: string
  content?: string
  publish_time?: string
  need_open_comment: number
  last_synced_at?: string
  created_at: string
  updated_at: string
}

export interface SyncedArticleListResponse {
  total: number
  page: number
  page_size: number
  items: SyncedArticle[]
}

export async function listSyncedArticles(params: {
  page?: number, page_size?: number, account_id?: number, article_type?: string,
}): Promise<SyncedArticleListResponse> {
  const res = await client.get('/wechat-articles', { params })
  return res.data
}

export async function syncDrafts(account_id: number): Promise<any> {
  const res = await client.post(`/wechat-articles/sync-drafts?account_id=${account_id}`)
  return res.data
}

export async function syncPublished(account_id: number): Promise<any> {
  const res = await client.post(`/wechat-articles/sync-published?account_id=${account_id}`)
  return res.data
}

export async function getSyncedArticle(article_id: number, fetch_content?: boolean): Promise<SyncedArticle> {
  const params: any = {}
  if (fetch_content) params.fetch_content = true
  const res = await client.get(`/wechat-articles/${article_id}`, { params })
  return res.data
}

export async function deleteSyncedArticle(article_id: number): Promise<any> {
  const res = await client.delete(`/wechat-articles/${article_id}`)
  return res.data
}

// --- Comment Leads ---

export interface LeadItem {
  id: number
  account_id: number
  openid: string
  nickname: string
  comment_content: string
  comment_time: string | null
  article_title: string
  article_id: number | null
  intent_type: string | null
  intent_score: number | null
  reply_type: string | null
  reply_content: boolean
  lead_status: string
  eligibility: any
  assigned_to_name: string | null
  created_at: string | null
}

export interface LeadListResponse {
  total: number
  page: number
  page_size: number
  items: LeadItem[]
}

export interface QueueStats {
  [key: string]: number
}

export async function listLeads(params: {
  queue?: string, page?: number, page_size?: number,
  account_id?: number, intent_type?: string,
  operator_id?: number, keyword?: string,
}): Promise<LeadListResponse> {
  const res = await client.get('/leads', { params })
  return res.data
}

export async function getLead(leadId: number): Promise<any> {
  const res = await client.get(`/leads/${leadId}`)
  return res.data
}

export async function getQueueStats(params?: { account_id?: number }): Promise<QueueStats> {
  const res = await client.get('/leads/queue-stats', { params })
  return res.data
}

export async function syncLeads(data: {
  account_id: number, scope?: string, article_id?: number,
}): Promise<{ job_id: number, status: string }> {
  const res = await client.post('/leads/sync', data)
  return res.data
}

export async function getSyncJobStatus(jobId: number): Promise<any> {
  const res = await client.get(`/leads/sync-jobs/${jobId}`)
  return res.data
}

export async function publicReply(leadId: number, data: {
  reply_type: string, content: string,
}): Promise<any> {
  const res = await client.post(`/leads/${leadId}/public-reply`, data)
  return res.data
}

export async function generateReply(leadId: number, data: {
  reply_type?: string, keyword?: string,
}): Promise<any> {
  const res = await client.post(`/leads/${leadId}/generate-reply`, data)
  return res.data
}

export async function closeLead(leadId: number): Promise<any> {
  const res = await client.post(`/leads/${leadId}/close`)
  return res.data
}

// --- P1.1 Contact Packages ---

export interface ContactPackage {
  id: number
  account_id: number
  account_name: string
  name: string
  description: string | null
  contact_name: string | null
  wechat_id: string | null
  phone: string | null
  text_content: string | null
  qr_asset_id: number | null
  qr_url: string | null
  media_status: string | null
  is_default: boolean
  is_enabled: boolean
  usage_count: number
  created_at: string | null
  updated_at: string | null
}

export async function listContactPackages(params?: {
  account_id?: number, enabled?: boolean, keyword?: string,
  page?: number, page_size?: number,
}): Promise<{ total: number, items: ContactPackage[] }> {
  const res = await client.get('/contact-packages', { params })
  return res.data
}

export async function getContactPackage(id: number): Promise<ContactPackage> {
  const res = await client.get(`/contact-packages/${id}`)
  return res.data
}

export async function createContactPackage(data: any): Promise<any> {
  const res = await client.post('/contact-packages', data)
  return res.data
}

export async function updateContactPackage(id: number, data: any): Promise<any> {
  const res = await client.put(`/contact-packages/${id}`, data)
  return res.data
}

export async function enableContactPackage(id: number): Promise<any> {
  const res = await client.post(`/contact-packages/${id}/enable`)
  return res.data
}

export async function disableContactPackage(id: number): Promise<any> {
  const res = await client.post(`/contact-packages/${id}/disable`)
  return res.data
}

export async function deleteContactPackage(id: number): Promise<any> {
  const res = await client.delete(`/contact-packages/${id}`)
  return res.data
}

// --- P1.3 Eligibility ---

export async function checkEligibility(leadId: number): Promise<any> {
  const res = await client.post(`/leads/${leadId}/check-eligibility`)
  return res.data
}

// --- P1.4 Deliveries ---

export async function createDelivery(leadId: number, data: {
  package_id: number, idempotency_key: string,
}): Promise<{ delivery_id: number, status: string }> {
  const res = await client.post(`/leads/${leadId}/deliveries`, data)
  return res.data
}

export async function getDelivery(deliveryId: number): Promise<any> {
  const res = await client.get(`/deliveries/${deliveryId}`)
  return res.data
}

export async function listLeadDeliveries(leadId: number): Promise<any[]> {
  const res = await client.get(`/leads/${leadId}/deliveries`)
  return res.data
}

// --- P1.5 Retry ---

export async function retryDelivery(deliveryId: number, data: {
  step: string, idempotency_key: string,
}): Promise<any> {
  const res = await client.post(`/deliveries/${deliveryId}/retry`, data)
  return res.data
}

// --- P1.2 Media Assets ---

export async function uploadMediaAsset(data: {
  account_id: number, asset_id: number,
}): Promise<any> {
  const res = await client.post('/media-assets/upload', data)
  return res.data
}

export async function getMediaAsset(id: number): Promise<any> {
  const res = await client.get(`/media-assets/${id}`)
  return res.data
}

export async function refreshMediaAsset(id: number): Promise<any> {
  const res = await client.post(`/media-assets/${id}/refresh`)
  return res.data
}
