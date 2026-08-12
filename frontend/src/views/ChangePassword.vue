<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { Lock } from '@element-plus/icons-vue';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const auth = useAuthStore();

const form = reactive({
  oldPassword: '',
  newPassword: '',
  confirm: '',
});
const submitting = ref(false);

async function handleSubmit(): Promise<void> {
  if (!form.oldPassword || !form.newPassword || !form.confirm) {
    ElMessage.warning('请填写完整');
    return;
  }
  if (form.newPassword.length < 6) {
    ElMessage.warning('新密码长度至少 6 位');
    return;
  }
  if (form.newPassword !== form.confirm) {
    ElMessage.warning('两次输入的新密码不一致');
    return;
  }
  submitting.value = true;
  try {
    await auth.changePassword(form.oldPassword, form.newPassword);
    ElMessage.success('密码修改成功');
    router.replace('/chat');
  } catch (err) {
    ElMessage.error(`修改失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="change-password-page">
    <div class="change-card">
      <div class="change-header">
        <div class="logo-badge">R</div>
        <h1 class="title">修改密码</h1>
        <p class="subtitle">首次登录或使用初始密码, 请先设置新密码后继续使用</p>
      </div>

      <el-form class="change-form" @keyup.enter="handleSubmit">
        <el-form-item>
          <el-input
            v-model="form.oldPassword"
            type="password"
            placeholder="当前密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.newPassword"
            type="password"
            placeholder="新密码(至少 6 位)"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.confirm"
            type="password"
            placeholder="确认新密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-button type="primary" class="submit-btn" :loading="submitting" @click="handleSubmit">
          确认修改
        </el-button>
        <el-button text class="logout-btn" @click="auth.logout(); router.replace('/login')">
          退出登录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<style scoped lang="scss">
.change-password-page {
  position: relative;
  height: 100%;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: var(--ink);
}

.change-card {
  position: relative;
  width: 380px;
  padding: 40px 36px 28px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-elevated);
}

.change-header {
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
  color: var(--muted);
}

.change-form {
  margin-top: 8px;
}

.submit-btn {
  width: 100%;
  margin-top: 6px;
}

.logout-btn {
  width: 100%;
  margin-top: 6px;
  color: var(--muted);
}
</style>
