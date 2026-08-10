<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ArrowLeft, FolderOpened } from '@element-plus/icons-vue'

const router = useRouter()

function back(): void {
  router.push('/chat')
}
</script>

<template>
  <div class="knowledge-page">
    <header class="kb-header">
      <el-button text :icon="ArrowLeft" @click="back">返回聊天</el-button>
      <h2 class="kb-title">
        <el-icon><FolderOpened /></el-icon>
        知识库管理
      </h2>
    </header>

    <div class="kb-empty">
      <el-empty description="知识库管理模块(占位页, 待后续扩展文档上传、分片检索与索引管理能力)">
        <el-button type="primary" @click="back">返回聊天</el-button>
      </el-empty>
    </div>
  </div>
</template>

<style scoped lang="scss">
.knowledge-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--ink);
}

.kb-header {
  height: 48px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--hairline);
}

.kb-title {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  color: var(--ink-text);
  display: flex;
  align-items: center;
  gap: 8px;
}

.kb-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
