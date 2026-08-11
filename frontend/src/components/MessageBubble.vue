<script setup lang="ts">
import { computed, ref } from 'vue'
import { marked } from 'marked'
import type { MessageRole, EvidenceItem } from '@/api/chat'

const props = withDefaults(
  defineProps<{
    role: MessageRole
    content: string
    images?: string[]
    /** 引用证据(方案B, AI 消息): 回答引用的知识库图片 */
    evidence?: EvidenceItem[]
    /** 评估置信分(0-100), AI 消息由 done 事件携带 */
    score?: number
    /** 是否处于流式输出中(显示打字光标) */
    streaming?: boolean
  }>(),
  {
    images: () => [],
    evidence: () => [],
    score: undefined,
    streaming: false,
  },
)

const markdownBodyRef = ref<HTMLElement | null>(null)

// 配置 marked: 关闭 mangle, 启用基础换行
marked.setOptions({ breaks: true, gfm: true })

const isHuman = computed(() => props.role === 'human')

/** 检索内容N 引用正则: [检索内容N] / (检索内容N) / （检索内容N） */
const CITE_RE = /[\[（(]检索内容(\d+)[\]）)]/g

/**
 * 引用编号映射:
 * - 证据到达后按"去重后来源"编号: 同一来源的多个引用共享同一编号, 编号数 = 证据卡片数,
 *   避免"正文 1-5、证据只有 3 张"的错位;
 * - 流式期间证据未到, 先按出现顺序临时编号, done 后由证据一次性覆盖收敛。
 */
const citeSeqMap = computed(() => {
  const map = new Map<string, number>()
  // 仅当证据带 indexes(实时消息)才按去重后来源编号; 历史回放无 indexes 时回退按出现顺序
  const hasIndexes = props.evidence.some((ev) => (ev.indexes ?? []).length)
  if (hasIndexes) {
    props.evidence.forEach((ev, p) => {
      for (const idx of ev.indexes ?? []) {
        if (idx != null && !map.has(String(idx))) map.set(String(idx), p + 1)
      }
    })
    return map
  }
  let seq = 0
  for (const m of props.content.matchAll(CITE_RE)) {
    const n = m[1]
    if (!map.has(n)) map.set(n, ++seq)
  }
  return map
})

/** 证据项的来源编号(卡片角标, 与正文徽标对应) */
function citeSeq(index?: number): string {
  if (index == null) return ''
  return String(citeSeqMap.value.get(String(index)) ?? '')
}

/** 把 [检索内容N] 渲染成句子右上角的 [seq] 引用徽标(seq 按去重后来源编号) */
function renderCitations(md: string): string {
  return md.replace(CITE_RE, (_m, n: string) => {
    const seq = citeSeqMap.value.get(n) ?? 0
    return `<sup class="cite-badge" data-cite="${n}" title="引用证据 ${seq}">[${seq}]</sup>`
  })
}

// 将 Markdown 渲染为 HTML(先替换引用徽标)
const renderedHtml = computed(() => {
  if (!props.content) return ''
  return marked.parse(renderCitations(props.content)) as string
})

/** 点击引用徽标 → 滚动并高亮对应的证据图(同一图可能被多个编号引用) */
function handleMarkdownClick(e: Event): void {
  const target = (e.target as HTMLElement).closest?.('.cite-badge') as HTMLElement | null
  if (!target) return
  const n = Number(target.dataset.cite)
  if (!n || !props.evidence.length) return
  const pos = props.evidence.findIndex((ev) => (ev.indexes ?? []).includes(n))
  if (pos < 0) return
  const root = markdownBodyRef.value?.closest('.bubble')
  const item = root?.querySelector(`[data-evidence-idx="${pos}"]`)
  item?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  item?.classList.add('flash')
  window.setTimeout(() => item?.classList.remove('flash'), 1600)
}

// 图片预览列表(ElImage 需要 string[])
const previewList = computed(() => props.images || [])

// 证据区中的图片项(文本来源不含 url, 单独筛出供 el-image 预览)
const evidenceImages = computed(() => props.evidence.filter((e) => e.type !== 'text'))
const evidenceImageUrls = computed(() => evidenceImages.value.map((e) => e.url ?? ''))

// 头像文字
const avatarText = computed(() => (isHuman.value ? '我' : 'AI'))

// ---- 置信章: 仅 AI 消息且已结束流式且带分数时展示 ----
const showStamp = computed(
  () => props.role === 'ai' && !props.streaming && typeof props.score === 'number',
)
const stampTone = computed(() => {
  const s = props.score ?? 0
  if (s >= 80) return 'high'
  if (s >= 60) return 'mid'
  return 'low'
})
const stampLabel = computed(() => {
  const s = props.score ?? 0
  if (s >= 80) return '高可信'
  if (s >= 60) return '中等'
  return '低可信'
})
</script>

