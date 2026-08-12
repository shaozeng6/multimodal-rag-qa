<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { ArrowLeft, FolderOpened, Upload, Search } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { UploadFile, UploadInstance } from 'element-plus';
import Settings from '@/views/Settings.vue';
import UserManagement from '@/views/UserManagement.vue';
import {
  uploadPdf,
  getDocuments,
  deleteDocument,
  getKnowledgeStatus,
  getDocumentChunks,
  retryJob,
  cancelJob,
  pauseJob,
  resumeJob,
  deleteJob,
  startParse,
  startIndex,
  type UploadEntry,
  type KnowledgeStatus,
  type Chunk,
} from '@/api/knowledge';

const router = useRouter();
const route = useRoute();

function back(): void {
  router.push('/chat');
}

// ============ 工作台 Tab(知识库 / 系统设置 / 用户管理), URL query 驱动 ============
const activeTab = computed(() => {
  const t = route.query.tab;
  return t === 'settings' || t === 'users' ? t : 'kb';
});

function handleTabChange(tab: string): void {
  router.replace({
    path: '/knowledge',
    query: tab === 'kb' ? {} : { tab },
  });
}

// ============ 数据总览(顶栏) ============
const stats = ref<KnowledgeStatus | null>(null);

async function refreshStats(): Promise<void> {
  try {
    stats.value = await getKnowledgeStatus();
  } catch {
    stats.value = null;
  }
}

// ============ 上传(弹窗) ============
const MAX_UPLOAD_MB = 50;
const uploadVisible = ref(false);
const uploadRef = ref<UploadInstance>();
const selectedFile = ref<File | null>(null);
const uploading = ref(false);
/** 上传后先不处理: 停在首阶段, 在列表里点「继续」再处理 */
const parkOnUpload = ref(false);

function handleFileChange(uploadFile: UploadFile): void {
  const raw = uploadFile.raw;
  if (!raw) {
    uploadRef.value?.clearFiles();
    return;
  }
  if (!raw.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.warning('仅支持 PDF 文件');
    uploadRef.value?.clearFiles();
    return;
  }
  if (raw.size > MAX_UPLOAD_MB * 1024 * 1024) {
    ElMessage.warning(`文件过大, 请上传 ${MAX_UPLOAD_MB}MB 以内的 PDF`);
    uploadRef.value?.clearFiles();
    return;
  }
  selectedFile.value = raw;
}

function handleFileRemove(): void {
  selectedFile.value = null;
}

function handleFileExceed(): void {
  ElMessage.warning('每次只能上传一个文件, 请先移除已选文件');
}

