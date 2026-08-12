<script setup lang="ts">
import { ref, computed, nextTick } from 'vue';
import { Picture, Promotion, Close } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';

const props = defineProps<{
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: 'send', text: string, image: string | null): void;
}>();

const text = ref('');
const imageBase64 = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const textareaRef = ref<HTMLTextAreaElement | null>(null);

// 是否可发送
const canSend = computed(
  () => !props.disabled && (text.value.trim().length > 0 || !!imageBase64.value),
);

/** 触发图片选择 */
function triggerUpload(): void {
  fileInput.value?.click();
}

/** 处理图片选择, 转为 base64 */
function handleFileChange(e: Event): void {
  const target = e.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) return;

  // 限制图片大小(5MB)
  const MAX_SIZE = 5 * 1024 * 1024;
  if (file.size > MAX_SIZE) {
    ElMessage.warning('图片大小不能超过 5MB');
    target.value = '';
    return;
  }

  if (!file.type.startsWith('image/')) {
    ElMessage.warning('请选择图片文件');
    target.value = '';
    return;
  }

  const reader = new FileReader();
  reader.onload = () => {
    imageBase64.value = reader.result as string;
  };
  reader.onerror = () => {
    ElMessage.error('图片读取失败');
  };
  reader.readAsDataURL(file);
  // 重置 input 以便重复选择同一文件
  target.value = '';
}

/** 移除已选图片 */
function removeImage(): void {
  imageBase64.value = null;
}

/** 发送消息 */
function send(): void {
  if (!canSend.value) return;
  const content = text.value.trim();
  const img = imageBase64.value;
  emit('send', content, img);
  // 清空输入
  text.value = '';
  imageBase64.value = null;
  nextTick(() => {
    autoResize();
  });
}

/** 键盘事件: Enter 发送, Shift+Enter 换行 */
function handleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
}

/** 文本框高度自适应 */
function autoResize(): void {
  const el = textareaRef.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

function handleInput(): void {
  autoResize();
}
</script>

<template>
  <div class="chat-input">
    <!-- 图片预览 -->
    <div v-if="imageBase64" class="image-preview">
      <div class="preview-wrap">
        <img :src="imageBase64" alt="待发送图片" />
        <button class="remove-btn" @click="removeImage">
          <el-icon><Close /></el-icon>
        </button>
      </div>
    </div>

    <div class="input-row">
      <!-- 图片上传按钮 -->
      <el-button
        class="icon-btn"
        circle
        :disabled="disabled"
        :icon="Picture"
        @click="triggerUpload"
      />
      <input
        ref="fileInput"
        type="file"
        accept="image/*"
        class="hidden-input"
        @change="handleFileChange"
      />

      <!-- 多行文本输入 -->
      <textarea
        ref="textareaRef"
        v-model="text"
        class="text-area"
        :disabled="disabled"
        placeholder="输入问题, Enter 发送, Shift+Enter 换行"
        rows="1"
        @keydown="handleKeydown"
        @input="handleInput"
      />

      <!-- 发送按钮 -->
      <el-button
        type="primary"
        class="send-btn"
        :icon="Promotion"
        :disabled="!canSend"
        @click="send"
      />
    </div>
  </div>
</template>

<style scoped lang="scss">
.chat-input {
  padding: 12px 24px 16px;
  background: var(--bg-primary);
  border-top: 1px solid var(--border-color);
}

.image-preview {
  margin-bottom: 10px;
}

.preview-wrap {
  position: relative;
  display: inline-block;
  border-radius: var(--radius-md);
  overflow: hidden;
  border: 1px solid var(--border-color);

  img {
    display: block;
    width: 120px;
    height: 120px;
    object-fit: cover;
  }
}

.remove-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
  transition: background 0.2s;

  &:hover {
    background: var(--danger);
  }
}

.input-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 8px 10px;
  transition: border-color 0.2s;

  &:focus-within {
    border-color: var(--brass);
  }
}

.icon-btn {
  flex-shrink: 0;
  color: var(--muted);
  background: transparent;
  border: none;

  &:hover {
    color: var(--brass);
    background: var(--el-fill-color-light);
  }
}

.hidden-input {
  display: none;
}

.text-area {
  flex: 1;
  resize: none;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.6;
  font-family: inherit;
  max-height: 160px;
  overflow-y: auto;
  padding: 6px 4px;

  &::placeholder {
    color: var(--text-secondary);
  }
}

.send-btn {
  flex-shrink: 0;
  height: 36px;
  width: 36px;
  border: none;
  background: var(--brass);
  color: #ffffff;

  &:hover {
    background: var(--brass-hover);
    color: #ffffff;
  }

  &.is-disabled {
    opacity: 0.4;
  }
}
</style>
