<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus } from '@element-plus/icons-vue';
import {
  getUsers,
  createUser,
  updateUser,
  resetUserPassword,
  deleteUser,
  type AdminUser,
} from '@/api/users';

const users = ref<AdminUser[]>([]);
const loading = ref(false);

// ---- 新建用户 ----
const createVisible = ref(false);
const creating = ref(false);
const createForm = reactive({ username: '', password: '', role: 'user' });

async function load(): Promise<void> {
  loading.value = true;
  try {
    users.value = await getUsers();
  } catch (err) {
    ElMessage.error(`加载用户失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    loading.value = false;
  }
}

async function handleCreate(): Promise<void> {
  if (createForm.username.trim().length < 2) {
    ElMessage.warning('用户名至少 2 个字符');
    return;
  }
  if (createForm.password.length < 6) {
    ElMessage.warning('密码长度至少 6 位');
    return;
  }
  creating.value = true;
  try {
    await createUser({
      username: createForm.username.trim(),
      password: createForm.password,
      role: createForm.role,
    });
    ElMessage.success('用户已创建, 首登需改密');
    createVisible.value = false;
    createForm.username = '';
    createForm.password = '';
    createForm.role = 'user';
    await load();
  } catch (err) {
    ElMessage.error(`创建失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    creating.value = false;
  }
}

async function handleRoleChange(user: AdminUser, role: string): Promise<void> {
  try {
    await updateUser(user.id, { role });
    user.role = role;
    ElMessage.success('角色已更新');
  } catch (err) {
    ElMessage.error(`更新失败: ${err instanceof Error ? err.message : String(err)}`);
    await load();
  }
}

async function handleToggleActive(user: AdminUser): Promise<void> {
  const next = !user.is_active;
  try {
    await updateUser(user.id, { is_active: next });
    user.is_active = next;
    ElMessage.success(next ? `已启用 ${user.username}` : `已禁用 ${user.username}`);
  } catch (err) {
    ElMessage.error(`操作失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function handleResetPassword(user: AdminUser): Promise<void> {
  let value: string;
  try {
    const res = await ElMessageBox.prompt(
      `为「${user.username}」设置新密码(至少 6 位), 重置后该用户首登需改密`,
      '重置密码',
      {
        inputPlaceholder: '新密码',
        inputType: 'password',
        confirmButtonText: '重置',
        cancelButtonText: '取消',
      },
    );
    value = res.value;
  } catch {
    return; // 取消
  }
  if (!value || value.length < 6) {
    ElMessage.warning('密码长度至少 6 位');
    return;
  }
  try {
    await resetUserPassword(user.id, value);
    ElMessage.success('密码已重置');
    await load();
  } catch (err) {
    ElMessage.error(`重置失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function handleDelete(user: AdminUser): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${user.username}」? 其会话与消息历史将一并移除。`,
      '删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonType: 'danger',
      },
    );
  } catch {
    return;
  }
  try {
    await deleteUser(user.id);
    ElMessage.success('用户已删除');
    await load();
  } catch (err) {
    ElMessage.error(`删除失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

onMounted(load);
</script>

<template>
  <div class="user-mgmt">
    <!-- 工具条 -->
    <div class="um-toolbar">
      <div class="um-title-wrap">
        <h3 class="um-title">用户管理</h3>
        <p class="um-sub">创建用户 / 分配角色 / 启禁用 · 新用户与重置密码后首登需改密</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="createVisible = true">新建用户</el-button>
    </div>

    <!-- 用户表格 -->
    <div class="um-table-wrap" v-loading="loading">
      <el-table :data="users" empty-text="暂无用户">
        <el-table-column prop="username" label="用户名" min-width="140" show-overflow-tooltip />
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-select
              :model-value="row.role"
              size="small"
              @change="(v: string) => handleRoleChange(row, v)"
            >
              <el-option label="管理员" value="admin" />
              <el-option label="普通用户" value="user" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="status-pill" :class="row.is_active ? 'pill-active' : 'pill-disabled'">
              {{ row.is_active ? '启用' : '禁用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="改密" width="90">
          <template #default="{ row }">
            <span v-if="row.must_change_password" class="pill-pending">待改密</span>
            <span v-else class="muted-text">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="150" />
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button text size="small" @click="handleToggleActive(row)">
              {{ row.is_active ? '禁用' : '启用' }}
            </el-button>
            <el-button text size="small" @click="handleResetPassword(row)">重置密码</el-button>
            <el-button text type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 新建用户弹窗 -->
    <el-dialog v-model="createVisible" title="新建用户" width="400px" :close-on-click-modal="false">
      <el-form label-width="70px">
        <el-form-item label="用户名">
          <el-input v-model="createForm.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            placeholder="至少 6 位, 首登强制修改"
          />
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="createForm.role">
            <el-radio value="user">普通用户</el-radio>
            <el-radio value="admin">管理员</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.user-mgmt {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--ink);
  overflow: hidden;
}

.um-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--hairline);
  flex-shrink: 0;
  background: var(--surface);
}

.um-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-text);
}

.um-sub {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.um-table-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 24px;
}

.um-table-wrap :deep(.el-table) {
  --el-table-border-color: var(--hairline);
  --el-table-header-bg-color: #f8fafc;
  --el-table-header-text-color: var(--muted);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
}

.pill-active {
  color: #166534;
  background: rgba(22, 101, 52, 0.1);
}

.pill-disabled {
  color: #b91c1c;
  background: rgba(220, 38, 38, 0.1);
}

.pill-pending {
  display: inline-flex;
  align-items: center;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
  color: #92400e;
  background: rgba(217, 119, 6, 0.12);
}

.muted-text {
  color: var(--muted);
}
</style>