async function handleSubmit(): Promise<void> {
  if (!selectedFile.value || uploading.value) return;
  uploading.value = true;
  try {
    const res = await uploadPdf(selectedFile.value, parkOnUpload.value ? 'manual' : 'auto');
    ElMessage.success(
      parkOnUpload.value ? `已上传, 稍后处理: ${res.filename}` : `已提交入库: ${res.filename}`,
    );
    uploadVisible.value = false;
    selectedFile.value = null;
    uploadRef.value?.clearFiles();
    page.value = 1;
    await refreshDocs(); // 新上传立即出现在文件列表(进行中任务行)
  } catch (err) {
    ElMessage.error(`上传失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    uploading.value = false;
  }
}

// ============ 文件列表(已入库文档 + 进行中任务) ============
const docs = ref<UploadEntry[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);
const keyword = ref('');
const docStatus = ref('');
const loadingDocs = ref(false);
let pollTimer: number | null = null;
let wasActive = false;

/** 有 running 任务则持续轮询(进度)。手动停驻(pending)不持续轮询, 避免刷屏。 */
const hasActive = computed(() =>
  docs.value.some((e) => e.kind === 'job' && e.status === 'running'),
);
/** 用户触发 解析/入库/重试 后, 强制轮询 N 次捕获 pending→running→settled 的状态转换 */
let forcePollCount = 0;

function shouldPoll(): boolean {
  return hasActive.value || forcePollCount > 0;
}

async function refreshDocs(): Promise<void> {
  loadingDocs.value = true;
  try {
    const res = await getDocuments(
      page.value,
      pageSize.value,
      keyword.value.trim(),
      docStatus.value,
    );
    docs.value = res.items;
    total.value = res.total;
    // 有任务从进行中变为结束 → 刷新统计
    if (wasActive && !hasActive.value) {
      void refreshStats();
    }
    wasActive = hasActive.value;
  } catch (err) {
    ElMessage.error(`获取文件列表失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    loadingDocs.value = false;
    if (forcePollCount > 0) forcePollCount--;
    if (shouldPoll()) startPolling();
    else stopPolling();
  }
}

function handleSearch(): void {
  page.value = 1;
  void refreshDocs();
}

function handleDocFilter(): void {
  page.value = 1;
  void refreshDocs();
}

function onPageChange(p: number): void {
  page.value = p;
  void refreshDocs();
}

function startPolling(): void {
  if (pollTimer) return;
  pollTimer = window.setInterval(() => {
    void refreshDocs();
  }, 2000);
}

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

/** 字节数格式化为可读文本 */
function formatBytes(bytes?: number): string {
  if (!bytes) return '–';
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)}MB`;
}

// ============ 已入库文档: 查看 chunk / 删除 ============
const selected = ref<UploadEntry | null>(null);
const chunks = ref<Chunk[]>([]);
const chunksLoading = ref(false);
const chunkDrawerVisible = ref(false);

async function selectDoc(doc: UploadEntry): Promise<void> {
  if (doc.kind !== 'doc' || typeof doc.id !== 'number') return;
  selected.value = doc;
  chunkDrawerVisible.value = true;
  chunksLoading.value = true;
  chunks.value = [];
  try {
    const res = await getDocumentChunks(doc.id);
    chunks.value = res.items;
  } catch (err) {
    ElMessage.error(`加载 chunk 失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    chunksLoading.value = false;
  }
}

async function handleDelete(row: UploadEntry): Promise<void> {
  if (row.kind !== 'doc' || typeof row.id !== 'number') return;
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.filename}」吗?其 ${row.chunk_count ?? 0} 个向量与入库产物将一并删除。`,
      '删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonType: 'danger',
        customClass: 'confirm-box',
        center: true,
      },
    );
  } catch {
    return;
  }
  try {
    await deleteDocument(row.id);
    ElMessage.success('文档已删除');
    if (selected.value?.id === row.id) {
      selected.value = null;
      chunks.value = [];
      chunkDrawerVisible.value = false;
    }
    void refreshDocs();
    void refreshStats();
  } catch (err) {
    ElMessage.error(`删除失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

// ============ 任务详情抽屉: 步骤/进度/操作/日志 单独展示 ============
const taskDetailVisible = ref(false);
const taskDetailId = ref<string | null>(null);
/** 详情行实时取 docs(轮询刷新后按钮/进度随之更新), 任务结束(转文档)则为 null */
const taskDetail = computed<UploadEntry | null>(
  () => docs.value.find((e) => e.kind === 'job' && String(e.id) === taskDetailId.value) || null,
);

function openTaskDetail(row: UploadEntry): void {
  taskDetailId.value = String(row.id);
  taskDetailVisible.value = true;
}

