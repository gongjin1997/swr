import type { ScopeType, TagType, Tier } from '@/api/types'

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

export const DEPLOYMENT_KIND_TEXT: Record<string, string> = { deploy: '发布', rollback: '回滚', restart: '重新发布' }

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
