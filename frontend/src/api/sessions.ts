import api from './index';
import type { Message } from './chat';

/** 会话对象 */
export interface Session {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
}

/** 新建会话可选参数 */
export interface CreateSessionParams {
  title?: string;
}

/**
 * 获取会话列表: GET /sessions
 */
export async function getSessions(): Promise<Session[]> {
  const { data } = await api.get<Session[]>('/sessions');
  return data;
}

/**
 * 新建会话: POST /sessions
 */
export async function createSession(params: CreateSessionParams = {}): Promise<Session> {
  const { data } = await api.post<Session>('/sessions', params);
  return data;
}

/**
 * 删除会话: DELETE /sessions/:id
 */
export async function deleteSession(id: string): Promise<void> {
  await api.delete(`/sessions/${id}`);
}

/**
 * 重命名会话: PATCH /sessions/:id
 */
export async function renameSession(id: string, title: string): Promise<Session> {
  const { data } = await api.patch<Session>(`/sessions/${id}`, { title });
  return data;
}

/**
 * 获取会话的历史消息: GET /sessions/:id/messages
 */
export async function getSessionMessages(id: string): Promise<Message[]> {
  const { data } = await api.get<Message[]>(`/sessions/${id}/messages`);
  return data;
}
