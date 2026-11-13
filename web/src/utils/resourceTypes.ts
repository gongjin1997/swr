/**
 * 资源类型注册表
 * 与后端 delivery/resources.py 的 RESOURCE_TYPES 保持一致
 */

import type { ResourceType } from '@/api/types'

export interface AttributeDefinition {
  label: string
  type: 'string' | 'integer' | 'boolean'
  required?: boolean
  default?: any
  secret?: boolean
  description?: string
}

export interface ResourceTypeDefinition {
  label: string
  description: string
  attributes: Record<string, AttributeDefinition>
  parent_type?: ResourceType
  exports?: string[]
}

export const RESOURCE_TYPES: Record<ResourceType, ResourceTypeDefinition> = {
  gauss_db: {
    label: 'GaussDB 数据库',
    description: 'GaussDB 数据库实例',
    attributes: {
      host: { label: '主机地址', type: 'string', required: true },
      port: { label: '端口', type: 'integer', required: true, default: 5432 },
    },
    exports: ['DB_HOST', 'DB_PORT'],
  },
  gauss_schema: {
    label: 'GaussDB Schema',
    description: 'GaussDB 数据库 Schema',
    attributes: {
      database: { label: '数据库名', type: 'string', required: true },
      schema: { label: 'Schema 名', type: 'string', required: true, default: 'public' },
      gauss_db: { label: 'GaussDB 实例', type: 'string', required: true, description: '父资源：gauss_db' },
    },
    parent_type: 'gauss_db',
    exports: ['DB_NAME', 'DB_SCHEMA'],
  },
  gauss_user: {
    label: 'GaussDB 用户',
    description: 'GaussDB 数据库用户',
    attributes: {
      user: { label: '用户名', type: 'string', required: true },
      password: { label: '密码', type: 'string', required: true, secret: true },
      gauss_schema: { label: 'GaussDB Schema', type: 'string', required: true, description: '父资源：gauss_schema' },
    },
    parent_type: 'gauss_schema',
    exports: ['DB_USER', 'DB_PASSWORD', 'DB_URL'],
  },
  redis: {
    label: 'Redis',
    description: 'Redis 缓存实例',
    attributes: {
      host: { label: '主机地址', type: 'string', required: true },
      port: { label: '端口', type: 'integer', required: true, default: 6379 },
      password: { label: '密码', type: 'string', secret: true },
      db: { label: '数据库编号', type: 'integer', default: 0 },
    },
    exports: ['REDIS_HOST', 'REDIS_PORT', 'REDIS_PASSWORD', 'REDIS_DB', 'REDIS_URL'],
  },
  kafka: {
    label: 'Kafka',
    description: 'Kafka 消息队列',
    attributes: {
      bootstrap_servers: { label: 'Bootstrap Servers', type: 'string', required: true, description: '逗号分隔的多个地址' },
      sasl_username: { label: 'SASL 用户名', type: 'string' },
      sasl_password: { label: 'SASL 密码', type: 'string', secret: true },
    },
    exports: ['KAFKA_BOOTSTRAP_SERVERS', 'KAFKA_SASL_USERNAME', 'KAFKA_SASL_PASSWORD'],
  },
  mqs: {
    label: 'MQS',
    description: '华为云 MQS（RabbitMQ）',
    attributes: {
      host: { label: '主机地址', type: 'string', required: true },
      port: { label: '端口', type: 'integer', required: true, default: 5672 },
      vhost: { label: 'Virtual Host', type: 'string', default: '/' },
      username: { label: '用户名', type: 'string', required: true },
      password: { label: '密码', type: 'string', required: true, secret: true },
    },
    exports: ['MQS_HOST', 'MQS_PORT', 'MQS_VHOST', 'MQS_USERNAME', 'MQS_PASSWORD', 'MQS_URL'],
  },
  config_center: {
    label: '配置中心',
    description: '华为云 CSE 配置中心',
    attributes: {
      server_addr: { label: '服务地址', type: 'string', required: true },
      namespace: { label: '命名空间', type: 'string', required: true },
      ak: { label: 'Access Key', type: 'string', required: true, secret: true },
      sk: { label: 'Secret Key', type: 'string', required: true, secret: true },
    },
    exports: ['CONFIG_CENTER_ADDR', 'CONFIG_CENTER_NAMESPACE', 'CONFIG_CENTER_AK', 'CONFIG_CENTER_SK'],
  },
  iam_endpoint: {
    label: 'IAM Endpoint',
    description: '华为云 IAM 认证端点',
    attributes: {
      endpoint: { label: 'IAM Endpoint', type: 'string', required: true },
      project_id: { label: 'Project ID', type: 'string', required: true },
    },
    exports: ['IAM_ENDPOINT', 'IAM_PROJECT_ID'],
  },
  endpoint: {
    label: '通用 Endpoint',
    description: '通用服务端点（HTTP/HTTPS）',
    attributes: {
      url: { label: 'URL', type: 'string', required: true },
      token: { label: '认证 Token', type: 'string', secret: true },
    },
    exports: ['ENDPOINT_URL', 'ENDPOINT_TOKEN'],
  },
}

export function getResourceTypeLabel(type: ResourceType): string {
  return RESOURCE_TYPES[type]?.label || type
}

export function getResourceTypeDescription(type: ResourceType): string {
  return RESOURCE_TYPES[type]?.description || ''
}

export function getResourceAttributes(type: ResourceType): Record<string, AttributeDefinition> {
  return RESOURCE_TYPES[type]?.attributes || {}
}

export function getSecretAttributes(type: ResourceType): string[] {
  const attributes = getResourceAttributes(type)
  return Object.entries(attributes)
    .filter(([_, def]) => def.secret)
    .map(([key]) => key)
}

export function maskSecrets(type: ResourceType, attributes: Record<string, any>): Record<string, any> {
  const secretKeys = getSecretAttributes(type)
  const masked = { ...attributes }
  secretKeys.forEach(key => {
    if (masked[key] !== undefined) {
      masked[key] = '******'
    }
  })
  return masked
}

export function getParentType(type: ResourceType): ResourceType | undefined {
  return RESOURCE_TYPES[type]?.parent_type
}

export function hasParent(type: ResourceType): boolean {
  return getParentType(type) !== undefined
}

export function getExportKeys(type: ResourceType): string[] {
  return RESOURCE_TYPES[type]?.exports || []
}

export function getResourceTypeOptions(): { label: string; value: ResourceType }[] {
  return Object.entries(RESOURCE_TYPES).map(([key, def]) => ({
    label: def.label,
    value: key as ResourceType,
  }))
}
