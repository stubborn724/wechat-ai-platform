/**
 * SSE (Server-Sent Events) 连接管理器
 * 用于实时接收 AI 文章生成进度
 */

export type SseMessageHandler = (event: string, data: string) => void
export type SseStatusHandler = (connected: boolean) => void

interface SseOptions {
  onMessage: SseMessageHandler
  onStatusChange?: SseStatusHandler
  onError?: (error: Event) => void
}

export class SseConnection {
  private eventSource: EventSource | null = null
  private url: string = ''
  private options: SseOptions
  private reconnectTimer: number | null = null
  private maxRetries = 3
  private retryCount = 0

  constructor(url: string, options: SseOptions) {
    this.url = url
    this.options = options
  }

  connect() {
    if (this.eventSource) {
      this.disconnect()
    }

    const token = localStorage.getItem('access_token')
    const connectUrl = token ? `${this.url}?token=${token}` : this.url

    this.eventSource = new EventSource(connectUrl)
    this.options.onStatusChange?.(true)

    this.eventSource.onopen = () => {
      this.retryCount = 0
      this.options.onStatusChange?.(true)
    }

    // 监听所有命名事件
    const eventTypes = [
      'AGENT1_COMPLETE',
      'TITLES_GENERATED',
      'AGENT2_STREAMING',
      'AGENT2_COMPLETE',
      'OUTLINE_GENERATED',
      'AGENT3_STREAMING',
      'AGENT3_COMPLETE',
      'AGENT4_COMPLETE',
      'IMAGE_COMPLETE',
      'AGENT5_COMPLETE',
      'MERGE_COMPLETE',
      'ALL_COMPLETE',
      'ERROR',
    ]

    for (const eventType of eventTypes) {
      this.eventSource.addEventListener(eventType, (e: MessageEvent) => {
        this.options.onMessage(eventType, e.data)
      })
    }

    // 默认消息处理
    this.eventSource.onmessage = (e: MessageEvent) => {
      this.options.onMessage('message', e.data)
    }

    this.eventSource.onerror = (err: Event) => {
      this.options.onStatusChange?.(false)
      this.options.onError?.(err)
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return
    if (this.retryCount >= this.maxRetries) return

    this.retryCount++
    const delay = Math.min(1000 * Math.pow(2, this.retryCount), 10000)

    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.eventSource) {
      this.eventSource.close()
      this.eventSource = null
    }
    this.options.onStatusChange?.(false)
  }
}

/**
 * 基于 fetch 的 SSE 读取器（用于流式文本）
 */
export async function readFetchSSE(
  url: string,
  onToken: (token: string) => void,
  onComplete?: () => void,
  onError?: (err: any) => void
) {
  try {
    const token = localStorage.getItem('access_token')
    const response = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('No reader available')

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6)
          if (data === '[DONE]') {
            onComplete?.()
            return
          }
          onToken(data)
        }
      }
    }
    onComplete?.()
  } catch (err) {
    onError?.(err)
  }
}
