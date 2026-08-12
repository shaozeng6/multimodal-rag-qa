import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/views/Chat.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/change-password',
    name: 'change-password',
    component: () => import('@/views/ChangePassword.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('@/views/Knowledge.vue'),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 全局前置守卫: 未登录跳转 /login, 非管理员访问受限页面跳转 /chat
router.beforeEach(async (to) => {
  const auth = useAuthStore();

  // 已登录但尚未加载用户信息(如刷新页面后), 先拉取一次
  if (auth.isLoggedIn && !auth.user) {
    await auth.fetchUser();
  }

  // 已登录用户访问登录页 -> 跳转聊天
  if (to.path === '/login' && auth.isLoggedIn) {
    return { path: '/chat' };
  }

  // 需要认证但未登录 -> 跳转登录页
  if (to.meta.requiresAuth && !auth.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } };
  }

  // P0: 需先改密(首登强制) → 除改密页外一律拦截到改密页, 改完才能进系统
  if (auth.isLoggedIn && auth.user?.must_change_password && to.path !== '/change-password') {
    return { path: '/change-password' };
  }

  // 需要管理员角色但当前用户非管理员 -> 跳转聊天
  if (to.meta.requiresAdmin && !auth.isAdmin) {
    // 诊断: 理论上按钮仅在 isAdmin 时渲染, 走到这里说明 user/isAdmin 状态异常, 打印现场
    console.warn('[router] 访问受保护页被拦截(requiresAdmin)', {
      to: to.fullPath,
      user: auth.user,
      isLoggedIn: auth.isLoggedIn,
      isAdmin: auth.isAdmin,
      role: auth.user?.role,
    });
    return { path: '/chat' };
  }

  return true;
});

export default router;
