<script setup lang="ts">
import { computed, ref } from 'vue';
import { marked } from 'marked';
import type { MessageRole, EvidenceItem } from '@/api/chat';

const props = withDefaults(
  defineProps<{
    role: MessageRole;
    content: string;
    images?: string[];
    /** 引用证据(方案B, AI 消息): 回答引用的知识库图片 */
    evidence?: EvidenceItem[];
    /** 评估置信分(0-100), AI 消息由 done 事件携带 */
    score?: number;
    /** 是否处于流式输出中(显示打字光标) */
    streaming?: boolean;
    /** 是否展示置信章(仅管理员; 普通用户不暴露内部 QA 分数) */
    showConfidence?: boolean;
  }>(),
  {
    images: () => [],
    evidence: () => [],
    score: undefined,
    streaming: false,
    showConfidence: true,
  },
);

const markdownBodyRef = ref<HTMLElement | null>(null);
/** 文本来源卡片展开状态(点击查看完整来源, 与图片可点击预览对齐) */
const expandedTextIdx = ref<number | null>(null);

function toggleText(idx: number): void {
  expandedTextIdx.value = expandedTextIdx.value === idx ? null : idx;
}

function isLongText(text?: string): boolean {
  return (text || '').length > 60;
}

// 配置 marked: 关闭 mangle, 启用基础换行
marked.setOptions({ breaks: true, gfm: true });

const isHuman = computed(() => props.role === 'human');

/**
 * evidence 归一化: 历史消息无证据时后端返回 null(非 undefined), 直接 props.evidence 会在
 * 模板 `evidence.length` 处抛 null.length 异常, 导致 AI 消息整条渲染失败(刷新后只见"我"不见 AI)。
 */
const evidenceList = computed<EvidenceItem[]>(() => props.evidence || []);

/**
 * 检索引用正则: 兼容模型格式漂移——
 * - "检索内容N"/"检索文档N"/"检索资料N"/"检索来源N" + 半角/全角括号(可在词前/词后/词与数字间)
 * - 裸数字引用 "[N]" / "[[N]" / "【N】" / "（N）"(模型偶发不带"检索内容"前缀)
 * 避免标记显示为原始文本、证据对不上。
 */
