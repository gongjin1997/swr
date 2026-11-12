<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { resourceApi } from '@/api'
import type { CreateResourceRequest, Resource, ResourceScopeType, ResourceType, UpdateResourceRequest } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { useAction } from '@/composables/useAction'
import { getResourceAttributes, getResourceTypeOptions, type AttributeDefinition } from '@/utils/resourceTypes'

const route = useRoute()
const router = useRouter()
const resourceId = computed(() => route.params.id ? Number(route.params.id) : null)
const isEdit = computed(() => !!resourceId.value)

const resource = ref<Resource | null>(null)
const loading = ref(false)

const form = reactive<{
  scope_type: ResourceScopeType
  scope_id?: number
  type: ResourceType | ''
  name: string
  description: string
  attributes: Record<string, any>
  note: string
}>({
  scope_type: 'platform',
  scope_id: undefined,
  type: '',
  name: '',
  description: '',
  attributes: {},
  note: '',
})

const attributeDefs = computed<Record<string, AttributeDefinition>>(() => {
  return form.type ? getResourceAttributes(form.type) : {}
})

async function loadResource() {
  if (!resourceId.value) return
  loading.value = true
  try {
    resource.value = await resourceApi.get(resourceId.value)
    form.scope_type = resource.value.scope_type
    form.scope_id = resource.value.scope_id
    form.type = resource.value.type
    form.name = resource.value.name
    form.description = resource.value.description
    form.attributes = { ...resource.value.attributes }
  } catch (e) {
    ElMessage.error('加载资源失败')
    console.error(e)
  } finally {
    loading.value = false
  }
}

const [doSave, saving] = useAction(async () => {
  // 提取属性值
  const attributes: Record<string, any> = {}
  Object.entries(attributeDefs.value).forEach(([key, def]) => {
    const value = form.attributes[key]
    if (value !== undefined && value !== '') {
      attributes[key] = value
    } else if (def.default !== undefined) {
      attributes[key] = def.default
    }
  })

  if (isEdit.value && resourceId.value && resource.value) {
    const data: UpdateResourceRequest = {
      base_version: resource.value.version,
      attributes,
      note: form.note || '更新资源属性',
      description: form.description,
    }
    await resourceApi.update(resourceId.value, data)
    ElMessage.success('资源已更新')
    router.push(`/resources/${resourceId.value}`)
  } else {
    const data: CreateResourceRequest = {
      scope_type: form.scope_type,
      scope_id: form.scope_type === 'environment' ? form.scope_id : undefined,
      type: form.type as ResourceType,
      name: form.name,
      description: form.description,
      attributes,
      note: form.note || '创建资源',
    }
    const created = await resourceApi.create(data)
    ElMessage.success('资源已创建')
    router.push(`/resources/${created.id}`)
  }
}, { confirm: false })

function validateName(_rule: any, value: string, callback: any) {
  if (!value) {
    callback(new Error('请输入资源名称'))
  } else if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/.test(value)) {
    callback(new Error('资源名称必须是 DNS 子域名格式（小写字母、数字、连字符）'))
  } else {
    callback()
  }
}

const rules = computed(() => {
  const baseRules: any = {
    scope_type: [{ required: true, message: '请选择作用域类型' }],
    type: [{ required: true, message: '请选择资源类型' }],
    name: [{ validator: validateName, trigger: 'blur' }],
  }

  if (form.scope_type === 'environment') {
    baseRules.scope_id = [{ required: true, message: '请输入环境 ID', type: 'number' }]
  }

  // 动态添加属性验证规则
  Object.entries(attributeDefs.value).forEach(([key, def]) => {
    if (def.required) {
      baseRules[`attributes.${key}`] = [
        { required: true, message: `请输入${def.label}` },
      ]
    }
  })

  return baseRules
})

onMounted(loadResource)
</script>

<template>
  <PageHeader :title="isEdit ? '编辑资源' : '创建资源'" />

  <el-card v-loading="loading">
    <el-form :model="form" :rules="rules" label-width="120px" style="max-width: 600px">
      <template v-if="!isEdit">
        <el-form-item label="作用域类型" prop="scope_type">
          <el-radio-group v-model="form.scope_type">
            <el-radio-button label="platform">平台</el-radio-button>
            <el-radio-button label="environment">环境</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="form.scope_type === 'environment'" label="环境 ID" prop="scope_id">
          <el-input-number v-model="form.scope_id" :min="1" placeholder="请输入环境 ID" style="width: 100%" />
        </el-form-item>

        <el-form-item label="资源类型" prop="type">
          <el-select v-model="form.type" placeholder="请选择资源类型" filterable style="width: 100%">
            <el-option
              v-for="option in getResourceTypeOptions()"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="资源名称" prop="name">
          <el-input v-model="form.name" placeholder="例如：prod-db, cache-redis" />
        </el-form-item>
      </template>

      <el-form-item label="描述">
        <el-input v-model="form.description" type="textarea" :rows="2" placeholder="资源的简要描述（可选）" />
      </el-form-item>

      <template v-if="form.type">
        <el-divider content-position="left">资源属性</el-divider>

        <el-form-item
          v-for="(def, key) in attributeDefs"
          :key="key"
          :label="def.label"
          :prop="`attributes.${key}`"
        >
          <template v-if="def.type === 'integer'">
            <el-input-number v-model="form.attributes[key]" :placeholder="`请输入${def.label}`" style="width: 100%" />
          </template>
          <template v-else-if="def.type === 'boolean'">
            <el-switch v-model="form.attributes[key]" />
          </template>
          <template v-else-if="def.secret">
            <el-input
              v-model="form.attributes[key]"
              type="password"
              :placeholder="`请输入${def.label}`"
              show-password
              autocomplete="new-password"
            />
          </template>
          <template v-else>
            <el-input v-model="form.attributes[key]" :placeholder="`请输入${def.label}`" />
          </template>
          <div v-if="def.description" class="el-form-item__description">{{ def.description }}</div>
        </el-form-item>
      </template>

      <el-form-item v-if="isEdit" label="更新说明">
        <el-input v-model="form.note" type="textarea" :rows="2" placeholder="描述本次修改的原因或内容" />
      </el-form-item>

      <el-form-item>
        <el-button type="primary" @click="doSave" :loading="saving">
          {{ isEdit ? '保存' : '创建' }}
        </el-button>
        <el-button @click="router.back()">取消</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<style scoped>
.el-form-item__description {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
</style>