/** 手动触发解析段 */
async function handleStartParse(row: UploadEntry): Promise<void> {
  try {
    await startParse(String(row.id));
    forcePollCount = 8; // 触发后强制轮询, 捕获 pending→running→待入库 转换
    ElMessage.success('已触发解析(OCR/分片)');
    void refreshDocs();
  } catch (err) {
    ElMessage.error(`启动解析失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/** 手动触导入库段 */
async function handleStartIndex(row: UploadEntry): Promise<void> {
  try {
    await startIndex(String(row.id));
    forcePollCount = 8;
    ElMessage.success('已触导入库(描述/向量化/写入)');
    void refreshDocs();
  } catch (err) {
    ElMessage.error(`启导入库失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function handlePause(row: UploadEntry): Promise<void> {
  try {
    await pauseJob(String(row.id));
    ElMessage.info('已发送暂停请求, 本阶段结束后暂停');
    void refreshDocs();
  } catch (err) {
    ElMessage.error(`暂停失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function handleResume(row: UploadEntry): Promise<void> {
  try {
    await resumeJob(String(row.id));
    ElMessage.success(`已继续: ${row.filename}`);
    void refreshDocs();
  } catch (err) {
    ElMessage.error(`继续失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function handleCancel(row: UploadEntry): Promise<void> {
  try {
    await cancelJob(String(row.id));
    ElMessage.info('已发送取消请求, 当前阶段结束后生效');
    void refreshDocs();
  } catch (err) {
    ElMessage.error(`取消失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function handleRetry(row: UploadEntry): Promise<void> {
  try {
    await retryJob(String(row.id));
    forcePollCount = 8;
    ElMessage.success(`已重新入队: ${row.filename}`);
    void refreshDocs();
  } catch (err) {
    ElMessage.error(`重试失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function handleDeleteJob(row: UploadEntry): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除任务「${row.filename}」吗?其记录与中间产物(OCR 目录/临时 PDF)将一并清除。`,
      '删除任务',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonType: 'danger',
        customClass: 'confirm-box',
        center: true,
      },
    );
  } catch {
    return;
  }
  try {
    await deleteJob(String(row.id));
    ElMessage.success('任务已删除');
    void refreshDocs();
  } catch (err) {
    ElMessage.error(`删除失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

// ============ 管道步骤条(上传→OCR→分片→描述→向量化→入库) ============
const PIPELINE_STEPS = [
  { key: 'upload', label: '上传' },
  { key: 'ocr', label: 'OCR' },
  { key: 'split', label: '分片' },
  { key: 'describe', label: '描述' },
  { key: 'embed', label: '向量化' },
  { key: 'store', label: '入库' },
];
/** job.stage 名 → 步骤下标(1-5) */
const STAGE_INDEX: Record<string, number> = {
  'OCR识别(vllm)': 1,
  分片: 2,
  '生成图片/表格描述': 3,
  向量化: 4,
  '写入 Milvus': 5,
};

/** 由 job.phase + stage + status 推导每个步骤状态: done/active/pending/fail/cancel */
function stageSteps(row: UploadEntry): { label: string; state: string }[] {
  const steps = PIPELINE_STEPS.map((s) => ({ label: s.label, state: 'pending' as string }));
  steps[0].state = 'done'; // 上传始终已完成(任务已存在)
  // 手动: 解析完成、等待入库 → OCR/分片已 done, 索引段待执行
  if (row.phase === 'parsed' && row.status === 'pending') {
    steps[1].state = 'done';
    steps[2].state = 'done';
    return steps;
  }
  const at = STAGE_INDEX[row.stage || ''];
  if (row.status === 'success') {
    return steps.map((s) => ({ ...s, state: 'done' }));
  }
  if (row.status === 'error' || row.status === 'cancelled') {
    const i = at ?? 1;
    for (let s = 1; s < steps.length; s++) {
      if (s < i) steps[s].state = 'done';
      else if (s === i) steps[s].state = row.status === 'error' ? 'fail' : 'cancel';
    }
    return steps;
  }
  const i = at ?? 1;
  for (let s = 1; s < steps.length; s++) {
    if (s < i) steps[s].state = 'done';
    else if (s === i) steps[s].state = 'active';
  }
  return steps;
}

/** 任务状态中文(含手动停驻态: 待解析/待入库) */
function jobStatusLabel(row: UploadEntry): string {
  if (row.phase === 'parse' && row.status === 'pending') return '待解析';
  if (row.phase === 'parsed' && row.status === 'pending') return '待入库';
  if (row.status === 'running') return row.phase === 'index' ? '索引中' : '解析中';
  return jobLabel(row.status);
}

// ============ 状态映射 ============
const JOB_STATUS: Record<string, string> = {
  pending: '排队中',
  running: '处理中',
  success: '完成',
  error: '失败',
  cancelled: '已取消',
};
const DOC_STATUS: Record<string, string> = {
  ingested: '已入库',
  partial: '部分成功',
  failed: '失败',
  deleted: '已删除',
};
/** 统一状态筛选(同时覆盖任务/文档状态) */
const FILTER_OPTIONS = [
  { label: '处理中', value: 'running,pending' },
  { label: '已入库', value: 'ingested' },
  { label: '失败', value: 'error,failed' },
  { label: '已取消', value: 'cancelled' },
  { label: '已删除', value: 'deleted' },
];
const CAT_LABEL: Record<string, string> = {
  text: '文本',
  image: '图片',
  table: '表格',
  memory: '记忆',
};
function jobLabel(s: string): string {
  return JOB_STATUS[s] ?? s;
}
function docLabel(s: string): string {
  return DOC_STATUS[s] ?? s;
}
function catLabel(s: string): string {
  return CAT_LABEL[s] ?? s;
}
function milvusStatus(): string {
  if (!stats.value) return '–';
  return stats.value.status === 'ok' ? '正常' : '降级';
}

onMounted(() => {
  void refreshStats();
  void refreshDocs();
});

onUnmounted(() => {
  stopPolling();
});
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
        <span class="kb-stat"
          ><b>{{ stats ? stats.total_documents : '–' }}</b
          ><i>文档</i></span
        >
        <span class="kb-stat"
          ><b>{{ stats ? stats.total_vectors : '–' }}</b
          ><i>向量</i></span
        >
        <span class="kb-stat"
          ><b>{{ stats ? formatBytes(stats.total_chars) : '–' }}</b
          ><i>字符</i></span
        >
        <span class="kb-stat" :class="stats && stats.failed_jobs ? 'warn' : ''">
          <b>{{ stats ? (stats.failed_jobs ?? 0) : '–' }}</b
          ><i>失败任务</i>
        </span>
        <span class="kb-stat milvus" :class="stats?.status === 'ok' ? 'ok' : 'warn'">
          <span class="dot" :class="stats?.status === 'ok' ? 'dot-ok' : 'dot-warn'"></span>
          {{ milvusStatus() }}
        </span>
      </div>
    </header>

    <el-tabs :model-value="activeTab" class="kb-tabs" @update:model-value="handleTabChange">
      <el-tab-pane label="知识库" name="kb">
        <!-- 工具条 -->
        <div class="kb-toolbar">
          <div class="toolbar-left">
            <el-button type="primary" :icon="Upload" @click="uploadVisible = true"
              >上传文档</el-button
            >
          </div>
          <div class="toolbar-right">
            <el-input
              v-model="keyword"
              placeholder="搜索文件名"
              clearable
              class="search-input"
              @keyup.enter="handleSearch"
              @clear="handleSearch"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <el-select
              v-model="docStatus"
              placeholder="状态"
              clearable
              class="status-select"
              @change="handleDocFilter"
            >
              <el-option
                v-for="opt in FILTER_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>
        </div>

        <!-- 文件列表: 已入库文档 + 进行中/失败/已取消任务 -->
        <div class="kb-table-wrap" v-loading="loadingDocs">
          <el-table
            :data="docs"
            highlight-current-row
            @row-click="
              (row: UploadEntry) => {
                if (row.kind === 'doc') selectDoc(row);
              }
            "
            empty-text="暂无文件, 点击「上传文档」入库"
          >
            <el-table-column label="文件名" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="doc-name-cell">{{ row.filename }}</span>
                <span v-if="row.kind === 'job' && row.run_mode === 'manual'" class="mode-tag"
                  >先不处理</span
                >
              </template>
            </el-table-column>

            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <span v-if="row.kind === 'doc'" class="status-pill" :class="`pill-${row.status}`">
                  {{ docLabel(row.status) }}
                </span>
                <span v-else class="job-pill" :class="`jp-${row.status}`">
                  <span class="jp-dot"></span>{{ jobStatusLabel(row) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column width="220">
              <template #header>
                <span
                  class="col-header"
                  title="解析(上传→OCR→分片)→索引(描述→向量化→入库)。解析产物落盘可复用。各阶段边界可暂停/继续。点「详情」查看完整步骤。"
                  >进度</span
                >
              </template>
              <template #default="{ row }">
                <template v-if="row.kind === 'job'">
                  <div class="table-progress">
                    <el-progress :percentage="row.progress || 0" :stroke-width="5" />
                    <div class="table-stage-text" :class="{ err: row.status === 'error' }">
                      <span v-if="row.paused" class="stage-paused">已暂停 · </span>
                      {{ row.stage_detail || row.stage || jobStatusLabel(row) }}
                    </div>
                  </div>
                </template>
                <span v-else class="muted-text">—</span>
              </template>
            </el-table-column>

            <el-table-column label="chunk" width="80" sortable align="right">
              <template #default="{ row }">{{
                row.kind === 'doc' ? (row.chunk_count ?? 0) : '—'
              }}</template>
            </el-table-column>
            <el-table-column label="图片" width="70" sortable align="right">
              <template #default="{ row }">{{
                row.kind === 'doc' ? (row.image_count ?? 0) : '—'
              }}</template>
            </el-table-column>
            <el-table-column label="字符" width="100" sortable align="right">
              <template #default="{ row }">{{
                row.kind === 'doc' ? formatBytes(row.char_count) : '—'
              }}</template>
            </el-table-column>
            <el-table-column label="大小" width="100" sortable align="right">
              <template #default="{ row }">{{
                row.kind === 'doc' ? formatBytes(row.file_size) : '—'
              }}</template>
            </el-table-column>
            <el-table-column label="上传人" width="100">
              <template #default="{ row }">{{
                row.kind === 'doc' ? row.uploader_name || '—' : '—'
              }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="150" sortable />
            <el-table-column label="操作" width="190" fixed="right">
              <template #default="{ row }">
                <template v-if="row.kind === 'doc'">
                  <el-button text size="small" @click="selectDoc(row)">查看</el-button>
                  <el-button text type="danger" size="small" @click="handleDelete(row)"
                    >删除</el-button
                  >
                </template>
                <template v-else>
                  <el-button text size="small" @click.stop="openTaskDetail(row)">详情</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>

          <div class="table-pager">
            <el-pagination
              layout="total, prev, pager, next"
              :total="total"
              :page-size="pageSize"
              :current-page="page"
              background
              @current-change="onPageChange"
            />
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="系统设置" name="settings">
        <div class="settings-pane">
          <Settings />
        </div>
      </el-tab-pane>

      <el-tab-pane label="用户管理" name="users">
        <div class="settings-pane">
          <UserManagement />
        </div>
      </el-tab-pane>
    </el-tabs>

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
          <div class="mode-row">
            <el-checkbox v-model="parkOnUpload">手动控制(分步)</el-checkbox>
            <span class="mode-hint">上传后停在列表, 分别点「解析」「入库」两段执行</span>
          </div>
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

    <!-- chunk 详情抽屉(仅已入库文档) -->
    <el-drawer
      v-model="chunkDrawerVisible"
      :title="`chunk 详情 · ${selected?.filename || ''}`"
      size="560px"
    >
      <div v-if="selected" class="doc-summary">
        <span class="status-pill" :class="`pill-${selected.status}`">{{
          docLabel(selected.status)
        }}</span>
        <span class="sum-chip">{{ selected.chunk_count ?? 0 }} chunk</span>
        <span class="sum-chip">{{ selected.image_count ?? 0 }} 图片</span>
        <span class="sum-chip">{{ formatBytes(selected.char_count) }} 字符</span>
        <span class="sum-chip">{{ formatBytes(selected.file_size) }}</span>
        <span class="sum-time">{{ selected.created_at }}</span>
        <el-button
          text
          type="danger"
          size="small"
          class="doc-delete"
          @click="handleDelete(selected)"
        >
          删除文档
        </el-button>
      </div>

      <div class="chunk-list" v-loading="chunksLoading">
        <div v-if="chunksLoading" class="list-empty">加载中...</div>
        <div v-else-if="chunks.length === 0" class="list-empty">
          该文档没有 chunk(可能是无元数据的历史数据)
        </div>
        <template v-else>
          <div v-for="(c, i) in chunks" :key="c.id" class="chunk-item">
            <div class="chunk-head">
              <span class="chunk-index">#{{ i + 1 }}</span>
              <span class="chunk-cat" :class="`cat-${c.category}`">{{ catLabel(c.category) }}</span>
              <span v-if="c.title" class="chunk-title">{{ c.title }}</span>
              <span class="chunk-chars">{{ c.char_count ?? '–' }} 字符</span>
            </div>
            <div v-if="c.category === 'image' && c.url" class="chunk-image">
              <el-image :src="c.url" :preview-src-list="[c.url]" fit="contain" />
            </div>
            <pre v-else class="chunk-text">{{ c.text }}</pre>
          </div>
        </template>
      </div>
    </el-drawer>

    <!-- 任务详情抽屉: 步骤/进度/操作/日志 单独展示 -->
    <el-drawer
      v-model="taskDetailVisible"
      :title="`任务详情 · ${taskDetail?.filename || ''}`"
      size="480px"
    >
      <template v-if="taskDetail">
        <div class="td-head">
          <span class="job-pill" :class="`jp-${taskDetail.status}`">
            <span class="jp-dot"></span>{{ jobStatusLabel(taskDetail) }}
          </span>
          <span class="phase-tag" :class="taskDetail.phase === 'index' ? 'ph-index' : 'ph-parse'">
            {{ taskDetail.phase === 'index' ? '索引' : '解析' }}
          </span>
          <el-tag v-if="taskDetail.run_mode === 'manual'" size="small" type="info">手动</el-tag>
          <span class="td-time">{{ taskDetail.created_at }}</span>
        </div>

        <!-- 两段步骤: 解析(上传/OCR/分片) ‖ 索引(描述/向量化/入库) -->
        <div class="td-phases">
          <div class="td-phase">
            <span class="td-phase-name">解析</span>
            <div class="td-steps">
              <template v-for="(st, i) in stageSteps(taskDetail).slice(0, 3)" :key="st.label">
                <span v-if="i > 0" class="td-arrow">→</span>
                <span class="td-step" :class="`ts-${st.state}`">{{ st.label }}</span>
              </template>
            </div>
          </div>
          <div class="td-phase">
            <span class="td-phase-name">索引</span>
            <div class="td-steps">
              <template v-for="(st, i) in stageSteps(taskDetail).slice(3)" :key="st.label">
                <span v-if="i > 0" class="td-arrow">→</span>
                <span class="td-step" :class="`ts-${st.state}`">{{ st.label }}</span>
              </template>
            </div>
          </div>
        </div>

        <!-- 进度 -->
        <div class="td-progress">
          <el-progress :percentage="taskDetail.progress || 0" :stroke-width="6" />
          <div class="td-stage" :class="{ err: taskDetail.status === 'error' }">
            <span v-if="taskDetail.paused" class="stage-paused">已暂停 · </span>
            {{ taskDetail.stage_detail || taskDetail.stage }}
          </div>
          <div v-if="taskDetail.status === 'error'" class="td-err">{{ taskDetail.error }}</div>
        </div>

        <!-- 操作: 主操作高亮, 取消/重试/删除 分开 -->
        <div class="td-actions">
          <el-button
            v-if="taskDetail.phase === 'parse' && taskDetail.status === 'pending'"
            type="primary"
            @click="handleStartParse(taskDetail)"
            >开始解析(OCR/分片)</el-button
          >
          <el-button
            v-else-if="taskDetail.phase === 'parsed' && taskDetail.status === 'pending'"
            type="primary"
            @click="handleStartIndex(taskDetail)"
            >开始入库(描述/向量化/写入)</el-button
          >
          <el-button v-if="taskDetail.paused" type="primary" @click="handleResume(taskDetail)"
            >继续</el-button
          >
          <el-button v-else-if="taskDetail.status === 'running'" @click="handlePause(taskDetail)"
            >暂停</el-button
          >
          <el-button
            v-if="taskDetail.status === 'running' || taskDetail.status === 'pending'"
            type="warning"
            @click="handleCancel(taskDetail)"
            >取消</el-button
          >
          <el-button
            v-if="taskDetail.status === 'error'"
            type="danger"
            @click="handleRetry(taskDetail)"
            >重试</el-button
          >
          <el-button
            v-if="taskDetail.status === 'error' || taskDetail.status === 'cancelled'"
            type="danger"
            plain
            @click="handleDeleteJob(taskDetail)"
            >删除任务</el-button
          >
        </div>
        <p v-if="taskDetail.status === 'running' && !taskDetail.paused" class="td-hint">
          暂停将在当前阶段结束后生效; 向量化阶段可逐条暂停
        </p>

        <!-- 执行日志 -->
        <div class="td-log">
          <div class="td-log-title">执行日志</div>
          <pre class="log-body">{{ (taskDetail.log || []).join('\n') || '(暂无日志)' }}</pre>
        </div>
      </template>

      <div v-else class="list-empty">任务已结束(可能已完成入库), 可在文件列表查看结果</div>
    </el-drawer>
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

.kb-stat.warn b {
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

// ---- 工作台 Tab ----
.kb-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;

  :deep(.el-tabs__header) {
    margin: 0;
    padding: 0 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--hairline);
  }

  :deep(.el-tabs__nav-wrap)::after {
    display: none;
  }

  :deep(.el-tabs__item) {
    font-family: var(--font-display);
    font-size: 14px;
    height: 46px;
    line-height: 46px;
    padding: 0 20px;
    color: var(--muted);
    transition: color 0.15s;

    &:hover {
      color: var(--ink-text);
    }

    &.is-active {
      color: var(--brass-hover);
    }
  }

  :deep(.el-tabs__active-bar) {
    background: var(--brass);
    height: 2px;
  }

  :deep(.el-tabs__content) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  :deep(.el-tab-pane) {
    height: 100%;
    display: flex;
    flex-direction: column;
  }
}

.settings-pane {
  flex: 1;
  min-height: 0;
}

// ---- 工具条 ----
.kb-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--hairline);
  flex-shrink: 0;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-input {
  width: 240px;
}

