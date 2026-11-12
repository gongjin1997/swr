/**
 * 资源列表页面
 */

import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Card, message, Modal, Space, Table, Tabs, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/resources'
import type { Resource } from '../api/resources'
import { getResourceTypeLabel, maskSecrets } from '../utils/resourceTypes'

const ResourceList: React.FC = () => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'platform' | 'environment'>('platform')

  const { data, isLoading } = useQuery({
    queryKey: ['resources', activeTab],
    queryFn: () => api.listResources({ scope_type: activeTab }),
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteResource,
    onSuccess: () => {
      message.success('资源已删除')
      queryClient.invalidateQueries({ queryKey: ['resources'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '删除失败')
    },
  })

  const handleDelete = (record: Resource) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除资源 "${record.name}" 吗？`,
      okText: '确定',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => deleteMutation.mutate(record.id),
    })
  }

  const columns: ColumnsType<Resource> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (text, record) => (
        <Button type="link" onClick={() => navigate(`/resources/${record.id}`)}>
          {text}
        </Button>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      render: (type) => <Tag>{getResourceTypeLabel(type)}</Tag>,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 80,
    },
    {
      title: '作用域',
      key: 'scope',
      width: 150,
      render: (_, record) => {
        if (record.scope_type === 'platform') {
          return <Tag color="blue">平台</Tag>
        }
        return (
          <Tooltip title={`环境 ID: ${record.scope_id}`}>
            <Tag color="green">环境</Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '使用情况',
      dataIndex: 'binding_count',
      key: 'binding_count',
      width: 100,
      render: (count) => (count !== undefined ? `${count} 个服务` : '-'),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (text) => new Date(text).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      fixed: 'right',
      render: (_, record) => (
        <Space>
          <Tooltip title="查看详情">
            <Button
              type="text"
              size="small"
              icon={<EyeOutlined />}
              onClick={() => navigate(`/resources/${record.id}`)}
            />
          </Tooltip>
          {record.can_edit && (
            <>
              <Tooltip title="编辑">
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => navigate(`/resources/${record.id}/edit`)}
                />
              </Tooltip>
              <Tooltip title="删除">
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => handleDelete(record)}
                  loading={deleteMutation.isPending}
                />
              </Tooltip>
            </>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="资源管理"
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => navigate('/resources/new')}
        >
          创建资源
        </Button>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as 'platform' | 'environment')}
        items={[
          {
            key: 'platform',
            label: '平台资源',
            children: (
              <Table
                columns={columns}
                dataSource={data?.items || []}
                rowKey="id"
                loading={isLoading}
                pagination={{
                  showSizeChanger: true,
                  showTotal: (total) => `共 ${total} 条`,
                }}
              />
            ),
          },
          {
            key: 'environment',
            label: '环境资源',
            children: (
              <Table
                columns={columns}
                dataSource={data?.items || []}
                rowKey="id"
                loading={isLoading}
                pagination={{
                  showSizeChanger: true,
                  showTotal: (total) => `共 ${total} 条`,
                }}
              />
            ),
          },
        ]}
      />
    </Card>
  )
}

export default ResourceList
