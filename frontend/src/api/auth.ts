import api from './index';

/** 用户信息 */
export interface UserInfo {
  id: string | number;
  username: string;
  role: string;
  /** 是否需先改密(首登强制改密; 为 true 时前端拦截到改密页) */
  must_change_password?: boolean;
}

/** 登录接口返回结构 */
export interface LoginResult {
  access_token: string;
  token_type?: string;
  user?: UserInfo;
  must_change_password?: boolean;
}

/** 登录请求参数 */
export interface LoginParams {
  username: string;
  password: string;
}

/**
 * 登录: 调用后端 /auth/login, 返回 JWT token
 */
export async function login(params: LoginParams): Promise<LoginResult> {
  // 兼容表单格式与 JSON 格式后端, 这里使用 JSON
  const { data } = await api.post<LoginResult>('/auth/login', params);
  return data;
}

/**
 * 获取当前登录用户信息: /auth/me
 */
export async function getUserInfo(): Promise<UserInfo> {
  const { data } = await api.get<UserInfo>('/auth/me');
  return data;
}

/**
 * 修改当前用户密码(首登强制改密): /auth/change-password
 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<UserInfo> {
  const { data } = await api.post<UserInfo>('/auth/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  });
  return data;
}
