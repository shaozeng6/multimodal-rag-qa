<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  getConfig,
  updateConfig,
  resetConfig,
  type ConfigItem,
  type ConfigGroup,
} from '@/api/config';

/** 一级分类展示顺序(与后端 _GROUP_ORDER 一致) */
const GROUP_ORDER = ['ingestion', 'retrieval', 'evaluation', 'context', 'rag'];

/** 每类的标题与一句话说明 */
const GROUP_META: Record<string, { title: string; subtitle: string }> = {
  ingestion: { title: '入库', subtitle: '分片粒度与向量化重试' },
  retrieval: { title: '检索', subtitle: '召回数量与多路融合权重' },
  evaluation: { title: '评估', subtitle: '评判门槛、兜底与评审范围' },
  context: { title: '上下文', subtitle: '对话窗口与摘要压缩' },
  rag: { title: '图片上限', subtitle: '送进模型的图片数量控制' },
};

/** 组内子分组中文名 */
const SECTION_LABELS: Record<string, string> = {
  chunk: '分片',
  embed: '向量化',
  candidate: '各通道候选数',
  fuse: '融合',
  threshold: '阈值与兜底',
  dims: '维度默认分',
  scope: '评审范围',
  window: '窗口与摘要',
  images: '送模型图片上限',
  default: '参数',
};

/** 前端兜底: 后端未返回 section 时按 key 推导子分组, 保证不塌成单列表 */
function deriveSection(item: ConfigItem): string {
  if (item.section) return item.section;
  const k = item.key;
  if (k.startsWith('ingestion.')) return k.includes('chunk') ? 'chunk' : 'embed';
  if (k.startsWith('retrieval.')) {
    return k === 'retrieval.context_topk' ? 'fuse' : k.includes('topk') ? 'candidate' : 'fuse';
  }
  if (k.startsWith('evaluate.')) {
    if (k.includes('history')) return 'scope';
    if (k.includes('dim') || k.includes('image_fidelity')) return 'dims';
    return 'threshold';
  }
  if (k.startsWith('context.')) return 'window';
  if (k.startsWith('rag.')) return 'images';
  return 'default';
}

const groups = ref<Record<string, ConfigGroup>>({});
const loading = ref(false);
const saving = ref(false);
const drafts = reactive<Record<string, string>>({});
const activeGroup = ref('ingestion');

const activeItems = computed<ConfigItem[]>(() => groups.value[activeGroup.value]?.items || []);

/** 当前分类的子分组(保序) */
const sections = computed(() => {
  const list: { key: string; label: string; items: ConfigItem[] }[] = [];
  for (const item of activeItems.value) {
    const key = deriveSection(item);
    let sec = list.find((s) => s.key === key);
    if (!sec) {
      sec = { key, label: SECTION_LABELS[key] || key, items: [] };
      list.push(sec);
    }
    sec.items.push(item);
  }
  return list;
});

