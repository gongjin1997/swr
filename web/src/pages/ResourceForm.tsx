/**
 * 资源表单页面（创建/编辑）
 */

import { Button, Card, Form, Input, InputNumber, message, Select, Switch } from 'antd'
import React, { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/resources'
import type { CreateResourceRequest, UpdateResourceRequest } from '../api/resources'
import { RESOURCE_TYPES, getResourceAttributes } from '../utils/resourceTypes'

const { TextArea } = Input

const ResourceForm: React.FC = () => {
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const [form] = Form.useForm()
  const isEdit = !!id

  const resourceType = Form.useWatch('type', form)
  const scopeType = Form.useWatch('scope_type', form)

  const { data: resource } = useQuery({
    queryKey: ['resource', id],
    queryFn: () => api.getResource(Number(id)),
    enabled: isEdit,
  })

  useEffect(() => {
    if (resource) {
      form.setFieldsValue({
        scope_type: resource.scope_type,
        scope_id: resource.scope_id,
        type: resource.type,
        name: resource.name,
        description: resource.description,
        ...resource.attributes,
      })
    }
  }, [resource, form])

  const createMutation = useMutation({
    mutationFn: api.createResource,
    onSuccess: () => {
      message.success('资源已创建')
      queryClient.invalidateQueries({ queryKey: ['resources'] })
      navigate('/resources')
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '创建失败')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: UpdateResourceRequest }) =>
      api.updateResource(id, data),
    onSuccess: () => {
      message.success('资源已更新')
      queryClient.invalidateQueries({ queryKey: ['resources'] })
      queryClient.invalidateQueries({ queryKey: ['resource', id] })
      navigate(`/resources/${id}`)
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '更新失败')
    },
  })

  const handleSubmit = (values: any) => {
    const { scope_type, scope_id, type, name, description, note, ...attributes } = values

    if (isEdit) {
      const data: UpdateResourceRequest = {
        base_version: resource!.version,
        attributes,
        note: note || '更新资源属性',
        description,
      }
      updateMutation.mutate({ id: Number(id), data })
    } else {
      const data: CreateResourceRequest = {
        scope_type,
        scope_id: scope_type === 'environment' ? scope_id : undefined,
        type,
        name,
        description,
        attributes,
        note: note || '创建资源',
      }
      createMutation.mutate(data)
    }
  }

  const renderAttributeFields = () => {
    if (!resourceType) return null

    const attributeDefs = getResourceAttributes(resourceType)

    return Object.entries(attributeDefs).map(([key, def]) => {
      const commonProps = {
        label: def.label,
        name: key,
        rules: [{ required: def.required, message: `请输入${def.label}` }],
        tooltip: def.description,
      }

      if (def.type === 'integer') {
        return (
          <Form.Item key={key} {...commonProps}>
            <InputNumber style={{ width: '100%' }} placeholder={`请输入${def.label}`} />
          </Form.Item>
        )
      }

      if (def.type === 'boolean') {
        return (
          <Form.Item key={key} {...commonProps} valuePropName="checked">
            <Switch />
          </Form.Item>
        )
      }

      // string type
      if (def.secret) {
        return (
          <Form.Item key={key} {...commonProps}>
            <Input.Password placeholder={`请输入${def.label}`} autoComplete="new-password" />
          </Form.Item>
        )
      }

      return (
        <Form.Item key={key} {...commonProps}>
          <Input placeholder={`请输入${def.label}`} />
        </Form.Item>
      )
    })
  }

  return (
    <Card title={isEdit ? '编辑资源' : '创建资源'}>
      <Form
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        initialValues={{
          scope_type: 'platform',
        }}
        style={{ maxWidth: 600 }}
      >
        {!isEdit && (
          <>
            <Form.Item
              label="作用域类型"
              name="scope_type"
              rules={[{ required: true, message: '请选择作用域类型' }]}
            >
              <Select
                options={[
                  { label: '平台', value: 'platform' },
                  { label: '环境', value: 'environment' },
                ]}
              />
            </Form.Item>

            {scopeType === 'environment' && (
              <Form.Item
                label="环境 ID"
                name="scope_id"
                rules={[{ required: true, message: '请输入环境 ID' }]}
              >
                <InputNumber style={{ width: '100%' }} placeholder="请输入环境 ID" />
              </Form.Item>
            )}

            <Form.Item
              label="资源类型"
              name="type"
              rules={[{ required: true, message: '请选择资源类型' }]}
            >
              <Select
                placeholder="请选择资源类型"
                options={Object.entries(RESOURCE_TYPES).map(([key, def]) => ({
                  label: def.label,
                  value: key,
                }))}
                showSearch
                filterOption={(input, option) =>
                  (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>

            <Form.Item
              label="资源名称"
              name="name"
              rules={[
                { required: true, message: '请输入资源名称' },
                {
                  pattern: /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/,
                  message: '资源名称必须是 DNS 子域名格式（小写字母、数字、连字符）',
                },
              ]}
            >
              <Input placeholder="例如：prod-db, cache-redis" />
            </Form.Item>
          </>
        )}

        <Form.Item label="描述" name="description">
          <TextArea rows={2} placeholder="资源的简要描述（可选）" />
        </Form.Item>

        {renderAttributeFields()}

        {isEdit && (
          <Form.Item
            label="更新说明"
            name="note"
            tooltip="描述本次修改的原因或内容"
          >
            <TextArea rows={2} placeholder="例如：更新数据库密码" />
          </Form.Item>
        )}

        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={createMutation.isPending || updateMutation.isPending}
            style={{ marginRight: 8 }}
          >
            {isEdit ? '保存' : '创建'}
          </Button>
          <Button onClick={() => navigate('/resources')}>取消</Button>
        </Form.Item>
      </Form>
    </Card>
  )
}

export default ResourceForm
