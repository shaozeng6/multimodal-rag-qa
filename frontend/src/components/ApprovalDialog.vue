<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Check, Close } from '@element-plus/icons-vue';
import type { ApprovalPayload } from '@/api/chat';

const props = defineProps<{
  visible: boolean;
  approval: ApprovalPayload | null;
}>();

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void;
  (e: 'approve', reason?: string): void;
  (e: 'reject', reason?: string): void;
}>();

const remark = ref('');

// 双向绑定 visible
const dialogVisible = computed({
  get: () => props.visible,
  set: (val: boolean) => emit('update:visible', val),
});

// 评估分数(0-100)
const score = computed(() => {
  const s = props.approval?.score;
  return typeof s === 'number' ? s : null;
});

// 分数对应的颜色与等级
const scoreColor = computed(() => {
  const s = score.value;
  if (s === null) return 'var(--text-secondary)';
  if (s >= 80) return 'var(--success)';
  if (s >= 60) return 'var(--warning)';
  return 'var(--danger)';
});

const scoreLabel = computed(() => {
  const s = score.value;
  if (s === null) return '未评估';
  if (s >= 80) return '高可信';
  if (s >= 60) return '中等';
  return '低可信';
});

// 弹窗打开时重置备注
watch(
  () => props.visible,
  (val) => {
    if (val) remark.value = '';
  },
);

/** 通过审批 */
function handleApprove(): void {
  emit('approve', remark.value.trim() || undefined);
  emit('update:visible', false);
}

/** 驳回 */
function handleReject(): void {
  emit('reject', remark.value.trim() || undefined);
  emit('update:visible', false);
}
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    title="人工审批"
    width="520px"
    align-center
    :close-on-click-modal="false"
    class="approval-dialog"
  >
    <div v-if="approval" class="approval-body">
      <!-- 评估分数 -->
      <div class="score-block">
        <div class="score-label">评估分数</div>
        <div class="score-value" :style="{ color: scoreColor }">
          {{ score !== null ? score : '--' }}
          <span class="score-unit">/ 100</span>
        </div>
        <el-tag :color="scoreColor" effect="dark" round class="score-tag">
          {{ scoreLabel }}
        </el-tag>
      </div>

      <!-- 用户提问 -->
      <div v-if="approval.query" class="field">
        <div class="field-label">用户提问</div>
        <div class="field-content">{{ approval.query }}</div>
      </div>

      <!-- AI 草稿回答 -->
      <div v-if="approval.draft" class="field">
        <div class="field-label">AI 草稿回答</div>
        <div class="field-content draft">{{ approval.draft }}</div>
      </div>

      <!-- 拦截原因 -->
      <div v-if="approval.reason" class="field">
        <div class="field-label">拦截原因</div>
        <div class="field-content warn">{{ approval.reason }}</div>
      </div>

      <!-- 审批备注 -->
      <div class="field">
        <div class="field-label">审批备注(可选)</div>
        <el-input
          v-model="remark"
          type="textarea"
          :rows="3"
          placeholder="请输入审批意见"
          resize="none"
        />
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button type="danger" :icon="Close" class="reject-btn" @click="handleReject">
          驳回
        </el-button>
        <el-button type="success" :icon="Check" class="approve-btn" @click="handleApprove">
          通过
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped lang="scss">
.approval-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.score-block {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  background: var(--surface-2);
  border-radius: var(--radius-md);
  border: 1px solid var(--hairline);
}

.score-label {
  font-size: 13px;
  color: var(--muted);
}

.score-value {
  font-family: var(--font-display);
  font-size: 34px;
  font-weight: 700;
  line-height: 1;
}

.score-unit {
  font-size: 14px;
  color: var(--text-secondary);
  font-weight: 400;
}

.score-tag {
  margin-left: auto;
  border: none;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12.5px;
  color: var(--text-secondary);
}

.field-content {
  padding: 10px 12px;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  font-size: 13.5px;
  color: var(--text-primary);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.field-content.draft {
  max-height: 180px;
  overflow-y: auto;
}

.field-content.warn {
  color: var(--warning);
  border-color: rgba(243, 156, 18, 0.3);
  background: rgba(243, 156, 18, 0.08);
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.reject-btn {
  background: var(--danger);
  border-color: var(--danger);
}

.approve-btn {
  background: var(--success);
  border-color: var(--success);
}
</style>
