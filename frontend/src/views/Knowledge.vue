<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, FolderOpened, Upload, Search, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import {
  uploadPdf,
  getJobs,
  getDocuments,
  deleteDocument,
  getKnowledgeStatus,
  getDocumentChunks,
  type IngestJob,
  type KnowledgeDoc,
  type KnowledgeStatus,
  type Chunk,
} from '@/api/knowledge'

const router = useRouter()

function back(): void {
  router.push('/chat')
}

// ============ 数据总览(顶栏紧凑版) ============
const stats = ref<KnowledgeStatus | null>(null)

async function refreshStats(): Promise<void> {
  try {
    stats.value = await getKnowledgeStatus()
  } catch {
    stats.value = null
  }
}

// ============ 上传(弹窗) ============
const MAX_UPLOAD_MB = 50
const uploadVisible = ref(false)
const uploadRef = ref<UploadInstance>()
const selectedFile = ref<File | null>(null)
const uploading = ref(false)

function handleFileChange(uploadFile: UploadFile): void {
  const raw = uploadFile.raw
  if (!raw) {
    uploadRef.value?.clearFiles()
    return
  }
  if (!raw.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.warning('仅支持 PDF 文件')
    uploadRef.value?.clearFiles()
    return
  }
  if (raw.size > MAX_UPLOAD_MB * 1024 * 1024) {
    ElMessage.warning(`文件过大, 请上传 ${MAX_UPLOAD_MB}MB 以内的 PDF`)
    uploadRef.value?.clearFiles()
    return
  }
  selectedFile.value = raw
}

function handleFileRemove(): void {
  selectedFile.value = null
}

function handleFileExceed(): void {
  ElMessage.warning('每次只能上传一个文件, 请先移除已选文件')
}

