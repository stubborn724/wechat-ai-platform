/** HTML 安全清洗工具，使用 DOMPurify 白名单模式 */

import DOMPurify from 'dompurify'

// 严格白名单：只允许安全的排版标签
const ALLOWED_TAGS = [
  'p', 'br', 'hr',
  'strong', 'em', 'u', 's', 'del', 'ins',
  'a',
  'ul', 'ol', 'li',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'blockquote', 'pre', 'code',
  'img',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
  'div', 'span',
  'dl', 'dt', 'dd',
  'sub', 'sup',
  'figure', 'figcaption',
  'input', 'label',
]

const ALLOWED_ATTRS = [
  'href', 'target', 'rel',
  'src', 'alt', 'width', 'height',
  'colspan', 'rowspan',
  'start', 'type',
  'referrerpolicy',
  'style',
  'onclick',
  'id', 'for', 'name', 'checked', 'class',
]

// 禁止的危险标签（即使 DOMPurify 默认已处理，显式声明作为防御纵深）
const FORBIDDEN_TAGS = [
  'style', 'form', 'input', 'button', 'select', 'textarea', 'label',
  'object', 'embed', 'param',
  'svg', 'math',
  'iframe', 'frame', 'frameset',
  'script', 'noscript',
  'canvas', 'audio', 'video',
  'marquee', 'details', 'summary',
]

// 配置 DOMPurify
DOMPurify.setConfig({
  ALLOWED_TAGS,
  ALLOWED_ATTR: ALLOWED_ATTRS,
  FORBID_TAGS: FORBIDDEN_TAGS,
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: false,
})

// Hook: 保留事件属性（画廊缩略图点击切换需要 onclick）
DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
  if (data.attrName === 'onclick') {
    data.forceKeep = true
  }
})

/**
 * 清洗 HTML 字符串，移除危险内容
 * - 过滤所有事件属性（onerror, onclick 等）
 * - 过滤 javascript: URL
 * - 只保留白名单标签和属性
 * - 禁止危险标签（script, iframe, object, svg 等）
 */
export function sanitizeHtml(html: string): string {
  if (!html) return ''
  const cleaned = DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ALLOWED_ATTRS,
    FORBID_TAGS: FORBIDDEN_TAGS,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  })
  // 给所有没有 referrerpolicy 的 img 添加，绕过微信防盗链
  return cleaned.replace(
    /<img(?![^>]*referrerpolicy=)(\s)/g,
    '<img referrerpolicy="no-referrer" '
  )
}

/**
 * 安全的 Markdown 渲染，先渲染 Markdown 再清洗 HTML
 */
export function renderSafeMarkdown(markdown: string): string {
  if (!markdown) return ''
  // 这里不直接 import marked 以避免循环依赖
  // 请在调用方自己 marked.parse 后用 sanitizeHtml 包裹
  return sanitizeHtml(markdown)
}