<template>
  <div class="message-row" :class="{ 'is-human': isHuman }">
    <div class="avatar" :class="{ 'avatar-ai': !isHuman }">
      {{ avatarText }}
    </div>

    <div class="bubble-wrap">
      <!-- 图片缩略图(仅用户消息: 上传的图) -->
      <div v-if="isHuman && previewList.length" class="image-row">
        <el-image
          v-for="(img, idx) in previewList"
          :key="idx"
          class="thumb"
          :src="img"
          :preview-src-list="previewList"
          :initial-index="idx"
          fit="cover"
          preview-teleported
          hide-on-click-modal
        />
      </div>

      <!-- 文本气泡: 用户=墨色紧凑块, AI=纸面卡片+证据轨+置信章 -->
      <div class="bubble" :class="isHuman ? 'bubble-human' : 'bubble-ai'">
        <div v-if="showStamp" class="confidence-stamp" :class="`stamp-${stampTone}`">
          <span class="stamp-glyph">◉</span>
          {{ stampLabel }}<span class="stamp-num"> · {{ score }}</span>
        </div>
        <div
          v-if="content"
          ref="markdownBodyRef"
          class="markdown-body"
          :class="{ 'typing-cursor': streaming }"
          v-html="renderedHtml"
          @click="handleMarkdownClick"
        ></div>
        <div v-else-if="streaming" class="typing-cursor placeholder-dot"></div>
        <div v-else-if="isHuman && previewList.length" class="empty-content">[图片]</div>
        <div v-else class="empty-content">（无内容）</div>

        <!-- 引用证据区(方案B): 仅 AI 消息展示回答引用的来源(图片缩略图 + 文本来源卡片) -->
        <div v-if="!isHuman && evidence.length" class="evidence-area">
          <div class="evidence-title">⚑ 引用证据</div>
          <div class="evidence-row">
            <div
              v-for="(ev, idx) in evidence"
              :key="idx"
              class="evidence-item"
              :class="{ 'is-text': ev.type === 'text' }"
              :data-evidence-idx="idx"
            >
              <div class="evidence-figure">
                <el-image
                  v-if="ev.type !== 'text'"
                  class="evidence-thumb"
                  :src="ev.url"
                  :preview-src-list="evidenceImageUrls"
                  :initial-index="evidenceImages.indexOf(ev)"
                  fit="cover"
                  preview-teleported
                  hide-on-click-modal
                />
                <div v-else class="evidence-card">
                  <div class="evidence-card-file">📄 {{ ev.label || ev.filename || '文档' }}</div>
                  <div v-if="ev.text" class="evidence-card-snippet">{{ ev.text }}</div>
                </div>
                <span v-if="citeSeq(ev.indexes?.[0])" class="evidence-num">{{ citeSeq(ev.indexes?.[0]) }}</span>
              </div>
              <span v-if="ev.type !== 'text'" class="evidence-name">{{ ev.filename }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.message-row {
  display: flex;
  gap: 12px;
  padding: 8px 24px;
  align-items: flex-start;

  &.is-human {
    flex-direction: row-reverse;

    .bubble-wrap {
      align-items: flex-end;
    }
    .image-row {
      justify-content: flex-end;
    }
  }
}

// 头像: 无渐变, 克制
.avatar {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  background: var(--surface-2);
  color: var(--ink-text);
  border: 1px solid var(--hairline);
}
.avatar-ai {
  background: var(--brass-soft);
  color: var(--brass);
  border-color: transparent;
  font-family: var(--font-mono);
}

.bubble-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 70%;
}

.image-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.thumb {
  width: 140px;
  height: 140px;
  border-radius: var(--radius-md);
  border: 1px solid var(--hairline);
  cursor: pointer;
  object-fit: cover;
}

