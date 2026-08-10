<script setup lang="ts">
import { computed, ref, nextTick } from 'vue'
import { Plus, Search, Delete, EditPen } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import type { Session } from '@/api/sessions'

const props = withDefaults(
  defineProps<{
    sessions: Session[]
    currentId?: string | null
    loading?: boolean
    /** 流式输出中(用于禁用当前会话删除按钮, 避免白弹确认框) */
    streaming?: boolean
  }>(),
  {
    currentId: null,
    loading: false,
    streaming: false,
  },
)

const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'delete', id: string): void
  (e: 'new'): void
  (e: 'rename', id: string, title: string): void
}>()

const keyword = ref('')

// 正在编辑的会话 id 和临时标题
const editingId = ref<string | null>(null)
const editingTitle = ref('')
const editInputRef = ref<HTMLInputElement | null>(null)

// 关键词过滤会话列表
const filteredSessions = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.sessions
  return props.sessions.filter((s) => s.title?.toLowerCase().includes(kw))
})

/** 格式化时间 */
function formatTime(t?: string): string {
  if (!t) return ''
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return t
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

/** 选中会话 */
function handleSelect(id: string): void {
  if (id === props.currentId || editingId.value) return
  emit('select', id)
}

/** 删除会话(弹出确认弹窗) */
async function handleDelete(e: Event, session: Session): Promise<void> {
  e.stopPropagation()
  try {
    await ElMessageBox.confirm(
      `确定要删除会话「${session.title || '未命名会话'}」吗？删除后无法恢复。`,
      '删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonType: 'danger',
        customClass: 'confirm-box',
        center: true,
      },
    )
    emit('delete', session.id)
  } catch {
    // 用户取消删除
  }
}

/** 双击会话标题进入编辑模式 */
async function handleDoubleClick(session: Session): Promise<void> {
  editingId.value = session.id
  editingTitle.value = session.title || ''
  await nextTick()
  editInputRef.value?.focus()
  editInputRef.value?.select()
}

/** 确认重命名 */
function confirmRename(): void {
  if (editingId.value) {
    const title = editingTitle.value.trim()
    if (title) {
      emit('rename', editingId.value, title)
    }
    editingId.value = null
  }
}

/** 取消编辑 */
function cancelRename(): void {
  editingId.value = null
}
</script>

<template>
  <div class="session-list">
    <!-- 搜索框 -->
    <div class="search-box">
      <el-input
        v-model="keyword"
        placeholder="搜索会话"
        :prefix-icon="Search"
        clearable
        size="small"
      />
    </div>

    <!-- 会话列表 -->
    <div class="list-scroll">
      <div v-if="loading" class="list-tip">加载中...</div>

      <div v-else-if="filteredSessions.length === 0" class="list-tip">
        {{ keyword ? '无匹配会话' : '暂无会话, 点击下方新建' }}
      </div>

      <div
        v-for="session in filteredSessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.id === currentId }"
        @click="handleSelect(session.id)"
        @dblclick="handleDoubleClick(session)"
      >
        <div class="session-info">
          <!-- 编辑模式 -->
          <input
            v-if="editingId === session.id"
            ref="editInputRef"
            v-model="editingTitle"
            class="title-edit-input"
            @click.stop
            @keydown.enter="confirmRename"
            @keydown.esc="cancelRename"
            @blur="confirmRename"
          />
          <div v-else class="session-title">{{ session.title || '未命名会话' }}</div>
          <div class="session-time">{{ formatTime(session.updated_at || session.created_at) }}</div>
        </div>
        <div class="action-btns" v-if="editingId !== session.id">
          <el-button
            class="icon-btn"
            :icon="EditPen"
            text
            size="small"
            @click.stop="handleDoubleClick(session)"
          />
          <el-button
            class="icon-btn delete-btn"
            :icon="Delete"
            text
            size="small"
            :disabled="streaming && session.id === currentId"
            @click="(e) => handleDelete(e, session)"
          />
        </div>
      </div>
    </div>

    <!-- 新建会话按钮(底部固定) -->
    <div class="new-session">
      <el-button type="primary" :icon="Plus" class="new-btn" @click="emit('new')">
        新建会话
      </el-button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.session-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.search-box {
  padding: 0 14px 10px;
}

.list-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.list-tip {
  padding: 24px 14px;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary);
}

.session-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 10px 12px;
  margin-bottom: 4px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.18s;
  position: relative;

  &:hover {
    background: var(--surface-2);

    .action-btns {
      opacity: 1;
    }
  }

  &.active {
    background: var(--brass-soft);
    border: 1px solid rgba(194, 154, 59, 0.4);

    .session-title {
      color: var(--brass);
    }
  }
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: 13.5px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-time {
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--text-secondary);
}

.title-edit-input {
  width: 100%;
  font-size: 13.5px;
  padding: 2px 6px;
  border: 1px solid var(--brass);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--ink);
  color: var(--ink-text);
}

.action-btns {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.18s;
}

.icon-btn {
  color: var(--muted);
  padding: 4px;

  &:hover {
    color: var(--brass);
  }
}

.delete-btn {
  &:hover {
    color: var(--danger);
  }
}

.new-session {
  padding: 12px 14px;
  border-top: 1px solid var(--border-color);
}

.new-btn {
  width: 100%;
}
</style>