.status-select {
  width: 120px;
}

// ---- 文件列表表格 ----
.kb-table-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.kb-table-wrap :deep(.el-table) {
  --el-table-border-color: var(--hairline);
  --el-table-header-bg-color: #f8fafc;
  --el-table-header-text-color: var(--muted);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.kb-table-wrap :deep(.el-table__row) {
  cursor: pointer;
}

.doc-name-cell {
  font-weight: 500;
  color: var(--ink-text);
}

.col-header {
  cursor: help;
}

.muted-text {
  color: var(--muted);
}

// ---- 状态胶囊 ----
.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
}

.pill-ingested {
  color: #166534;
  background: rgba(22, 101, 52, 0.1);
}

.pill-partial {
  color: #92400e;
  background: rgba(217, 119, 6, 0.12);
}

.pill-failed {
  color: #b91c1c;
  background: rgba(220, 38, 38, 0.1);
}

.pill-deleted {
  color: #64748b;
  background: rgba(100, 116, 139, 0.12);
}

// ---- 任务行: 状态圆点 + 进度 + 步骤点 ----
.job-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--muted);

  .jp-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
  }
}

.jp-running {
  color: var(--brass);

  .jp-dot {
    animation: pulse 1.2s ease-in-out infinite;
  }
}

.jp-pending {
  color: var(--warning);
}

