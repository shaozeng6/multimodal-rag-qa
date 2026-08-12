<script setup lang="ts">
import { onMounted, ref, computed, watch, nextTick } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { ElMessageBox, ElMessage } from 'element-plus';
import { SwitchButton, DocumentCopy, Setting } from '@element-plus/icons-vue';
import { useChatStore } from '@/stores/chat';
import { useAuthStore } from '@/stores/auth';
import SessionList from '@/components/SessionList.vue';
import ChatInput from '@/components/ChatInput.vue';
import MessageBubble from '@/components/MessageBubble.vue';
import ApprovalDialog from '@/components/ApprovalDialog.vue';

const router = useRouter();
const route = useRoute();
const chatStore = useChatStore();
const auth = useAuthStore();

const messagesRef = ref<HTMLElement | null>(null);
const approvalVisible = ref(false);
// 移动端侧栏抽屉开关
const sidebarOpen = ref(false);

// 当前会话标题
const currentTitle = ref('多模态 RAG 知识库问答');

/** 当前 AI 消息的执行链路: 流式期间实时累计, 回答完成后仍保留展示 */
const activeAiSteps = computed(() => {
  if (!chatStore.activeAiId) return [];
  const msg = chatStore.messages.find((m) => m.id === chatStore.activeAiId);
  return msg?.nodeSteps || [];
});

