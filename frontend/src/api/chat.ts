import { getToken } from './index'

/** 消息角色 */
export type MessageRole = 'human' | 'ai' | 'system'

/** 引用证据(方案B): AI 回答引用的来源(图片缩略图 / 文本来源卡片) */
export interface EvidenceItem {
  /** 来源类型: image=知识库图片 / text=文本来源 */
  type?: 'image' | 'text'
  /** 展示标签: 文本来源的标题(text 类型), 如"历史记忆"或文件名 */
  label?: string
  filename: string
  /** 图片来源的 URL(image 类型) */
  url?: string
  /** 文本来源摘要片段(text 类型) */
  text?: string
  /** 引用它的 kb_context 1基编号列表(同一来源可能被多个 chunk 引用), 供 [N] 徽标跳转 */
  indexes?: number[]
}

/** 单条消息 */
export interface Message {
  id?: string
  role: MessageRole
  content: string
  /** 图片 base64 列表(用户上传的图片) */
  images?: string[]
  /** 引用证据(方案B, AI 消息): 回答引用的知识库图片 */
  evidence?: EvidenceItem[]
  /** 评估置信分(0-100, AI 消息; 通过/审批后由 done 事件携带) */
  score?: number
  created_at?: string
}

/** SSE 事件类型(与后端 chat.py 实际发送的事件对齐; delta/sources/meta 为旧协议遗留, 已移除) */
export type ChatEventType = 'token' | 'done' | 'interrupt' | 'error' | 'node_update' | 'title_update'

/** SSE 推送的事件结构 */
export interface ChatEvent {
  event?: ChatEventType
  type?: ChatEventType
  /** 文本片段(流式 token) */
  content?: string
  /** 完整文本(结束事件时可能携带) */
  text?: string
  /** 引用证据(方案B, done 事件携带): 被引用的知识库图片 */
  evidence?: EvidenceItem[]
  /** 审批负载(命中审批拦截时携带) */
  approval?: ApprovalPayload
  /** 错误信息 */
  message?: string
  /** 节点名称(node_update 事件) */
  node?: string
  /** 节点中文标签(node_update 事件) */
  label?: string
  [key: string]: unknown
}

/** 审批负载: 评估分数等 */
export interface ApprovalPayload {
  session_id?: string
  query?: string
  draft?: string
  score?: number
  reason?: string
  [key: string]: unknown
}

/** 审批请求参数 */
export interface ApproveParams {
  approved: boolean
  reason?: string
}

/**
 * 通过 SSE 发送消息并流式接收回复.
 * 因 axios 不支持 SSE, 这里使用 fetch + ReadableStream 实现.
 *
 * @param sessionId 会话 ID
 * @param text      文本内容
 * @param image     图片 base64(可为 null)
 * @param onEvent   每收到一条 SSE data 事件时回调
 */
export async function sendMessage(
  sessionId: string,
  text: string,
  image: string | null,
  onEvent: (data: ChatEvent) => void,
): Promise<void> {
  const token = getToken()
  const response = await fetch(`/api/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: token ? `Bearer ${token}` : '',
    },
    body: JSON.stringify({ text, image }),
  })

  if (!response.ok) {
    throw new Error(`对话请求失败: ${response.status} ${response.statusText}`)
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    // 最后一段可能不完整, 留到下次拼接
    buffer = lines.pop() || ''
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('data:')) {
        const payload = trimmed.slice(5).trim()
        if (!payload || payload === '[DONE]') {
          onEvent({ event: 'done' })
          continue
        }
        try {
          onEvent(JSON.parse(payload))
        } catch {
          // 非 JSON 数据, 作为纯文本 token 处理
          onEvent({ event: 'token', content: payload })
        }
      }
    }
  }
}

/**
 * 提交人工审批结果: POST /sessions/:id/approve
 * 后端返回 SSE 流(approve 时发 done, reject 时发 token + done)
 */
export async function approveSession(
  sessionId: string,
  params: ApproveParams,
  onEvent: (data: ChatEvent) => void,
): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: tokenHeader(),
    },
    body: JSON.stringify(params),
  })

  if (!response.ok) {
    throw new Error(`审批请求失败: ${response.status} ${response.statusText}`)
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('data:')) {
        const payload = trimmed.slice(5).trim()
        if (!payload || payload === '[DONE]') {
          onEvent({ event: 'done' })
          continue
        }
        try {
          onEvent(JSON.parse(payload))
        } catch {
          onEvent({ event: 'token', content: payload })
        }
      }
    }
  }
}

function tokenHeader(): string {
  const token = getToken()
  return token ? `Bearer ${token}` : ''
}
