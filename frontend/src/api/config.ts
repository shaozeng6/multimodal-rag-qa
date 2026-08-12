import api from './index';

/** 单个配置项(前端设置页展示用) */
export interface ConfigItem {
  key: string;
  label: string;
  value: string;
  value_type: string; // str / int / float / bool
  description: string;
  apply_mode: string; // hot / restart
  default: string;
  group: string;
  section?: string; // 组内子分组(逻辑分域)
}

/** 配置分组 */
export interface ConfigGroup {
  label: string;
  items: ConfigItem[];
}

/** 全部配置: 按分组返回 */
export interface ConfigGroupsResult {
  groups: Record<string, ConfigGroup>;
}

/** 批量更新响应 */
export interface ConfigUpdateResult {
  updated: number;
  errors: { key?: string; error: string }[];
  restart_required: string[];
  message: string;
}

/**
 * 分组获取全部配置项(仅管理员)
 */
export async function getConfig(): Promise<ConfigGroupsResult> {
  const { data } = await api.get<ConfigGroupsResult>('/config');
  return data;
}

/**
 * 批量更新配置项(仅管理员), items=[{key, value}]
 */
export async function updateConfig(
  items: { key: string; value: string }[],
): Promise<ConfigUpdateResult> {
  const { data } = await api.put<ConfigUpdateResult>('/config', items);
  return data;
}

/**
 * 整组恢复默认值(仅管理员)
 */
export async function resetConfig(group: string): Promise<{
  reset: number;
  message: string;
}> {
  const { data } = await api.post(`/config/${group}/reset`);
  return data;
}