/** 滚动到消息底部 */
function scrollToBottom(): void {
  nextTick(() => {
    const el = messagesRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

/** 新建会话 */
async function handleNewSession(): Promise<void> {
  await chatStore.createSession();
  router.push({
    path: '/chat',
    query: { sessionId: chatStore.currentSessionId || undefined },
  });
}

/** 选中会话(由 SessionList 触发) */
function handleSelectSession(id: string): void {
  sidebarOpen.value = false; // 移动端选中后收起抽屉
  router.push({ path: '/chat', query: { sessionId: id } });
}

/** 删除会话: 错误处理 + 路由更新 + 无会话时自动新建 */
async function handleDeleteSession(id: string): Promise<void> {
  // 流式输出中禁止删除当前会话
  if (chatStore.streaming && id === chatStore.currentSessionId) {
    ElMessage.warning('正在回复中, 请等待回复结束后再删除当前会话');
    return;
  }
  try {
    await chatStore.removeSession(id);
    if (chatStore.currentSessionId) {
      // 已自动选中剩余会话, 同步路由
      router.replace({
        path: '/chat',
        query: { sessionId: chatStore.currentSessionId },
      });
    } else {
      // 无剩余会话, 自动新建一个
      await chatStore.createSession();
      router.replace({
        path: '/chat',
        query: { sessionId: chatStore.currentSessionId || undefined },
      });
    }
    ElMessage.success('会话已删除');
  } catch (err) {
    ElMessage.error(`删除失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/** 发送消息 */
async function handleSend(text: string, image: string | null): Promise<void> {
  // 无当前会话时自动新建一个,避免对空/已删除会话发请求导致 404
  if (!chatStore.currentSessionId) {
    try {
      await chatStore.createSession();
      router.replace({
        path: '/chat',
        query: { sessionId: chatStore.currentSessionId || undefined },
      });
    } catch {
      ElMessage.error('创建会话失败, 请稍后重试');
      return;
    }
  }
  await chatStore.sendMessage(text, image);
  scrollToBottom();
}

/** 登出 */
async function handleLogout(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定要退出登录吗?', '提示', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning',
      confirmButtonType: 'danger',
      customClass: 'confirm-box',
      center: true,
    });
    auth.logout();
    router.push('/login');
  } catch {
    // 用户取消
  }
}

/** 跳转知识库管理 */
function goKnowledge(): void {
  console.log('[nav] goKnowledge → /knowledge', { isAdmin: auth.isAdmin, user: auth.user });
  router
    .push('/knowledge')
    .then(() => {
      console.log('[nav] goKnowledge 已跳转, URL =', window.location.pathname);
    })
    .catch((err) => {
      console.error('[nav] goKnowledge 跳转失败', err);
      ElMessage.error(`跳转失败: ${err instanceof Error ? err.message : String(err)}`);
    });
}

/** 跳转知识库管理页的系统设置 Tab(同一管理页内切换, 无需来回跳页) */
function goSettings(): void {
  console.log('[nav] goSettings → /knowledge?tab=settings', {
    isAdmin: auth.isAdmin,
    user: auth.user,
  });
  router
    .push({ path: '/knowledge', query: { tab: 'settings' } })
    .then(() => {
      console.log(
        '[nav] goSettings 已跳转, URL =',
        window.location.pathname + window.location.search,
      );
    })
    .catch((err) => {
      console.error('[nav] goSettings 跳转失败', err);
      ElMessage.error(`跳转失败: ${err instanceof Error ? err.message : String(err)}`);
    });
}

// 监听审批拦截: 命中时弹出审批弹窗
watch(
  () => chatStore.pendingApproval,
  (val) => {
    approvalVisible.value = !!val;
  },
);

// 监听消息变化自动滚动到底部
watch(
  () => chatStore.messages.length,
  () => scrollToBottom(),
);

// 监听流式输出时内容增长, 持续滚动
watch(
  () => chatStore.messages.map((m) => m.content).join(''),
  () => scrollToBottom(),
);

// 监听路由 sessionId 参数, 切换会话
watch(
  () => route.query.sessionId,
  async (id) => {
    if (typeof id === 'string' && id && id !== chatStore.currentSessionId) {
      // 仅当会话存在于本地列表时才切换, 避免对已删除会话发请求导致 404
      const exists = chatStore.sessions.some((s) => s.id === id);
      if (exists) {
        await chatStore.selectSession(id);
        scrollToBottom();
      }
    }
  },
);

onMounted(async () => {
  await chatStore.loadSessions();
  const id = route.query.sessionId as string;
  if (id) {
    await chatStore.selectSession(id);
  } else if (chatStore.sessions.length > 0) {
    await chatStore.selectSession(chatStore.sessions[0].id);
  } else {
    // 无会话时自动创建一个默认会话
    await chatStore.createSession();
    router.push({
      path: '/chat',
      query: { sessionId: chatStore.currentSessionId || undefined },
    });
  }
  scrollToBottom();
});
</script>

<template>
  <div class="chat-layout">
    <!-- 顶栏 -->
    <header class="chat-header">
      <div class="header-left">
        <button class="menu-btn" aria-label="会话列表" @click="sidebarOpen = !sidebarOpen">
          <span></span><span></span><span></span>
        </button>
        <div class="brand">
          <span class="brand-mark">R</span>
          <span class="brand-name">RAG 知识工作台</span>
        </div>
        <el-divider direction="vertical" />
        <span class="session-title">{{ chatStore.currentSession?.title || currentTitle }}</span>
      </div>

      <div class="header-right">
        <el-button
          v-if="auth.isAdmin"
          text
          :icon="DocumentCopy"
          class="header-btn"
          @click="goKnowledge"
        >
          知识库管理
        </el-button>
        <el-button v-if="auth.isAdmin" text :icon="Setting" class="header-btn" @click="goSettings">
          系统设置
        </el-button>
        <el-divider v-if="auth.isAdmin" direction="vertical" class="header-divider" />
        <el-dropdown trigger="click">
          <div class="user-info">
            <el-avatar :size="32" class="user-avatar">
              {{ auth.user?.username?.charAt(0).toUpperCase() || 'U' }}
            </el-avatar>
            <span class="username">{{ auth.user?.username || '用户' }}</span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item :icon="SwitchButton" @click="handleLogout">
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 主体 -->
    <div class="chat-body">
      <!-- 左侧会话列表(移动端为抽屉) -->
      <div v-if="sidebarOpen" class="sidebar-backdrop" @click="sidebarOpen = false"></div>
      <aside class="sidebar" :class="{ open: sidebarOpen }">
        <SessionList
          :sessions="chatStore.sessions"
          :current-id="chatStore.currentSessionId"
          :loading="chatStore.loadingSessions"
          :streaming="chatStore.streaming"
          @select="handleSelectSession"
          @delete="handleDeleteSession"
          @new="handleNewSession"
          @rename="(id, title) => chatStore.renameSession(id, title)"
        />
      </aside>

      <!-- 右侧聊天区 -->
      <main class="chat-main">
        <!-- 消息区 -->
        <div ref="messagesRef" class="messages-area">
          <div v-if="chatStore.messages.length === 0" class="empty-state">
            <div class="empty-icon">❝</div>
            <p>向企业知识库提问</p>
            <p class="empty-sub">支持文本与图片；回答会标注来源与置信度，低可信将进入人工审批</p>
          </div>

          <MessageBubble
            v-for="msg in chatStore.messages"
            :key="msg.id"
            :role="msg.role"
            :content="msg.content"
            :images="msg.images"
            :evidence="msg.evidence"
            :score="msg.score"
            :show-confidence="auth.isAdmin"
            :streaming="
              chatStore.streaming &&
              msg.role === 'ai' &&
              msg === chatStore.messages[chatStore.messages.length - 1]
            "
          />

          <!-- 执行链路(仅管理员): 当前 AI 消息经过的节点(回答完成后仍显示, 直到下一条消息) -->
          <div v-if="auth.isAdmin && activeAiSteps.length" class="node-steps">
            <template v-for="(step, idx) in activeAiSteps" :key="idx">
              <span v-if="idx > 0" class="step-arrow">→</span>
              <span class="step-item">
                <span class="step-dot"></span>
                <span class="step-label">{{ step.label }}</span>
              </span>
            </template>
          </div>
        </div>

        <!-- 输入区: 仅流式输出中禁用, 无会话时 handleSend 会自动创建 -->
        <ChatInput :disabled="chatStore.streaming" @send="handleSend" />
      </main>
    </div>

    <!-- 审批弹窗 -->
    <ApprovalDialog
      v-model:visible="approvalVisible"
      :approval="chatStore.pendingApproval"
      @approve="(reason?: string) => chatStore.submitApproval(true, reason)"
      @reject="(reason?: string) => chatStore.submitApproval(false, reason)"
    />
  </div>
</template>

<style scoped lang="scss">
.chat-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  background: var(--bg-primary);
}

// 顶栏
.chat-header {
  height: 48px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--hairline);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--brass);
  color: #ffffff;
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 700;
}

.brand-name {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--ink-text);
  white-space: nowrap;
}

// 移动端抽屉按钮(桌面隐藏)
.menu-btn {
  display: none;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  width: 30px;
  height: 30px;
  padding: 0 6px;
  background: transparent;
  border: 1px solid var(--hairline);
  border-radius: 6px;
  cursor: pointer;

  span {
    display: block;
    height: 1.5px;
    background: var(--ink-text);
    border-radius: 1px;
  }
  &:hover {
    border-color: var(--brass);
  }
}

.session-title {
  font-size: 14px;
  color: var(--text-secondary);
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-btn {
  color: var(--muted);
  font-size: 13px;
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  transition:
    background 0.15s,
    color 0.15s;

  &:hover {
    background: var(--surface-2);
    color: var(--ink-text);
  }
}

.header-divider {
  height: 20px;
  margin: 0 4px;
  border-color: var(--hairline);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  transition: background 0.2s;
}
.user-info:hover {
  background: var(--el-fill-color-light);
}

.user-avatar {
  background: var(--surface-2);
  color: var(--ink-text);
  border: 1px solid var(--hairline);
  font-weight: 600;
}

.username {
  font-size: 13px;
  color: var(--text-primary);
}

// 主体
.chat-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.sidebar {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border-color);
}

.sidebar-top {
  padding: 14px 14px 8px;
}

.new-session-btn {
  width: 100%;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-primary);
}

.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0;
}

.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);

  .empty-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 64px;
    height: 64px;
    margin-bottom: 16px;
    border-radius: 16px;
    border: 1px solid var(--brass);
    background: var(--brass-soft);
    color: var(--brass);
    font-family: var(--font-display);
    font-size: 30px;
  }
  p {
    font-size: 15px;
    color: var(--ink-text);
  }
  .empty-sub {
    margin-top: 6px;
    font-size: 13px;
    opacity: 0.75;
    max-width: 380px;
    text-align: center;
  }
}

// 执行链路: 单色仪器条(签名元素的延伸)
.node-steps {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 24px;
  font-family: var(--font-mono);
  font-size: 12px;

  .step-arrow {
    color: var(--muted);
    opacity: 0.5;
  }

  .step-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 8px;
    border-radius: 3px;
    background: var(--brass-soft);
    border: 1px solid rgba(37, 99, 235, 0.28);
    color: var(--brass);
    animation: step-fade-in 0.3s ease;

    .step-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: currentColor;
    }

    .step-label {
      line-height: 1;
      letter-spacing: 0.3px;
    }
  }
}

@keyframes step-fade-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

// ============================================================
// 响应式: 移动端侧栏变抽屉
// ============================================================
.sidebar-backdrop {
  display: none;
}

@media (max-width: 900px) {
  .menu-btn {
    display: flex;
  }

  .sidebar {
    position: fixed;
    left: 0;
    top: 48px;
    bottom: 0;
    z-index: 40;
    width: 280px;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    box-shadow: var(--shadow-elevated);

    &.open {
      transform: translateX(0);
    }
  }

  .sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 48px 0 0 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 30;
  }

  .bubble-wrap,
  .message-row {
    padding-left: 16px;
    padding-right: 16px;
  }
}
</style>