.jp-error {
  color: var(--danger);
}

.jp-cancelled {
  color: var(--muted);
}

// ---- 表格进度: 进度条 + 阶段文案 ----
.table-progress {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.table-stage-text {
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &.err {
    color: var(--danger);
  }
}

.phase-tag {
  font-size: 10px;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 4px;
  flex-shrink: 0;
}

.ph-parse {
  color: var(--muted);
  background: var(--surface-2);
}

.ph-index {
  color: var(--brass);
  background: var(--brass-soft);
}

.stage-paused {
  color: var(--warning);
  font-weight: 600;
}

.mode-tag {
  display: inline-block;
  font-size: 10px;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--brass-soft);
  color: var(--brass);
  margin-left: 6px;
  vertical-align: 1px;
}

.table-pager {
  display: flex;
  justify-content: flex-end;
  flex-shrink: 0;
}

// ---- chunk 抽屉 ----
.doc-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--hairline);
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.sum-chip {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 8px;
  border: 1px solid var(--hairline);
  border-radius: 3px;
  color: var(--muted);
}

.sum-time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted);
}

.doc-delete {
  margin-left: auto;
}

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

.chunk-chars {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted);
  flex-shrink: 0;
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

// ---- 上传弹窗 ----
.mode-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.mode-hint {
  font-size: 12px;
  color: var(--muted);
}

