import api from './index';

/** 待审核项(普通用户低分回答已交付) */
export interface ReviewItem {
  message_id: number;
  session_id: string;
  query: string;
  answer: string;
  score: number; // 0-1
  created_at?: string;
}

/**
 * 待审核列表(仅管理员): GET /admin/reviews
 */
export async function getReviews(): Promise<ReviewItem[]> {
  const { data } = await api.get<ReviewItem[]>('/admin/reviews');
  return data;
}

/**
 * 处理审核项(仅管理员): approve 通过 / dismiss 忽略
 */
export async function resolveReview(
  messageId: number,
  action: 'approve' | 'dismiss',
): Promise<void> {
  await api.post(`/admin/reviews/${messageId}/resolve`, { action });
}
