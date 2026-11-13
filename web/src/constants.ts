import type { CompareStatus, ConfigKind, ConfigScope, ExpiryStatus, ScopeType, TagType, Tier } from '@/api/types'

export const MODE_TEXT: Record<string, string> = { incremental: '累加构建', full: '全量构建' }

export const STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  running: '构建中',
  applying: '发布中',
  verifying: '验证中',
  pending_approval: '待审批',
  rejected: '已拒绝',
  expired: '审批超时',
  unknown: '结果待确认',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
}

export const STATUS_TAG: Record<string, TagType> = {
  queued: 'info',
  running: 'primary',
  applying: 'primary',
  verifying: 'primary',
  pending_approval: 'warning',
  rejected: 'danger',
  expired: 'info',
  unknown: 'warning',
  success: 'success',
  failed: 'danger',
  cancelled: 'info',
}

export const JOB_STATUSES = ['running', 'queued', 'success', 'failed', 'cancelled'] as const
export const JOB_FINAL = new Set(['success', 'failed', 'cancelled'])
export const DEPLOYMENT_ACTIVE = new Set(['queued', 'applying', 'verifying'])
export const DEPLOYMENT_STATUSES = ['pending_approval', 'queued', 'applying', 'verifying', 'success', 'failed',
  'rejected', 'expired', 'cancelled', 'unknown'] as const

export const DEPLOYMENT_KIND_TEXT: Record<string, string> = {
  deploy: '发布', rollback: '回滚', restart: '重新发布', adopt: '接管发布',
}

// ------------------------------------------------------------------ 服务与运行状态
export const SERVICE_MODE_TEXT: Record<string, string> = { managed: '托管', image_only: '纳管' }
export const SERVICE_MODE_HINT: Record<string, string> = {
  managed: '平台按模板生成并下发 Deployment、Service、Ingress、HPA 与配置对象',
  image_only: '关联集群中已有的 Deployment，发布时只更新容器镜像',
}
export const RUNTIME_STATE_TEXT: Record<string, string> = {
  healthy: '运行正常', progressing: '滚动中', degraded: '部分可用', failed: '滚动失败', stopped: '已停止',
  paused: '已暂停', missing: '未找到工作负载', unknown: '无法读取',
}
export const RUNTIME_STATE_TAG: Record<string, TagType> = {
  healthy: 'success', progressing: 'primary', degraded: 'warning', failed: 'danger', stopped: 'info', paused: 'info',
  missing: 'info', unknown: 'info',
}
export const LEVEL_TAG: Record<string, TagType> = { ok: 'success', warning: 'warning', error: 'danger' }
export const CONTAINER_STATE_TEXT: Record<string, string> = {
  running: '运行中', waiting: '等待', terminated: '已终止', unknown: '未知',
}
export const APPLY_ACTION_TEXT: Record<string, string> = {
  create: '新建', update: '更新', unchanged: '无变化', skip: '保持线上', delete: '删除', error: '失败',
}
export const APPLY_ACTION_TAG: Record<string, TagType> = {
  create: 'success', update: 'warning', unchanged: 'info', skip: 'info', delete: 'danger', error: 'danger',
}
export const CONFIG_CHANGE_TEXT: Record<string, string> = {
  updated: '有新版本', added: '新增', removed: '已删除', replaced: '被下层同名配置替换',
}
export const PROBE_TYPES: [string, string][] = [['http', 'HTTP(S) 请求'], ['tcp', 'TCP 端口'], ['none', '不检查']]
export const SERVICE_TYPES = ['ClusterIP', 'NodePort', 'LoadBalancer'] as const
export const PULL_POLICIES = ['IfNotPresent', 'Always', 'Never'] as const
export const IMPORT_ACTION_TEXT: Record<string, string> = {
  create: '新建托管服务', convert: '纳管服务转为托管', skip: '已托管，跳过', unsupported: '模板不支持',
}
export const IMPORT_ACTION_TAG: Record<string, TagType> = {
  create: 'success', convert: 'primary', skip: 'info', unsupported: 'danger',
}

export const BUILD_STEPS: [string, string][] = [
  ['resolve', '解析版本'],
  ['login', '登录仓库'],
  ['build', '构建镜像'],
  ['push', '推送镜像'],
  ['done', '完成'],
]

export const TIERS: [Tier, string][] = [
  ['dev', '开发'],
  ['test', '测试'],
  ['staging', 'UAT（预生产）'],
  ['prod', '生产'],
]
export const TIER_TEXT: Record<string, string> = Object.fromEntries(TIERS)
export const TIER_TAG: Record<string, TagType> = { dev: 'info', test: 'primary', staging: 'warning', prod: 'danger' }

export const SCOPE_TEXT: Record<ScopeType, string> = { global: '全局', project: '项目', namespace: '命名空间' }

export const WEEKDAYS: [number, string][] = [
  [1, '周一'], [2, '周二'], [3, '周三'], [4, '周四'], [5, '周五'], [6, '周六'], [7, '周日'],
]

export const TAG_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/

// ------------------------------------------------------------------ 环境与配置
export const CONFIG_SCOPE_TEXT: Record<ConfigScope, string> = {
  platform: '平台', project: '项目', environment: '环境', binding: '资源绑定', service: '服务',
}
export const CONFIG_SCOPE_TAG: Record<ConfigScope, TagType> = {
  platform: 'info', project: 'primary', environment: 'success', binding: 'danger', service: 'warning',
}
export const CONFIG_KIND_TEXT: Record<ConfigKind, string> = {
  env: '环境变量', secret_env: '敏感环境变量', files: '配置文件', secret_files: '敏感文件',
}
export const CONFIG_KIND_HINT: Record<ConfigKind, string> = {
  env: '合并后写入每个服务的 <服务>-configmap，以 envFrom 注入容器；下层覆盖上层同名的键',
  secret_env: '合并后写入每个服务的 <服务>-secrets；值加密保存，界面默认不显示',
  files: '生成同名 ConfigMap，按文件挂载到容器（例如公共 properties 文件）；下层同名配置整体替换上层',
  secret_files: '生成同名 Secret，按文件挂载（例如证书目录）；文件通常引用证书库中的证书或密钥',
}
export const EXPIRY_TEXT: Record<ExpiryStatus, string> = {
  ok: '有效', warning: '90 天内到期', expiring: '30 天内到期', expired: '已过期', unknown: '未知',
}
export const EXPIRY_TAG: Record<ExpiryStatus, TagType> = {
  ok: 'success', warning: 'warning', expiring: 'danger', expired: 'danger', unknown: 'info',
}
export const COMPARE_TEXT: Record<CompareStatus, string> = {
  same: '相同', different: '不同', left_only: '仅左侧', right_only: '仅右侧', hidden: '无权比较',
}
export const COMPARE_TAG: Record<CompareStatus, TagType> = {
  same: 'success', different: 'warning', left_only: 'info', right_only: 'info', hidden: 'info',
}