// ---- 日志抽屉 ----
.log-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--ink-text);
  margin-bottom: 12px;
}

.log-err {
  margin-left: 8px;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-body {
  margin: 0;
  padding: 12px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 70vh;
  overflow-y: auto;
}

// ---- 任务详情抽屉 ----
.td-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--hairline);
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.td-time {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted);
}

.td-phases {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 18px;
}

.td-phase {
  display: flex;
  align-items: center;
  gap: 10px;
}

.td-phase-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  width: 34px;
  flex-shrink: 0;
}

.td-steps {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.td-arrow {
  color: var(--muted);
  font-size: 12px;
  opacity: 0.5;
}

.td-step {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--hairline);
  color: var(--muted);
}

.ts-done {
  color: #166534;
  background: rgba(22, 101, 52, 0.08);
  border-color: rgba(22, 101, 52, 0.2);
}

.ts-active {
  color: #fff;
  background: var(--brass);
  border-color: var(--brass);
  font-weight: 600;
}

.ts-pending {
  opacity: 0.7;
}

.ts-fail {
  color: #b91c1c;
  background: rgba(220, 38, 38, 0.08);
  border-color: rgba(220, 38, 38, 0.2);
}

.ts-cancel {
  text-decoration: line-through;
}

.td-progress {
  margin-bottom: 18px;
}

.td-stage {
  font-size: 13px;
  color: var(--ink-text);
  margin-top: 8px;

  &.err {
    color: var(--danger);
  }
}

.td-err {
  font-size: 12px;
  color: var(--danger);
  margin-top: 4px;
}

.td-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 14px 0;
  border-top: 1px solid var(--hairline);
  border-bottom: 1px solid var(--hairline);
  margin-bottom: 12px;
}

.td-hint {
  font-size: 12px;
  color: var(--muted);
  margin: 0 0 12px;
}

.td-log-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 8px;
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
