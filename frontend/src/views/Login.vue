<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { User, Lock } from '@element-plus/icons-vue';
import { ElMessage, type FormInstance, type FormRules } from 'element-plus';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const formRef = ref<FormInstance>();
const loading = ref(false);

const form = reactive({
  username: '',
  password: '',
});

// 表单校验规则
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 4, message: '密码长度不少于 4 位', trigger: 'blur' },
  ],
};

/** 登录提交 */
async function handleLogin(): Promise<void> {
  if (!formRef.value) return;
  await formRef.value.validate(async (valid) => {
    if (!valid) return;
    loading.value = true;
    try {
      await auth.login({ username: form.username, password: form.password });
      ElMessage.success('登录成功');
      // 优先跳转到 redirect 参数指向的页面
      const redirect = (route.query.redirect as string) || '/chat';
      router.push(redirect);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '登录失败, 请检查账号密码';
      ElMessage.error(msg);
    } finally {
      loading.value = false;
    }
  });
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <div class="logo-badge">R</div>
        <h1 class="title">多模态 RAG 知识库</h1>
        <p class="subtitle">企业版智能问答平台</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        size="large"
        class="login-form"
        @keyup.enter="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入用户名"
            :prefix-icon="User"
            clearable
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-button type="primary" class="login-btn" :loading="loading" @click="handleLogin">
          登 录
        </el-button>
      </el-form>

      <p class="footer-tip">多模态检索增强生成 · 安全可控的企业知识中枢</p>
    </div>
  </div>
</template>

<style scoped lang="scss">
.login-page {
  position: relative;
  height: 100%;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: var(--ink);
}

.login-card {
  position: relative;
  width: 380px;
  padding: 40px 36px 28px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-elevated);
}

.login-header {
  text-align: center;
  margin-bottom: 28px;
}

.logo-badge {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 16px;
  font-size: 28px;
  font-weight: 700;
  font-family: var(--font-display);
  color: #ffffff;
  background: var(--brass);
}

.title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
  color: var(--ink-text);
  letter-spacing: 1px;
}

.subtitle {
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-secondary);
}

.login-form {
  margin-top: 8px;
}

.login-btn {
  width: 100%;
  margin-top: 6px;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 4px;
  border: none;
  background: var(--brass);
  color: #ffffff;

  &:hover {
    background: var(--brass-hover);
    color: #ffffff;
  }
}

.footer-tip {
  margin-top: 22px;
  text-align: center;
  font-size: 12px;
  color: var(--muted);
}
</style>
