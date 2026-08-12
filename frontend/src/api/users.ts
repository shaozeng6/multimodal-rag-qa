import api from './index';

/** 用户(管理端) */
export interface AdminUser {
  id: number;
  username: string;
  role: string; // user / admin
  is_active: boolean;
  must_change_password: boolean;
  created_at?: string;
}

/** 创建用户参数 */
export interface CreateUserParams {
  username: string;
  password: string;
  role: string;
}

/**
 * 用户列表(仅管理员): GET /admin/users
 */
export async function getUsers(): Promise<AdminUser[]> {
  const { data } = await api.get<AdminUser[]>('/admin/users');
  return data;
}

/**
 * 创建用户(仅管理员, 新用户首登强制改密)
 */
export async function createUser(params: CreateUserParams): Promise<AdminUser> {
  const { data } = await api.post<AdminUser>('/admin/users', params);
  return data;
}

/**
 * 修改角色 / 启禁用(仅管理员, 带自我保护守卫)
 */
export async function updateUser(
  id: number,
  params: { role?: string; is_active?: boolean },
): Promise<AdminUser> {
  const { data } = await api.patch<AdminUser>(`/admin/users/${id}`, params);
  return data;
}

/**
 * 管理员重置密码(重置后该用户首登强制改密)
 */
export async function resetUserPassword(id: number, newPassword: string): Promise<AdminUser> {
  const { data } = await api.post<AdminUser>(`/admin/users/${id}/reset-password`, {
    new_password: newPassword,
  });
  return data;
}

/**
 * 删除用户(仅管理员, 不能删自己/最后一个管理员)
 */
export async function deleteUser(id: number): Promise<void> {
  await api.delete(`/admin/users/${id}`);
}
