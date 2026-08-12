import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import {
  login as loginApi,
  getUserInfo,
  changePassword as changePasswordApi,
  type UserInfo,
  type LoginParams,
} from '@/api/auth';
import { getToken, setToken, clearToken } from '@/api/index';

/**
 * 认证状态 Store
 * 管理 JWT token 与当前用户信息, token 从 localStorage 恢复
 */
export const useAuthStore = defineStore('auth', () => {
  // 从 localStorage 恢复 token
  const token = ref<string | null>(getToken());
  const user = ref<UserInfo | null>(null);

  // 是否已登录
  const isLoggedIn = computed(() => !!token.value);

  // 是否为管理员
  const isAdmin = computed(() => user.value?.role === 'admin');

  // 是否需要先改密(首登强制改密; 为 true 时前端拦截到改密页)
  const mustChangePassword = computed(() => !!user.value?.must_change_password);

  /**
   * 登录: 调用后端接口, 成功后持久化 token
   */
  async function login(params: LoginParams): Promise<void> {
    const result = await loginApi(params);
    const tk = result.access_token;
    setToken(tk);
    token.value = tk;
    // 若登录接口直接返回了用户信息则使用, 否则拉取一次
    if (result.user) {
      user.value = result.user;
    } else {
      try {
        user.value = await getUserInfo();
      } catch {
        user.value = null;
      }
    }
  }

  /**
   * 获取当前用户信息(已登录状态下刷新用户)
   */
  async function fetchUser(): Promise<void> {
    if (!token.value) return;
    try {
      user.value = await getUserInfo();
    } catch {
      user.value = null;
    }
  }

  /**
   * 修改密码: 成功后更新用户信息(清除强制改密标记)
   */
  async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
    const updated = await changePasswordApi(oldPassword, newPassword);
    user.value = updated;
  }

  /**
   * 登出: 清除 token 与用户信息
   */
  function logout(): void {
    clearToken();
    token.value = null;
    user.value = null;
  }

  return {
    token,
    user,
    isLoggedIn,
    isAdmin,
    mustChangePassword,
    login,
    fetchUser,
    changePassword,
    logout,
  };
});