// ---- 引用证据区(方案B): 分隔线 + 黄铜标题 + 缩略图 ----
.evidence-area {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid rgba(0, 0, 0, 0.07);

  .evidence-title {
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.4px;
    color: var(--brass);
    margin-bottom: 8px;
  }

  .evidence-row {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }

  .evidence-item {
    display: flex;
    flex-direction: column;
    gap: 3px;
    width: 88px;

    // 文本来源卡片: 更宽
    &.is-text {
      width: 200px;
    }
  }

  .evidence-figure {
    position: relative;
    width: 88px;
    height: 88px;

    .is-text & {
      width: 200px;
      height: auto;
    }
  }

  .evidence-thumb {
    width: 88px;
    height: 88px;
    border-radius: var(--radius-sm);
    border: 1px solid rgba(0, 0, 0, 0.08);
    cursor: pointer;
    object-fit: cover;
  }

  // 文本来源卡片
  .evidence-card {
    width: 200px;
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: var(--radius-sm);
    background: rgba(194, 154, 59, 0.04);
    padding: 8px 10px;

    .evidence-card-file {
      font-size: 11px;
      font-weight: 600;
      color: var(--paper-ink);
      margin-bottom: 4px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .evidence-card-snippet {
      font-size: 11px;
      line-height: 1.5;
      color: var(--muted);
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
  }

  .evidence-num {
    position: absolute;
    top: 4px;
    left: 4px;
    min-width: 15px;
    height: 15px;
    padding: 0 3px;
    border-radius: 4px;
    background: rgba(27, 24, 20, 0.78);
    color: #f0e6cf;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    line-height: 15px;
    text-align: center;
    pointer-events: none;
  }

  .evidence-name {
    font-size: 10px;
    line-height: 1.3;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  // 点击引用徽标后的高亮闪烁(图片缩略图 / 文本来源卡片)
  .evidence-item.flash .evidence-thumb,
  .evidence-item.flash .evidence-card {
    outline: 2px solid var(--brass);
    outline-offset: 1px;
    animation: evidence-flash 0.6s ease;
  }
}

@keyframes evidence-flash {
  0% {
    box-shadow: 0 0 0 4px rgba(194, 154, 59, 0.35);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(194, 154, 59, 0);
  }
}

.bubble {
  position: relative;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

// 用户: 墨色紧凑块(无渐变)
.bubble-human {
  background: var(--surface-2);
  color: var(--ink-text);
  border: 1px solid var(--hairline);
  border-top-right-radius: 4px;
}

// AI: 纸面卡片 + 左侧黄铜证据轨
.bubble-ai {
  background: var(--paper);
  color: var(--paper-ink);
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-left: 3px solid var(--brass);
  border-top-left-radius: 4px;
  padding-top: 34px; // 给右上置信章留位
}

// ---- 置信章(签名元素) ----
.confidence-stamp {
  position: absolute;
  top: 10px;
  right: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.4px;
  padding: 2px 8px;
  border-radius: 3px;
  border: 1px solid currentColor;

  .stamp-glyph {
    font-size: 9px;
  }
  .stamp-num {
    opacity: 0.75;
  }

  &.stamp-high {
    color: var(--brass);
    background: rgba(194, 154, 59, 0.08);
  }
  &.stamp-mid {
    color: #9c7d2f;
    background: rgba(199, 162, 74, 0.1);
  }
  &.stamp-low {
    color: #a04a3f;
    background: rgba(194, 102, 90, 0.1);
  }
}

.placeholder-dot {
  min-height: 20px;
  color: var(--paper-ink);
}

// ---- Markdown 渲染样式(纸面: 墨字在纸上) ----
.markdown-body {
  :deep() {
    p {
      margin: 0 0 8px;
      &:last-child {
        margin-bottom: 0;
      }
    }
    h1,
    h2,
    h3,
    h4 {
      margin: 12px 0 8px;
      font-weight: 600;
      color: var(--paper-ink);
    }
    h1 {
      font-size: 18px;
    }
    h2 {
      font-size: 16px;
    }
    h3 {
      font-size: 15px;
    }
    ul,
    ol {
      padding-left: 20px;
      margin: 0 0 8px;
    }
    li {
      margin: 2px 0;
    }
    code {
      background: rgba(0, 0, 0, 0.07);
      padding: 2px 6px;
      border-radius: 4px;
      font-family: var(--font-mono);
      font-size: 13px;
      color: #7a5220;
    }
    // 引用徽标: 句子右上角的 [N](由 (检索内容N) 渲染而来, 点击跳证据)
    sup.cite-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 15px;
      height: 15px;
      padding: 0 3px;
      margin: 0 1px;
      border-radius: 4px;
      background: rgba(194, 154, 59, 0.12);
      border: 1px solid rgba(194, 154, 59, 0.35);
      color: var(--brass);
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 600;
      line-height: 1;
      cursor: pointer;
      vertical-align: super;
      transition: background 0.15s;
      &:hover {
        background: rgba(194, 154, 59, 0.22);
      }
    }
    pre {
      background: rgba(0, 0, 0, 0.08);
      padding: 12px;
      border-radius: var(--radius-sm);
      overflow-x: auto;
      margin: 0 0 8px;
      code {
        background: transparent;
        padding: 0;
        color: var(--paper-ink);
      }
    }
    blockquote {
      border-left: 3px solid var(--brass);
      padding-left: 12px;
      margin: 8px 0;
      color: #6b6558;
    }
    a {
      color: #8a6a1e;
    }
    table {
      border-collapse: collapse;
      margin: 8px 0;
      th,
      td {
        border: 1px solid rgba(0, 0, 0, 0.15);
        padding: 6px 10px;
      }
      th {
        background: rgba(0, 0, 0, 0.05);
      }
    }
    hr {
      border: none;
      border-top: 1px solid rgba(0, 0, 0, 0.15);
      margin: 12px 0;
    }
  }
}

.empty-content {
  color: var(--muted);
  font-style: italic;
}
</style>
