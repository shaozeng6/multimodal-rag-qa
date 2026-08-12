import api from './index';

/** 入库任务 */
export interface IngestJob {
  id: string;
  filename: string;
  status: string; // pending / running / success / error / cancelled
  stage: string;
  documents_count?: number;
  error?: string;
  progress?: number; // 0~100
  stage_detail?: string;
  run_mode?: string; // auto 自动全流程 / manual 上传后先不处理
  paused?: boolean; // 是否暂停在阶段边界
  log?: string[]; // 内存日志尾部
  created_at?: string;
  updated_at?: string;
}

/** 已入库文档 */
export interface KnowledgeDoc {
  id: number;
  job_id?: string;
  filename: string;
  filetype: string;
  title?: string;
  status: string; // ingested / partial / failed / deleted
  chunk_count: number;
  image_count: number;
  char_count?: number;
  file_size?: number;
  uploader_name?: string;
  created_at?: string;
  updated_at?: string;
}

/** 上传结果 */
export interface UploadResult {
  job_id: string;
  status: string;
  filename: string;
  run_mode?: string;
}

/**
 * 文件列表统一条目: kind=doc 已入库文档 / kind=job 进行中/失败/已取消的上传任务
 * doc 侧字段: chunk_count/image_count/char_count/file_size/uploader_name...
 * job 侧字段: stage/stage_detail/progress/run_mode/paused/error/log
 */
export interface UploadEntry {
  id: number | string;
  kind: 'doc' | 'job';
  filename: string;
  status: string; // doc: ingested/partial/failed/deleted; job: pending/running/error/cancelled
  // ---- doc ----
  job_id?: string;
  filetype?: string;
  title?: string;
  chunk_count?: number;
  image_count?: number;
  char_count?: number;
  file_size?: number;
  uploader_name?: string;
  // ---- job ----
  stage?: string;
  stage_detail?: string;
  progress?: number;
  run_mode?: string;
  phase?: string; // parse 解析(OCR/分片) / index 索引(描述/向量化/入库)
  paused?: boolean;
  error?: string;
  log?: string[];
  created_at?: string;
  updated_at?: string;
}

/** 文件分页结果(已入库文档 + 进行中任务) */
export interface DocumentListResult {
  items: UploadEntry[];
  total: number;
  page: number;
  page_size: number;
}

/** 文档的 chunk 明细 */
export interface Chunk {
  id: number;
  text: string;
  category: string; // text / image / table
  image_path?: string;
  title?: string;
  url?: string;
  char_count?: number;
}

/** 知识库统计 */
export interface KnowledgeStatus {
  status: 'ok' | 'degraded';
  collections?: { name: string; document_count: number }[];
  total_documents: number;
  total_vectors: number;
  total_chars?: number;
  failed_jobs?: number;
  message?: string;
}

/**
 * 上传 PDF 触发异步入库: POST /knowledge/upload, 返回 job_id 供轮询进度
 * mode: auto 自动全流程 / manual 手动分步(每阶段完成即停, 手动推进)
 */
export async function uploadPdf(file: File, mode = 'auto'): Promise<UploadResult> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('mode', mode);
  const { data } = await api.post<UploadResult>('/knowledge/upload', formData);
  return data;
}

/**
 * 最近入库任务列表(按创建时间倒序, 可按 status 筛选)
 */
export async function getJobs(limit = 20, status = ''): Promise<IngestJob[]> {
  const { data } = await api.get<{ jobs: IngestJob[] }>('/knowledge/jobs', {
    params: { limit, status: status || undefined },
  });
  return data.jobs;
}

/**
 * 重试失败任务
 */
export async function retryJob(jobId: string): Promise<void> {
  await api.post(`/knowledge/jobs/${jobId}/retry`);
}

/**
 * 删除失败/已取消的任务(清理记录与中间产物)
 */
export async function deleteJob(jobId: string): Promise<void> {
  await api.delete(`/knowledge/jobs/${jobId}`);
}

/**
 * 取消 running/pending 任务(协作式)
 */
export async function cancelJob(jobId: string): Promise<void> {
  await api.post(`/knowledge/jobs/${jobId}/cancel`);
}

/**
 * 手动控制: 触发解析段(OCR/分片)
 */
export async function startParse(jobId: string): Promise<void> {
  await api.post(`/knowledge/jobs/${jobId}/parse`);
}

/**
 * 手动控制: 触导入库段(描述/向量化/写入)
 */
export async function startIndex(jobId: string): Promise<void> {
  await api.post(`/knowledge/jobs/${jobId}/index`);
}

/**
 * 暂停任务: 当前阶段结束后在下一阶段边界停下
 */
export async function pauseJob(jobId: string): Promise<void> {
  await api.post(`/knowledge/jobs/${jobId}/pause`);
}

/**
 * 继续任务: 放行暂停中的任务, 之后一路跑完剩余阶段
 */
export async function resumeJob(jobId: string): Promise<void> {
  await api.post(`/knowledge/jobs/${jobId}/resume`);
}

/**
 * 文件分页列表(已入库文档 + 进行中/失败/已取消任务), keyword 文件名过滤, status 逗号分隔
 */
export async function getDocuments(
  page: number,
  pageSize: number,
  keyword = '',
  status = '',
): Promise<DocumentListResult> {
  const { data } = await api.get<DocumentListResult>('/knowledge/documents', {
    params: {
      page,
      page_size: pageSize,
      keyword,
      status: status || undefined,
    },
  });
  return data;
}

/**
 * 知识库统计: Milvus 实体数 + MySQL 文档数 + 字符数 + 失败任务数 + 服务状态
 */
export async function getKnowledgeStatus(): Promise<KnowledgeStatus> {
  const { data } = await api.get<KnowledgeStatus>('/knowledge/status');
  return data;
}

/**
 * 某文档的 chunk 明细(按入库顺序, 图片带可加载 url, 每个 chunk 带字符数)
 */
export async function getDocumentChunks(id: number): Promise<{ items: Chunk[]; total: number }> {
  const { data } = await api.get<{ items: Chunk[]; total: number }>(
    `/knowledge/documents/${id}/chunks`,
  );
  return data;
}

/**
 * 删除文档(先删 Milvus 向量, 再清理磁盘产物, 最后删元数据行)
 */
export async function deleteDocument(id: number): Promise<void> {
  await api.delete(`/knowledge/documents/${id}`);
}
