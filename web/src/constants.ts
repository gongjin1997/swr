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

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'
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

export const JOB_FINAL = new Set(['success', 'failed', 'cancelled'])

export const BUILD_STEPS: [string, string][] = [
  ['resolve', '解析版本'],
  ['login', '登录仓库'],
  ['build', '构建镜像'],
  ['push', '推送镜像'],
  ['done', '完成'],
]

export const TIERS: [string, string][] = [
  ['dev', '开发'],
  ['test', '测试'],
  ['staging', '预发布'],
  ['prod', '生产'],
]
export const TIER_TEXT: Record<string, string> = Object.fromEntries(TIERS)

export const WEEKDAYS: [number, string][] = [
  [1, '周一'], [2, '周二'], [3, '周三'], [4, '周四'], [5, '周五'], [6, '周六'], [7, '周日'],
]

/** 授权范围 → 可授予的角色。与后端 services/bindings.ALLOWED_ROLES 一致。 */
export const SCOPE_ROLES: Record<string, [string, string][]> = {
  global: [['platform_admin', '平台管理员'], ['auditor', '审计员']],
  project: [['project_admin', '项目管理员'], ['developer', '开发者'], ['project_viewer', '项目只读']],
  namespace: [['namespace_owner', '命名空间管理员'], ['deployer', '部署员'], ['namespace_viewer', '命名空间只读']],
}
export const ROLE_TEXT: Record<string, string> = Object.fromEntries(Object.values(SCOPE_ROLES).flat())

export const TAG_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/