async function loadConfig(): Promise<void> {
  loading.value = true;
  try {
    const res = await getConfig();
    groups.value = res.groups;
    for (const g of Object.values(res.groups)) {
      for (const item of g.items) {
        drafts[item.key] = item.value;
      }
    }
    if (!res.groups[activeGroup.value]) {
      activeGroup.value = GROUP_ORDER.find((k) => res.groups[k]) || 'ingestion';
    }
  } catch (err) {
    ElMessage.error(`加载配置失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    loading.value = false;
  }
}

function isDirty(item: ConfigItem): boolean {
  return drafts[item.key] !== item.value;
}

function groupDirtyCount(group: string): number {
  return (groups.value[group]?.items || []).filter((it) => isDirty(it)).length;
}

/** 保存全部未保存修改 */
async function handleSaveAll(): Promise<void> {
  const changed: { key: string; value: string }[] = [];
  for (const g of GROUP_ORDER) {
    for (const item of groups.value[g]?.items || []) {
      if (isDirty(item)) changed.push({ key: item.key, value: drafts[item.key] });
    }
  }
  if (!changed.length) {
    ElMessage.info('没有需要保存的修改');
    return;
  }
  saving.value = true;
  try {
    const res = await updateConfig(changed);
    if (res.errors?.length) {
      ElMessage.error(res.message || '部分配置保存失败');
      for (const e of res.errors) {
        if (e.key) {
          for (const g of Object.values(groups.value)) {
            const it = g.items.find((x) => x.key === e.key);
            if (it) drafts[e.key] = it.value;
          }
        }
      }
    } else {
      ElMessage.success(res.message || '已保存');
      await loadConfig();
    }
  } catch (err) {
    ElMessage.error(`保存失败: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    saving.value = false;
  }
}

/** 撤销全部未保存修改 */
function handleRevertAll(): void {
  for (const g of Object.values(groups.value)) {
    for (const it of g.items) drafts[it.key] = it.value;
  }
  ElMessage.info('已撤销全部未保存修改');
}

/** 恢复当前分类默认值 */
async function handleResetGroup(): Promise<void> {
  const group = activeGroup.value;
  try {
    await ElMessageBox.confirm(
      `确定将「${groups.value[group]?.label || group}」恢复为默认值? 已修改项将被覆盖。`,
      '恢复默认值',
      { confirmButtonText: '恢复', cancelButtonText: '取消', type: 'warning' },
    );
  } catch {
    return;
  }
  try {
    const res = await resetConfig(group);
    ElMessage.success(res.message || '已恢复默认值');
    await loadConfig();
  } catch (err) {
    ElMessage.error(`重置失败: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function handleResetItem(item: ConfigItem): void {
  drafts[item.key] = item.default;
}

function isNumeric(item: ConfigItem): boolean {
  return item.value_type === 'int' || item.value_type === 'float';
}

onMounted(loadConfig);
</script>

<template>
  <div class="settings-body" v-loading="loading">
    <!-- 左栏: 分类导航(与知识库侧栏同构: 白底 + 右分割线) -->
    <aside class="settings-nav">
      <button
        v-for="g in GROUP_ORDER.filter((k) => groups[k])"
        :key="g"
        class="nav-item"
        :class="{ active: activeGroup === g }"
        @click="activeGroup = g"
      >
        <span class="nav-text">
          <span class="nav-title">{{ groups[g].label }}</span>
          <span class="nav-sub">{{ GROUP_META[g]?.subtitle }}</span>
        </span>
        <span v-if="groupDirtyCount(g)" class="nav-dirty">{{ groupDirtyCount(g) }}</span>
      </button>
    </aside>

    <!-- 主区: 分组卡片 -->
    <main class="settings-main">
      <div class="main-head">
        <div class="main-title-wrap">
          <h3 class="main-title">{{ groups[activeGroup]?.label }}</h3>
          <p class="main-sub">{{ GROUP_META[activeGroup]?.subtitle }} · 即时生效</p>
        </div>
        <div class="main-actions">
          <el-button size="small" @click="handleRevertAll">撤销修改</el-button>
          <el-button size="small" @click="handleResetGroup">恢复默认</el-button>
          <el-button type="primary" size="small" :loading="saving" @click="handleSaveAll">
            保存全部
          </el-button>
        </div>
      </div>

      <div class="main-scroll">
        <template v-for="sec in sections" :key="sec.key">
          <div v-if="sec.items.length" class="sec-title">{{ sec.label }}</div>
          <div v-if="sec.items.length" class="sec-card">
            <div
              v-for="item in sec.items"
              :key="item.key"
              class="cfg-row"
              :class="{ dirty: isDirty(item) }"
            >
              <div class="row-meta">
                <div class="row-label-row">
                  <span class="row-label">{{ item.label }}</span>
                  <span v-if="isDirty(item)" class="row-dirty-tag">已修改</span>
                </div>
                <p class="row-desc">{{ item.description }}</p>
                <p v-if="isDirty(item)" class="row-default">默认值 {{ item.default }}</p>
              </div>

              <div class="row-input">
                <el-switch
                  v-if="item.value_type === 'bool'"
                  :model-value="drafts[item.key] === 'true'"
                  active-text="开启"
                  inactive-text="关闭"
                  @change="(v: any) => (drafts[item.key] = String(v))"
                />
                <el-input-number
                  v-else-if="isNumeric(item)"
                  :model-value="Number(drafts[item.key])"
                  :step="item.value_type === 'int' ? 1 : 0.1"
                  :precision="item.value_type === 'int' ? 0 : undefined"
                  @change="
                    (v: any) =>
                      (drafts[item.key] =
                        v === null || v === undefined || Number.isNaN(v) ? '' : String(v))
                  "
                />
                <el-input
                  v-else
                  :model-value="drafts[item.key]"
                  placeholder="请输入"
                  @change="(v: any) => (drafts[item.key] = v ?? '')"
                />
                <el-button
                  v-if="isDirty(item)"
                  text
                  size="small"
                  class="reset-one"
                  title="恢复该项默认值"
                  @click="handleResetItem(item)"
                >
                  重置
                </el-button>
              </div>
            </div>
          </div>
        </template>

        <div v-if="activeItems.length === 0" class="sec-empty">该分类暂无配置项</div>
      </div>
    </main>
  </div>
</template>

<style scoped lang="scss">
// 与知识库页 kb-body 同构: 左栏 + 主区
.settings-body {
  height: 100%;
  display: flex;
  background: var(--ink);
}

// ---- 左栏分类导航(同 kb-sidebar: 白底 + 右分割线) ----
.settings-nav {
  width: 200px;
  flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--hairline);
  padding: 12px 10px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  border: none;
  border-left: 2px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  text-align: left;
  transition:
    background 0.15s,
    color 0.15s;

  &:hover {
    background: var(--surface-2);
  }

  &.active {
    background: var(--brass-soft);
    border-left-color: var(--brass);

    .nav-title {
      color: var(--brass);
      font-weight: 600;
    }
  }
}

.nav-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.nav-title {
  font-size: 13px;
  color: var(--ink-text);
}

.nav-sub {
  font-size: 11px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-dirty {
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 8px;
  background: var(--brass);
  color: #fff;
  flex-shrink: 0;
}

// ---- 主区 ----
.settings-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.main-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--hairline);
  flex-shrink: 0;
}

.main-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-text);
}

.main-sub {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.main-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.main-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 12px 24px 40px;
}

.sec-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.3px;
  margin: 14px 2px 8px;

  &:first-child {
    margin-top: 4px;
  }
}

.sec-card {
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  overflow: hidden;
  box-shadow: var(--shadow-card);
}

.cfg-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--hairline);

  &:last-child {
    border-bottom: none;
  }

  &.dirty {
    background: rgba(37, 99, 235, 0.03);
  }
}

.row-meta {
  flex: 1;
  min-width: 0;
}

.row-label-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.row-label {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink-text);
}

.row-dirty-tag {
  font-size: 11px;
  padding: 0 8px;
  border-radius: 9px;
  line-height: 18px;
  color: var(--brass);
  background: var(--brass-soft);
  flex-shrink: 0;
}

.row-desc {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

.row-default {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--warning);
}

.row-input {
  flex-shrink: 0;
  width: 240px;
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
}

.row-input :deep(.el-input-number) {
  width: 180px;
}

.row-input :deep(.el-input) {
  width: 240px;
}

.reset-one {
  padding: 0 4px;
  font-size: 11px;
}

.sec-empty {
  padding: 24px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}
</style>
