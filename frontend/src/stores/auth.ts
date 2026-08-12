import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { login as loginApi, getUserInfo, type UserInfo, type LoginParams } from '@/api/auth';
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
    login,
    fetchUser,
    logout,
  };
});