async function handleSubmit(): Promise<void> {
  if (!selectedFile.value || uploading.value) return
  uploading.value = true
  try {
    const res = await uploadPdf(selectedFile.value)
    ElMessage.success(`已提交入库: ${res.filename}`)
    uploadVisible.value = false
    selectedFile.value = null
    uploadRef.value?.clearFiles()
    page.value = 1
    await refreshJobs() // 新任务进入列表, hasActive 自动触发轮询
  } catch (err) {
    ElMessage.error(`上传失败: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    uploading.value = false
  }
}

// ============ 文档列表(左侧) ============
const docs = ref<KnowledgeDoc[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const loadingDocs = ref(false)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

async function refreshDocs(): Promise<void> {
  loadingDocs.value = true
  try {
    const res = await getDocuments(page.value, pageSize.value, keyword.value.trim())
    docs.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(`获取文档失败: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    loadingDocs.value = false
  }
}

function handleSearch(): void {
  page.value = 1
  void refreshDocs()
}

// ============ 选中文档 + chunk(右侧) ============
const selected = ref<KnowledgeDoc | null>(null)
const chunks = ref<Chunk[]>([])
const chunksLoading = ref(false)

async function selectDoc(doc: KnowledgeDoc): Promise<void> {
  selected.value = doc
  chunksLoading.value = true
  chunks.value = []
  try {
    const res = await getDocumentChunks(doc.id)
    chunks.value = res.items
  } catch (err) {
    ElMessage.error(`加载 chunk 失败: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    chunksLoading.value = false
  }
}

// ============ 入库任务(左下, 含轮询) ============
const jobs = ref<IngestJob[]>([])
const loadingJobs = ref(false)
let pollTimer: number | null = null
let wasActive = false

const hasActive = computed(() =>
  jobs.value.some((j) => j.status === 'pending' || j.status === 'running'),
)

async function refreshJobs(): Promise<void> {
  loadingJobs.value = true
  try {
    const list = await getJobs()
    jobs.value = list
    // 活跃任务全部结束时, 同步文档列表与统计
    if (wasActive && !hasActive.value) {
      await refreshDocs()
      void refreshStats()
    }
    wasActive = hasActive.value
  } catch (err) {
    ElMessage.error(`获取任务失败: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    loadingJobs.value = false
  }
}

function startPolling(): void {
  if (pollTimer) return
  pollTimer = window.setInterval(() => {
    void refreshJobs()
  }, 2000)
}

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(hasActive, (on) => {
  if (on) startPolling()
  else stopPolling()
})

// ============ 删除 ============
async function handleDelete(row: KnowledgeDoc): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.filename}」吗?其 ${row.chunk_count} 个向量与入库产物将一并删除。`,
      '删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonType: 'danger',
        customClass: 'confirm-box',
        center: true,
      },
    )
  } catch {
    return
  }
  try {
    await deleteDocument(row.id)
    ElMessage.success('文档已删除')
    if (selected.value?.id === row.id) {
      selected.value = null
      chunks.value = []
    }
    void refreshDocs()
    void refreshJobs()
    void refreshStats()
  } catch (err) {
    ElMessage.error(`删除失败: ${err instanceof Error ? err.message : String(err)}`)
  }
}

// ============ 状态映射 ============
const JOB_STATUS: Record<string, string> = {
  pending: '排队中',
  running: '处理中',
  success: '完成',
  error: '失败',
}
const DOC_STATUS: Record<string, string> = {
  ingested: '已入库',
  partial: '部分成功',
  failed: '失败',
  deleted: '已删除',
}
const CAT_LABEL: Record<string, string> = {
  text: '文本',
  image: '图片',
  table: '表格',
  memory: '记忆',
}
function jobLabel(s: string): string {
  return JOB_STATUS[s] ?? s
}
function docLabel(s: string): string {
  return DOC_STATUS[s] ?? s
}
function catLabel(s: string): string {
  return CAT_LABEL[s] ?? s
}
function milvusStatus(): string {
  if (!stats.value) return '–'
  return stats.value.status === 'ok' ? '正常' : '降级'
}

onMounted(() => {
  void refreshStats()
  void refreshJobs()
  void refreshDocs()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div class="knowledge-page">
    <header class="kb-header">
      <el-button text :icon="ArrowLeft" @click="back">返回聊天</el-button>
      <h2 class="kb-title">
        <el-icon><FolderOpened /></el-icon>
        知识库管理
      </h2>
      <div class="kb-stats">
        <span class="kb-stat"><b>{{ stats ? stats.total_documents : '–' }}</b><i>文档</i></span>
        <span class="kb-stat"><b>{{ stats ? stats.total_vectors : '–' }}</b><i>向量</i></span>
        <span class="kb-stat milvus" :class="stats?.status === 'ok' ? 'ok' : 'warn'">
          <span class="dot" :class="stats?.status === 'ok' ? 'dot-ok' : 'dot-warn'"></span>
          {{ milvusStatus() }}
        </span>
      </div>
    </header>

    <div class="kb-body">
      <!-- 左栏: 上传 + 文档列表 + 最近任务 -->
      <aside class="kb-sidebar">
        <el-button type="primary" class="upload-btn" :icon="Upload" @click="uploadVisible = true">
          上传文档
        </el-button>

        <el-input
          v-model="keyword"
          placeholder="搜索文件名"
          clearable
          size="small"
          class="search-input"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <div class="sidebar-label">文档列表</div>
        <div class="doc-list">
          <div v-if="loadingDocs" class="list-empty">加载中...</div>
          <template v-else>
            <div
              v-for="doc in docs"
              :key="doc.id"
              class="doc-item"
              :class="{ active: selected?.id === doc.id }"
              @click="selectDoc(doc)"
            >
              <div class="doc-name">{{ doc.filename }}</div>
              <div class="doc-meta">{{ doc.chunk_count }} chunk · {{ docLabel(doc.status) }}</div>
            </div>
            <div v-if="docs.length === 0" class="list-empty">暂无文档, 点击上方上传</div>
          </template>
        </div>

        <div class="sidebar-pager" v-if="total > 0">
          <el-button text size="small" :disabled="page <= 1" @click="page--; refreshDocs()">‹</el-button>
          <span class="pager-info">{{ page }} / {{ pageCount }}</span>
          <el-button text size="small" :disabled="page >= pageCount" @click="page++; refreshDocs()">›</el-button>
        </div>

        <div class="sidebar-label sidebar-label-task">最近任务</div>
        <div class="task-list">
          <div v-for="job in jobs" :key="job.id" class="task-item" :title="job.error || ''">
            <span class="status-dot" :class="`dot-${job.status}`"></span>
            <span class="task-name">{{ job.filename }}</span>
            <span class="task-stage" :class="{ err: job.status === 'error' }">
              {{ job.status === 'running' ? job.stage : jobLabel(job.status) }}
            </span>
          </div>
          <div v-if="jobs.length === 0" class="list-empty">暂无任务</div>
        </div>
      </aside>

      <!-- 右栏: 选中文档的 chunk 详情 -->
      <main class="kb-main">
        <div v-if="!selected" class="kb-placeholder">
          <el-empty description="从左侧选择文档, 查看它的 chunk 详情">
            <el-button type="primary" @click="uploadVisible = true">上传文档</el-button>
          </el-empty>
        </div>

        <div v-else class="doc-detail">
          <div class="doc-detail-head">
            <div class="doc-title">
              <span class="section-mark"></span>
              {{ selected.filename }}
            </div>
            <div class="doc-tags">
              <span class="status" :class="`status-${selected.status}`">
                <span class="status-dot"></span>{{ docLabel(selected.status) }}
              </span>
              <span class="tag-chip">{{ selected.chunk_count }} chunk</span>
              <span class="tag-chip">{{ selected.image_count }} 图片</span>
              <span class="doc-time">{{ selected.created_at }}</span>
              <el-button text type="danger" size="small" class="doc-delete" @click="handleDelete(selected)">
                删除文档
              </el-button>
            </div>
          </div>

          <div class="chunk-list">
            <div v-if="chunksLoading" class="list-empty">加载中...</div>
            <div v-else-if="chunks.length === 0" class="list-empty">
              该文档没有 chunk(可能是无元数据的历史数据)
            </div>
            <div v-else>
              <div v-for="(c, i) in chunks" :key="c.id" class="chunk-item">
                <div class="chunk-head">
                  <span class="chunk-index">#{{ i + 1 }}</span>
                  <span class="chunk-cat" :class="`cat-${c.category}`">{{ catLabel(c.category) }}</span>
                  <span v-if="c.title" class="chunk-title">{{ c.title }}</span>
                </div>
                <div v-if="c.category === 'image' && c.url" class="chunk-image">
                  <el-image :src="c.url" :preview-src-list="[c.url]" fit="contain" />
                </div>
                <pre v-else class="chunk-text">{{ c.text }}</pre>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>

    <!-- 上传弹窗 -->
    <el-dialog v-model="uploadVisible" title="上传文档" width="480px" :close-on-click-modal="false">
      <el-upload
        ref="uploadRef"
        drag
        accept=".pdf"
        :auto-upload="false"
        :limit="1"
        :on-change="handleFileChange"
        :on-remove="handleFileRemove"
        :on-exceed="handleFileExceed"
      >
        <el-icon class="upload-icon"><Upload /></el-icon>
        <div class="upload-text">拖拽 PDF 到此处, 或 <em>点击选择文件</em></div>
        <template #tip>
          <div class="upload-tip">仅支持 PDF · 提交后后台自动执行 OCR → 分片 → 向量化 → 入库</div>
        </template>
      </el-upload>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!selectedFile || uploading"
          :loading="uploading"
          @click="handleSubmit"
        >
          {{ uploading ? '入库中...' : '开始入库' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.knowledge-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--ink);
}

.kb-header {
  height: 48px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--hairline);
}

.kb-title {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  color: var(--ink-text);
  display: flex;
  align-items: center;
  gap: 8px;
}

.kb-stats {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 18px;
}

.kb-stat {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);

  b {
    font-size: 15px;
    color: var(--ink-text);
    margin-right: 4px;
  }

  i {
    font-style: normal;
  }
}

.kb-stat.milvus.ok {
  color: var(--success);
}

.kb-stat.milvus.warn {
  color: var(--warning);
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 5px;
  vertical-align: middle;
}

.dot-ok {
  background: var(--success);
}

.dot-warn {
  background: var(--warning);
}

// ---- 双栏主体 ----
.kb-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.kb-sidebar {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  background: var(--surface);
  border-right: 1px solid var(--hairline);
  overflow-y: auto;
}

.kb-main {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}

.upload-btn {
  width: 100%;
}

.search-input {
  width: 100%;
}

.sidebar-label {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.4px;
  color: var(--muted);
  text-transform: uppercase;
  margin-top: 4px;
}

.sidebar-label-task {
  border-top: 1px solid var(--hairline);
  padding-top: 10px;
  margin-top: auto;
}

.doc-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  min-height: 120px;
}

.doc-item {
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s;

  &:hover {
    background: var(--surface-2);
  }

  &.active {
    background: var(--brass-soft);
    border-color: rgba(194, 154, 59, 0.35);
  }
}

.doc-name {
  font-size: 13px;
  color: var(--ink-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}

.sidebar-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.pager-info {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
}

.task-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 140px;
  overflow-y: auto;
}

.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  font-size: 12px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-pending {
  background: var(--warning);
}

.dot-running {
  background: var(--brass);
  animation: pulse 1.2s ease-in-out infinite;
}

.dot-success {
  background: var(--success);
}

.dot-error {
  background: var(--danger);
}

.task-name {
  color: var(--ink-text);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-stage {
  color: var(--muted);
  font-size: 11px;

  &.err {
    color: var(--danger);
  }
}

// ---- 右栏详情 ----
.kb-placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.doc-detail-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--hairline);
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.doc-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-display);
  font-size: 17px;
  font-weight: 600;
  color: var(--ink-text);
}

.section-mark {
  width: 3px;
  height: 16px;
  border-radius: 2px;
  background: var(--brass);
}

.doc-tags {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--muted);
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.status-ingested .status-dot {
  background: var(--success);
}

.status-partial .status-dot {
  background: var(--warning);
}

.status-failed .status-dot {
  background: var(--danger);
}

.status-deleted .status-dot {
  background: var(--muted);
}

.tag-chip {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 7px;
  border: 1px solid var(--hairline);
  border-radius: 3px;
  color: var(--muted);
}

.doc-time {
  font-family: var(--font-mono);
  font-size: 11px;
}

.doc-delete {
  margin-left: auto;
}

// ---- chunk 列表 ----
.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chunk-item {
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}

.chunk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.chunk-index {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--brass);
}

.chunk-cat {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--hairline);
  color: var(--muted);
}

.cat-image {
  color: var(--brass);
  border-color: var(--brass-soft);
}

.chunk-title {
  font-size: 12px;
  color: var(--ink-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-image {
  margin-top: 4px;

  :deep(.el-image) {
    max-height: 240px;
    border-radius: var(--radius-sm);
    background: #000;
  }
}

.chunk-text {
  margin: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-text);
  white-space: pre-wrap;
  word-break: break-word;
}

.list-empty {
  padding: 16px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
</style>
