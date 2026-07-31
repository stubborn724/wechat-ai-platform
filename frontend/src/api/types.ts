export interface User { id: number; email: string; display_name: string; role: string }
export interface Article { id: number; task_id: string; topic: string; status: string; phase: string; main_title?: string; sub_title?: string; content?: string; full_content?: string; cover_image?: string; images?: any[]; title_options?: any[]; outline?: any; style?: string; error_message?: string; footer_template?: string; created_at: string; updated_at: string }
export interface ContentJob { id: number; topic: string; status: string; version: number; content_type?: string; approval_mode?: string; account_id?: string; latest_version?: { title?: string; summary?: string; body_markdown?: string; version_number?: number; source?: string }; created_at: string; updated_at: string; error_message?: string }
export interface ContentJobArticle { id: number; sort_order: number; content_type: string; publish_domain: string; status: string; version?: any; error_message?: string }
export interface ContentJobPage { total: number; page: number; page_size: number; items: ContentJob[] }
export interface Account { id: number; name: string; app_id: string; status: string; auth_mode: string; credential_configured?: boolean; last_health_at?: string; capabilities?: any }
export interface Asset { id: number; filename: string; original_filename?: string; asset_type: string; mime_type?: string; file_size?: number; storage_key?: string; tags?: string[]; width?: number; height?: number; usage_count?: number; preview_url?: string; is_watermarked?: boolean; created_at?: string }
export interface FeedSource { id: number; name: string; slug: string; source_type: string; source_identifier: string; feed_url?: string; is_active: boolean; status?: string; style_profile?: any; article_count?: number; last_fetched_at?: string; created_at: string }
export interface FeedSourceArticle { id: number; title: string; body_markdown?: string; body_html?: string; summary?: string; cover_image_url?: string; word_count?: number; is_analyzed?: boolean; published_at?: string }
export interface FeedSourceArticlePage { total: number; page: number; page_size: number; items: FeedSourceArticle[] }
export interface KnowledgeBase { id: number | string; name: string; slug?: string; kb_type?: string; description?: string; is_active?: boolean; created_at: string }
export interface KbDocument { id: number | string; filename: string; file_type?: string; status: string; chunk_count?: number; error_message?: string; created_at: string }
export interface PublishPlan { id: number; account_id: number; day_of_week: number; article_slots: ArticleSlot[]; publish_times: string[]; public_count: number; private_count: number; is_active: boolean; created_at?: string }
export interface ArticleSlot { content_type: string; sort_order: number; publish_domain: string; topic?: string }
export interface TitleOption { main_title: string; sub_title: string }
export interface LoginRequest { email: string; password: string }
export interface LoginResponse { access_token: string; refresh_token: string; user: User }
export interface PaginatedResponse<T> { total: number; page: number; page_size: number; items: T[] }
export interface ScheduledTask { id: number; name: string; is_active: boolean; writing_mode: string; topic: string | null; feed_source_ids: number[] | null; style: string | null; knowledge_base_ids: number[] | null; day_of_week: number; publish_times: string[]; article_slots: ArticleSlot[] | null; articles_per_day: number; public_count: number; private_count: number; approval_mode: string; account_id: number | null; footer_template: string | null; total_generated: number; last_run_at: string | null; created_at: string; updated_at: string }
export interface ImitationPool { id: number; name: string; description: string | null; is_active: boolean; source_count: number; created_at: string }
export interface ImitationTask { id: number; name: string; pool_id: number | null; strategy: string; articles_per_day: number; status: string; total_generated: number; created_at: string; updated_at: string }
