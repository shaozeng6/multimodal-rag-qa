import api from './index'

/** 入库任务 */
export interface IngestJob {
  id: string
  filename: string
  status: string // pending / running / success / error
  stage: string
  documents_count?: number
  error?: string
  created_at?: string
  updated_at?: string
}

/** 已入库文档 */
export interface KnowledgeDoc {
  id: number
  job_id?: string
  filename: string
  filetype: string
  title?: string
  status: string // ingested / partial / failed / deleted
  chunk_count: number
  image_count: number
  uploader_name?: string
  created_at?: string
  updated_at?: string
}

/** 上传结果 */
export interface UploadResult {
  job_id: string
  status: string
  filename: string
}

/** 文档分页结果 */
export interface DocumentListResult {
  items: KnowledgeDoc[]
  total: number
  page: number
  page_size: number
}

/** 文档的 chunk 明细 */
export interface Chunk {
  id: number
  text: string
  category: string // text / image / table
  image_path?: string
  title?: string
  url?: string
}

/** 知识库统计 */
export interface KnowledgeStatus {
  status: 'ok' | 'degraded'
  collections?: { name: string; document_count: number }[]
  total_documents: number
  total_vectors: number
  message?: string
}

/**
 * 上传 PDF 触发异步入库: POST /knowledge/upload, 返回 job_id 供轮询进度
 */
export async function uploadPdf(file: File): Promise<UploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await api.post<UploadResult>('/knowledge/upload', formData)
  return data
}

/**
 * 最近入库任务列表(按创建时间倒序)
 */
export async function getJobs(limit = 20): Promise<IngestJob[]> {
  const { data } = await api.get<{ jobs: IngestJob[] }>('/knowledge/jobs', {
    params: { limit },
  })
  return data.jobs
}

/**
 * 知识文档分页列表, keyword 按文件名模糊过滤
 */
export async function getDocuments(
  page: number,
  pageSize: number,
  keyword = '',
): Promise<DocumentListResult> {
  const { data } = await api.get<DocumentListResult>('/knowledge/documents', {
    params: { page, page_size: pageSize, keyword },
  })
  return data
}

/**
 * 知识库统计: Milvus 实体数 + MySQL 文档数 + 服务状态
 */
export async function getKnowledgeStatus(): Promise<KnowledgeStatus> {
  const { data } = await api.get<KnowledgeStatus>('/knowledge/status')
  return data
}

/**
 * 某文档的 chunk 明细(按入库顺序, 图片带可加载 url)
 */
export async function getDocumentChunks(
  id: number,
): Promise<{ items: Chunk[]; total: number }> {
  const { data } = await api.get<{ items: Chunk[]; total: number }>(
    `/knowledge/documents/${id}/chunks`,
  )
  return data
}

/**
 * 删除文档(先删 Milvus 向量, 再清理磁盘产物, 最后删元数据行)
 */
export async function deleteDocument(id: number): Promise<void> {
  await api.delete(`/knowledge/documents/${id}`)
}
