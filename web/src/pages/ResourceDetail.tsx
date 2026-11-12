/**
 * 资源详情页面
 */

import { DeleteOutlined, EditOutlined, EyeInvisibleOutlined, EyeOutlined } from '@ant-design/icons'
import { Button, Card, Descriptions, message, Modal, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import React, { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/resources'
import type { Resource, ResourceVersion } from '../api/resources'
import { getResourceTypeLabel, maskSecrets, getSecretAttributes } from '../utils/resourceTypes'

const { Text } = Typography

const ResourceDetail: React.FC = () => {
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const [revealed, setRevealed] = useState(false)
  const [revealedData, setRevealedData] = useState<Record<string, any> | null>(null)

  const { data: resource, isLoading } = useQuery({
    queryKey: ['resource', id],
    queryFn: () => api.getResource(Number(id)),
    enabled: !!id,
  })

  const { data: versions } = useQuery({
    queryKey: ['resource-versions', id],
    queryFn: () => api.listResourceVersions(Number(id)),
    enabled: !!id,
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteResource,
    onSuccess: () => {
      message.success('资源已删除')
      queryClient.invalidateQueries({ queryKey: ['resources'] })
      navigate('/resources')
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '删除失败')
    },
  })

  const revealMutation = useMutation({
    mutationFn: api.revealResource,
    onSuccess: (data) => {
      setRevealedData(data.attributes)
      setRevealed(true)
      message.success('敏感信息已解密')
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '解密失败')
    },
  })

  const handleDelete = () => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除资源 "${resource?.name}" 吗？`,
      okText: '确定',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => deleteMutation.mutate(Number(id)),
    })
  }

  const handleReveal = () => {
    if (revealed) {
      setRevealed(false)
      setRevealedData(null)
    } else {
      revealMutation.mutate(Number(id))
    }
  }

  const renderAttributes = () => {
    if (!resource) return null

    const attrs = revealed && revealedData ? revealedData : resource.attributes
    const secretKeys = getSecretAttributes(resource.type)

    return (
      <Descriptions column={1} bordered>
        {Object.entries(attrs).map(([key, value]) => {
          const isSecret = secretKeys.includes(key)
          const displayValue = isSecret && !revealed ? '******' : String(value)

          return (
            <Descriptions.Item key={key} label={key}>
              <Text code={isSecret}>{displayValue}</Text>
            </Descriptions.Item>
          )
        })}
      </Descriptions>
    )
  }

  const versionColumns: ColumnsType<ResourceVersion> = [
    {
      title: '版本号',
      dataIndex: 'number',
      key: 'number',
      width: 100,
    },
    {
      title: '变更说明',
      dataIndex: 'note',
      key: 'note',
    },
    {
      title: 'Checksum',
      dataIndex: 'checksum',
      key: 'checksum',
      render: (text) => <Text code>{text.slice(0, 16)}...</Text>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (text) => new Date(text).toLocaleString('zh-CN'),
    },
  ]

  if (isLoading || !resource) {
    return <Card loading />
  }

  return (
    <Card
      title={
        <Space>
          <span>{resource.name}</span>
          <Tag>{getResourceTypeLabel(resource.type)}</Tag>
          {resource.scope_type === 'platform' ? (
            <Tag color="blue">平台</Tag>
          ) : (
            <Tag color="green">环境</Tag>
          )}
        </Space>
      }
      extra={
        <Space>
          {resource.can_reveal && (
            <Button
              icon={revealed ? <EyeInvisibleOutlined /> : <EyeOutlined />}
              onClick={handleReveal}
              loading={revealMutation.isPending}
            >
              {revealed ? '隐藏' : '显示'}敏感信息
            </Button>
          )}
          {resource.can_edit && (
            <>
              <Button
                type="primary"
                icon={<EditOutlined />}
                onClick={() => navigate(`/resources/${id}/edit`)}
              >
                编辑
              </Button>
              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={handleDelete}
                loading={deleteMutation.isPending}
              >
                删除
              </Button>
            </>
          )}
        </Space>
      }
    >
      <Tabs
        items={[
          {
            key: 'info',
            label: '基本信息',
            children: (
              <Space direction="vertical" size="large" style={{ width: '100%' }}>
                <Descriptions column={2} bordered>
                  <Descriptions.Item label="资源名称">{resource.name}</Descriptions.Item>
                  <Descriptions.Item label="资源类型">
                    {getResourceTypeLabel(resource.type)}
                  </Descriptions.Item>
                  <Descriptions.Item label="当前版本">{resource.version}</Descriptions.Item>
                  <Descriptions.Item label="使用情况">
                    {resource.binding_count !== undefined
                      ? `${resource.binding_count} 个服务`
                      : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="作用域" span={2}>
                    {resource.scope_type === 'platform'
                      ? '平台'
                      : `环境 (ID: ${resource.scope_id})`}
                  </Descriptions.Item>
                  <Descriptions.Item label="描述" span={2}>
                    {resource.description || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间">
                    {new Date(resource.created_at).toLocaleString('zh-CN')}
                  </Descriptions.Item>
                  <Descriptions.Item label="更新时间">
                    {new Date(resource.updated_at).toLocaleString('zh-CN')}
                  </Descriptions.Item>
                </Descriptions>

                <Card title="资源属性" size="small">
                  {renderAttributes()}
                </Card>
              </Space>
            ),
          },
          {
            key: 'versions',
            label: '版本历史',
            children: (
              <Table
                columns={versionColumns}
                dataSource={versions?.items || []}
                rowKey="number"
                pagination={false}
              />
            ),
          },
        ]}
      />
    </Card>
  )
}

export default ResourceDetail