const CITE_RE =
  /[【[（(]{0,2}\s*(?:检索(?:内容|文档|资料|来源))?\s*[【[（(]?\s*(\d+)\s*[】\]）)]+/g;

/**
 * 折叠紧邻的重复引用标记: 模型常重复输出引用(如 "部署 [2][2]"),
 * 这里把连续相同的引用折叠成一个, 避免正文出现 [2][2] 这种冗余。
 */
const DUP_CITE_RE =
  /([【[（(]{0,2}\s*(?:检索(?:内容|文档|资料|来源))?\s*[【[（(]?\s*(\d+)\s*[】\]）)]+)(?:\s*[【[（(]{0,2}\s*(?:检索(?:内容|文档|资料|来源))?\s*[【[（(]?\s*\2\s*[】\]）)]+)+/g;

/**
 * 引用编号映射:
 * - 证据到达后按"去重后来源"编号: 同一来源的多个引用共享同一编号, 编号数 = 证据卡片数,
 *   避免"正文 1-5、证据只有 3 张"的错位;
 * - 流式期间证据未到, 先按出现顺序临时编号, done 后由证据一次性覆盖收敛。
 */
const citeSeqMap = computed(() => {
  const map = new Map<string, number>();
  // 仅当证据带 indexes(实时消息)才按去重后来源编号; 历史回放无 indexes 时回退按出现顺序
  const hasIndexes = evidenceList.value.some((ev) => (ev.indexes ?? []).length);
  if (hasIndexes) {
    evidenceList.value.forEach((ev, p) => {
      for (const idx of ev.indexes ?? []) {
        if (idx != null && !map.has(String(idx))) map.set(String(idx), p + 1);
      }
    });
    return map;
  }
  let seq = 0;
  for (const m of props.content.matchAll(CITE_RE)) {
    const n = m[1];
    if (!map.has(n)) map.set(n, ++seq);
  }
  return map;
});

/** 证据项的来源编号(卡片角标, 与正文徽标对应) */
function citeSeq(index?: number): string {
  if (index == null) return '';
  return String(citeSeqMap.value.get(String(index)) ?? '');
}

/** 把 [检索内容N] 渲染成句子右上角的 [seq] 引用徽标(seq 按去重后来源编号) */
function renderCitations(md: string): string {
  const cleaned = md.replace(DUP_CITE_RE, '$1'); // 先折叠紧邻重复引用
  return cleaned.replace(CITE_RE, (_m, n: string) => {
    const seq = citeSeqMap.value.get(n) ?? 0;
    return `<sup class="cite-badge" data-cite="${n}" title="引用证据 ${seq}">[${seq}]</sup>`;
  });
}

// 将 Markdown 渲染为 HTML(先替换引用徽标)
const renderedHtml = computed(() => {
  if (!props.content) return '';
  return marked.parse(renderCitations(props.content)) as string;
});

/** 点击引用徽标 → 滚动并高亮对应的证据图(同一图可能被多个编号引用) */
function handleMarkdownClick(e: Event): void {
  const target = (e.target as HTMLElement).closest?.('.cite-badge') as HTMLElement | null;
  if (!target) return;
  const n = Number(target.dataset.cite);
  if (!n || !evidenceList.value.length) return;
  const pos = evidenceList.value.findIndex((ev) => (ev.indexes ?? []).includes(n));
  if (pos < 0) return;
  const root = markdownBodyRef.value?.closest('.bubble');
  const item = root?.querySelector(`[data-evidence-idx="${pos}"]`);
  item?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  item?.classList.add('flash');
  window.setTimeout(() => item?.classList.remove('flash'), 1600);
}

// 图片预览列表(ElImage 需要 string[])
const previewList = computed(() => props.images || []);

// 证据区中的图片项(文本来源不含 url, 单独筛出供 el-image 预览)
const evidenceImages = computed(() => evidenceList.value.filter((e) => e.type !== 'text'));
const evidenceImageUrls = computed(() => evidenceImages.value.map((e) => e.url ?? ''));

// 头像文字
const avatarText = computed(() => (isHuman.value ? '我' : 'AI'));

// ---- 置信章: 仅 AI 消息、已结束流式、带分数、且允许展示(管理员)时展示 ----
const showStamp = computed(
  () =>
    props.role === 'ai' &&
    !props.streaming &&
    typeof props.score === 'number' &&
    props.showConfidence,
);
const stampTone = computed(() => {
  const s = props.score ?? 0;
  if (s >= 80) return 'high';
  if (s >= 60) return 'mid';
  return 'low';
});
const stampLabel = computed(() => {
  const s = props.score ?? 0;
  if (s >= 80) return '高可信';
  if (s >= 60) return '中等';
  return '低可信';
});
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
        <div v-if="!isHuman && evidenceList.length" class="evidence-area">
          <div class="evidence-title">⚑ 引用证据</div>
          <div class="evidence-row">
            <div
              v-for="(ev, idx) in evidenceList"
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
                <el-popover
                  v-else
                  placement="bottom-start"
                  :width="360"
                  trigger="click"
                  :visible="expandedTextIdx === idx"
                  @hide="expandedTextIdx = null"
                >
                  <div class="evidence-pop">
                    <div class="evidence-pop-file">{{ ev.label || ev.filename || '文档' }}</div>
                    <div class="evidence-pop-text">{{ ev.text }}</div>
                  </div>
                  <template #reference>
                    <div class="evidence-card" @click="toggleText(idx)">
                      <div class="evidence-card-file">
                        📄 {{ ev.label || ev.filename || '文档' }}
                      </div>
                      <div v-if="ev.text" class="evidence-card-snippet">{{ ev.text }}</div>
                      <span v-if="isLongText(ev.text)" class="evidence-expand">查看全文</span>
                    </div>
                  </template>
                </el-popover>
                <span v-if="citeSeq(ev.indexes?.[0])" class="evidence-num">{{
                  citeSeq(ev.indexes?.[0])
                }}</span>
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
  min-width: 0; // 允许收缩到内容最小宽以下, 证据区换行不撑破气泡
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
    flex-wrap: wrap;
    gap: 8px;
    align-items: flex-start; // 行内项按内容高度顶部对齐, 不被行高拉伸
    min-width: 0; // 允许收缩换行, 不撑破气泡
  }

  .evidence-item {
    display: flex;
    flex-direction: column;
    gap: 3px;
    // 图片卡固定宽度
    width: 80px;
    flex-shrink: 0;

    // 文本来源卡片: 固定统一宽度, 与缩略图组成均匀网格(换行对齐, 截断一致)
    &.is-text {
      flex: 0 1 220px;
      width: 220px;
      min-width: 0;

      // 左上角编号标签占位, 避免覆盖卡片内 "📄 文件名"
      .evidence-card {
        padding-left: 24px;
      }
    }
  }

  .evidence-figure {
    position: relative;
    width: 80px;
    height: 80px;
  }

  // 文本来源卡片: figure 不设固定方框, 随卡片内容自适应(原 `.is-text &`
  // 编译成 `.is-text .evidence-area .evidence-figure`, 祖先关系颠倒永不命中)
  .evidence-item.is-text .evidence-figure {
    width: auto;
    height: auto;
    min-width: 0;
  }

  .evidence-thumb {
    width: 80px;
    height: 80px;
    border-radius: var(--radius-sm);
    border: 1px solid rgba(0, 0, 0, 0.08);
    cursor: pointer;
    object-fit: cover;
  }

  // 文本来源卡片(点击展开查看完整来源, 与图片可点击预览对齐)
  .evidence-card {
    width: 100%; // 填满固定宽文本项
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: var(--radius-sm);
    background: rgba(37, 99, 235, 0.05);
    padding: 8px 10px;
    cursor: pointer;
    transition: background 0.15s;

    &:hover {
      background: rgba(37, 99, 235, 0.08);
    }

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

    .evidence-expand {
      display: inline-block;
      margin-top: 4px;
      font-size: 11px;
      color: var(--brass);
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
    background: var(--brass);
    color: #ffffff;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    line-height: 15px;
    text-align: center;
    pointer-events: none;
  }

  .evidence-name {
    width: 100%; // 限定在缩略图列宽内, 保证单行省略号生效
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
    box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.35);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(37, 99, 235, 0);
  }
}

.bubble {
  position: relative;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
  min-width: 0; // 允许内部证据区收缩换行
}

// 用户: 品牌蓝紧凑块(企业常规形态)
.bubble-human {
  background: var(--brass);
  color: #ffffff;
  border: none;
  border-top-right-radius: 4px;
  box-shadow: var(--shadow-card);
}

// AI: 白色卡片 + 左侧蓝色证据轨
.bubble-ai {
  background: var(--paper);
  color: var(--paper-ink);
  border: 1px solid var(--hairline);
  border-left: 3px solid var(--brass);
  border-top-left-radius: 4px;
  padding-top: 34px; // 给右上置信章留位
  box-shadow: var(--shadow-card);
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
    background: rgba(37, 99, 235, 0.09);
  }
  &.stamp-mid {
    color: #b45309;
    background: rgba(217, 119, 6, 0.1);
  }
  &.stamp-low {
    color: #dc2626;
    background: rgba(220, 38, 38, 0.1);
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
      background: #f1f5f9;
      border: 1px solid var(--hairline);
      padding: 2px 6px;
      border-radius: 4px;
      font-family: var(--font-mono);
      font-size: 13px;
      color: #0f172a;
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
      background: rgba(37, 99, 235, 0.12);
      border: 1px solid rgba(37, 99, 235, 0.35);
      color: var(--brass);
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 600;
      line-height: 1;
      cursor: pointer;
      vertical-align: super;
      transition: background 0.15s;
      &:hover {
        background: rgba(37, 99, 235, 0.22);
      }
    }
    pre {
      background: #f8fafc;
      border: 1px solid var(--hairline);
      padding: 12px;
      border-radius: var(--radius-sm);
      overflow-x: auto;
      margin: 0 0 8px;
      code {
        background: transparent;
        border: none;
        padding: 0;
        color: var(--paper-ink);
      }
    }
    blockquote {
      border-left: 3px solid var(--brass);
      padding-left: 12px;
      margin: 8px 0;
      color: var(--muted);
    }
    a {
      color: var(--brass);
    }
    table {
      border-collapse: collapse;
      margin: 8px 0;
      th,
      td {
        border: 1px solid var(--hairline);
        padding: 6px 10px;
      }
      th {
        background: #f8fafc;
      }
    }
    hr {
      border: none;
      border-top: 1px solid var(--hairline);
      margin: 12px 0;
    }
  }
}

.empty-content {
  color: var(--muted);
  font-style: italic;
}
</style>

<!-- 全文浮层样式: el-popover 内容 teleport 到 body, 需全局样式 -->
<style lang="scss">
.evidence-pop {
  .evidence-pop-file {
    font-size: 12px;
    font-weight: 600;
    color: var(--ink-text);
    margin-bottom: 8px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .evidence-pop-text {
    font-size: 12px;
    line-height: 1.7;
    color: var(--ink-text);
    max-height: 280px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }
}
</style>
