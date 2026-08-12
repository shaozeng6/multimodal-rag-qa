<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { getReviews, resolveReview, type ReviewItem } from '@/api/reviews';

const reviews = ref<ReviewItem[]>([]);
const loading = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  try {
    reviews.value = await getReviews();
  } catch (err) {
    ElMessage.error(`加载审核队列失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    loading.value = false;
  }
}

async function handleResolve(item: ReviewItem, action: 'approve' | 'dismiss'): Promise<void> {
  try {
    await resolveReview(item.message_id, action);
    ElMessage.success(action === 'approve' ? '已通过' : '已忽略');
    await load();
  } catch (err) {
    ElMessage.error(`处理失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/** 0-1 → 0-100 展示分 */
function scoreDisplay(s: number): string {
  return `${Math.round(s * 100)}`;
}

onMounted(load);
</script>

<template>
  <div class="review-queue">
    <div class="rq-toolbar">
      <div class="rq-title-wrap">
        <h3 class="rq-title">审核队列</h3>
        <p class="rq-sub">普通用户低分回答已直接交付, 在此复核 · 管理员自己的会话仍走即时审批</p>
      </div>
      <el-button size="small" @click="load">刷新</el-button>
    </div>

    <div class="rq-table-wrap" v-loading="loading">
      <el-table :data="reviews" empty-text="暂无待审核回答">
        <el-table-column prop="query" label="用户问题" min-width="160" show-overflow-tooltip />
        <el-table-column prop="answer" label="AI 回答" min-width="260" show-overflow-tooltip />
        <el-table-column label="评分" width="80" align="right">
          <template #default="{ row }">
            <span class="score-badge" :class="row.score < 0.6 ? 'bad-low' : 'bad-mid'">
              {{ scoreDisplay(row.score) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="150" />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button text type="success" size="small" @click="handleResolve(row, 'approve')">
              通过
            </el-button>
            <el-button text size="small" @click="handleResolve(row, 'dismiss')">忽略</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped lang="scss">
.review-queue {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--ink);
  overflow: hidden;
}

.rq-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--hairline);
  flex-shrink: 0;
  background: var(--surface);
}

.rq-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-text);
}

.rq-sub {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.rq-table-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 24px;
}

.rq-table-wrap :deep(.el-table) {
  --el-table-border-color: var(--hairline);
  --el-table-header-bg-color: #f8fafc;
  --el-table-header-text-color: var(--muted);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.score-badge {
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
  font-family: var(--font-mono);
}

.bad-mid {
  color: #92400e;
  background: rgba(217, 119, 6, 0.12);
}

.bad-low {
  color: #b91c1c;
  background: rgba(220, 38, 38, 0.1);
}
</style>
