<script setup lang="ts">
/**
 * 增强配置管理页面：支持多维度配置模型
 *
 * 功能：
 * - 按作用域查看配置（平台/项目/环境/服务）
 * - 多维度筛选（stage/kind/process_type）
 * - 配置优先级显示
 * - 配置锁定管理
 * - 配置模板应用
 * - 配置导入导出
 * - 配置变更历史
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { Delete, Download, Edit, Lock, Plus, Refresh, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'

// API 定义
interface ConfigEntry {
  id: number
  scope_type: string
  scope_id: number
  stage: string
  priority: number
  kind: string
  process_type: string
  key: string
  value: string
  locked: boolean
  description: string
  version: number
  created_at: string
  updated_at: string
}

interface ConfigTemplate {
  id: number
  name: string
  description: string
  category: string
  scope_type: string
}

// Props
interface Props {
  scopeType?: string  // 'platform' | 'project' | 'environment' | 'service'
  scopeId?: number
}

const props = withDefaults(defineProps<Props>(), {
  scopeType: 'service',
  scopeId: 0,
})

// State
const loading = ref(false)
const configs = ref<ConfigEntry[]>([])
const templates = ref<ConfigTemplate[]>([])

// 筛选条件
const filters = reactive({
  stage: '',
  kind: '',
  process_type: '',
  keyword: '',
})

// 对话框
const dialogVisible = ref(false)
const dialogTitle = ref('')
const dialogType = ref<'create' | 'edit'>('create')
const formRef = ref<FormInstance>()

const form = reactive({
  id: 0,
  key: '',
  value: '',
  stage: 'app',
  kind: 'common',
  process_type: 'env',
  locked: false,
  description: '',
})

const formRules = {
  key: [{ required: true, message: '请输入配置键', trigger: 'blur' }],
  value: [{ required: true, message: '请输入配置值', trigger: 'blur' }],
}

// 模板对话框
const templateDialogVisible = ref(false)
const selectedTemplate = ref<number>()

// 导入导出
const importDialogVisible = ref(false)
const importData = ref('')
const importFormat = ref<'json' | 'yaml'>('json')

// Computed
const filteredConfigs = computed(() => {
  let result = configs.value

  if (filters.stage) {
    result = result.filter(c => c.stage === filters.stage)
  }

  if (filters.kind) {
    result = result.filter(c => c.kind === filters.kind)
  }

  if (filters.process_type) {
    result = result.filter(c => c.process_type === filters.process_type)
  }

  if (filters.keyword) {
    const keyword = filters.keyword.toLowerCase()
    result = result.filter(c =>
      c.key.toLowerCase().includes(keyword) ||
      c.value.toLowerCase().includes(keyword) ||
      c.description.toLowerCase().includes(keyword)
    )
  }

  return result
})

const stageOptions = [
  { label: '应用阶段', value: 'app' },
  { label: '初始化阶段', value: 'init' },
]

const kindOptions = [
  { label: '普通配置', value: 'common' },
  { label: '敏感配置', value: 'secret' },
]

const processTypeOptions = [
  { label: '环境变量', value: 'env' },
  { label: '配置文件', value: 'file' },
  { label: '模板占位符', value: 'template' },
]

const priorityText = (priority: number) => {
  const map: Record<number, string> = {
    1: '默认',
    2: '服务普通',
    3: '服务敏感',
    4: '环境普通',
    5: '环境敏感',
    6: '平台普通',
    7: '平台敏感',
  }
  return map[priority] || priority.toString()
}

const priorityType = (priority: number) => {
  if (priority >= 6) return 'danger'
  if (priority >= 4) return 'warning'
  return ''
}

// Methods
async function loadConfigs() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    params.append('scope_type', props.scopeType)
    params.append('scope_id', props.scopeId.toString())

    const response = await fetch(`/api/configs?${params}`)
    const data = await response.json()
    configs.value = data
  } catch (error) {
    ElMessage.error('加载配置失败')
    console.error(error)
  } finally {
    loading.value = false
  }
}

async function loadTemplates() {
  try {
    const response = await fetch('/api/configs/templates')
    const data = await response.json()
    templates.value = data
  } catch (error) {
    console.error('加载模板失败', error)
  }
}

function handleCreate() {
  dialogType.value = 'create'
  dialogTitle.value = '创建配置'
  Object.assign(form, {
    id: 0,
    key: '',
    value: '',
    stage: 'app',
    kind: 'common',
    process_type: 'env',
    locked: false,
    description: '',
  })
  dialogVisible.value = true
}

function handleEdit(row: ConfigEntry) {
  dialogType.value = 'edit'
  dialogTitle.value = '编辑配置'
  Object.assign(form, {
    id: row.id,
    key: row.key,
    value: row.value,
    stage: row.stage,
    kind: row.kind,
    process_type: row.process_type,
    locked: row.locked,
    description: row.description,
  })
  dialogVisible.value = true
}

async function handleDelete(row: ConfigEntry) {
  try {
    await ElMessageBox.confirm(
      `确定删除配置 "${row.key}" 吗？`,
      '删除确认',
      { type: 'warning' }
    )

    const response = await fetch(`/api/configs/${row.id}`, {
      method: 'DELETE',
    })

    if (!response.ok) throw new Error('删除失败')

    ElMessage.success('删除成功')
    await loadConfigs()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
      console.error(error)
    }
  }
}

async function handleSubmit() {
  if (!formRef.value) return

  await formRef.value.validate(async (valid) => {
    if (!valid) return

    try {
      const url = dialogType.value === 'create'
        ? '/api/configs'
        : `/api/configs/${form.id}`

      const method = dialogType.value === 'create' ? 'POST' : 'PUT'

      const body = dialogType.value === 'create'
        ? {
            scope_type: props.scopeType,
            scope_id: props.scopeId,
            key: form.key,
            value: form.value,
            stage: form.stage,
            kind: form.kind,
            process_type: form.process_type,
            locked: form.locked,
            description: form.description,
          }
        : {
            value: form.value,
            kind: form.kind,
            process_type: form.process_type,
            locked: form.locked,
            description: form.description,
          }

      const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || '操作失败')
      }

      ElMessage.success(dialogType.value === 'create' ? '创建成功' : '更新成功')
      dialogVisible.value = false
      await loadConfigs()
    } catch (error: any) {
      ElMessage.error(error.message || '操作失败')
      console.error(error)
    }
  })
}

async function handleApplyTemplate() {
  if (!selectedTemplate.value) {
    ElMessage.warning('请选择模板')
    return
  }

  try {
    const response = await fetch('/api/configs/templates/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        template_id: selectedTemplate.value,
        scope_type: props.scopeType,
        scope_id: props.scopeId,
      }),
    })

    if (!response.ok) throw new Error('应用模板失败')

    const result = await response.json()
    ElMessage.success(`已应用模板，创建了 ${result.length} 个配置`)
    templateDialogVisible.value = false
    await loadConfigs()
  } catch (error) {
    ElMessage.error('应用模板失败')
    console.error(error)
  }
}

async function handleExport(format: 'json' | 'yaml') {
  try {
    const response = await fetch('/api/configs/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope_type: props.scopeType,
        scope_id: props.scopeId,
        format,
      }),
    })

    if (!response.ok) throw new Error('导出失败')

    const result = await response.json()
    const blob = new Blob([result.data], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `config_${props.scopeType}_${props.scopeId}.${format}`
    a.click()
    URL.revokeObjectURL(url)

    ElMessage.success('导出成功')
  } catch (error) {
    ElMessage.error('导出失败')
    console.error(error)
  }
}

async function handleImport() {
  if (!importData.value.trim()) {
    ElMessage.warning('请输入导入数据')
    return
  }

  try {
    const response = await fetch('/api/configs/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope_type: props.scopeType,
        scope_id: props.scopeId,
        data: importData.value,
        format: importFormat.value,
        overwrite: false,
      }),
    })

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || '导入失败')
    }

    const result = await response.json()
    ElMessage.success(`导入成功，创建了 ${result.length} 个配置`)
    importDialogVisible.value = false
    importData.value = ''
    await loadConfigs()
  } catch (error: any) {
    ElMessage.error(error.message || '导入失败')
    console.error(error)
  }
}

function resetFilters() {
  Object.assign(filters, {
    stage: '',
    kind: '',
    process_type: '',
    keyword: '',
  })
}

onMounted(() => {
  loadConfigs()
  loadTemplates()
})
</script>

<template>
  <div class="enhanced-config-page">
    <!-- 筛选栏 -->
    <el-card class="filter-card" shadow="never">
      <el-form :inline="true">
        <el-form-item label="阶段">
          <el-select v-model="filters.stage" placeholder="全部" clearable style="width: 120px">
            <el-option v-for="opt in stageOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="filters.kind" placeholder="全部" clearable style="width: 120px">
            <el-option v-for="opt in kindOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="处理方式">
          <el-select v-model="filters.process_type" placeholder="全部" clearable style="width: 140px">
            <el-option v-for="opt in processTypeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" placeholder="搜索键/值/描述" clearable style="width: 200px" />
        </el-form-item>
        <el-form-item>
          <el-button @click="resetFilters">重置</el-button>
          <el-button type="primary" :icon="Refresh" @click="loadConfigs">刷新</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 操作栏 -->
    <el-card class="action-card" shadow="never">
      <el-space>
        <el-button type="primary" :icon="Plus" @click="handleCreate">新建配置</el-button>
        <el-button :icon="Upload" @click="templateDialogVisible = true">应用模板</el-button>
        <el-dropdown @command="handleExport">
          <el-button :icon="Download">
            导出 <el-icon class="el-icon--right"><arrow-down /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="json">导出为 JSON</el-dropdown-item>
              <el-dropdown-item command="yaml">导出为 YAML</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button :icon="Upload" @click="importDialogVisible = true">导入</el-button>
      </el-space>
      <div class="stats">
        <span>共 {{ filteredConfigs.length }} 个配置</span>
      </div>
    </el-card>

    <!-- 配置列表 -->
    <el-card shadow="never">
      <el-table :data="filteredConfigs" v-loading="loading" stripe>
        <el-table-column prop="key" label="配置键" min-width="180" />
        <el-table-column prop="value" label="配置值" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.kind === 'secret'">******</span>
            <span v-else>{{ row.value }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="stage" label="阶段" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ row.stage === 'app' ? '应用' : '初始化' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="priorityType(row.priority)">
              {{ priorityText(row.priority) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="kind" label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kind === 'secret' ? 'danger' : ''">
              {{ row.kind === 'secret' ? '敏感' : '普通' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="process_type" label="处理方式" width="120">
          <template #default="{ row }">
            {{ row.process_type === 'env' ? '环境变量' : row.process_type === 'file' ? '文件' : '模板' }}
          </template>
        </el-table-column>
        <el-table-column prop="locked" label="锁定" width="80" align="center">
          <template #default="{ row }">
            <el-icon v-if="row.locked" color="var(--el-color-warning)"><Lock /></el-icon>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="150" show-overflow-tooltip />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :icon="Edit" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" :icon="Delete" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="600px">
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-form-item label="配置键" prop="key">
          <el-input v-model="form.key" :disabled="dialogType === 'edit'" placeholder="例如：DATABASE_HOST" />
        </el-form-item>
        <el-form-item label="配置值" prop="value">
          <el-input
            v-model="form.value"
            type="textarea"
            :rows="3"
            placeholder="配置的值"
            :show-password="form.kind === 'secret'"
          />
        </el-form-item>
        <el-form-item label="阶段">
          <el-radio-group v-model="form.stage">
            <el-radio label="app">应用阶段</el-radio>
            <el-radio label="init">初始化阶段</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="form.kind">
            <el-radio label="common">普通配置</el-radio>
            <el-radio label="secret">敏感配置</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="处理方式">
          <el-radio-group v-model="form.process_type">
            <el-radio label="env">环境变量</el-radio>
            <el-radio label="file">配置文件</el-radio>
            <el-radio label="template">模板占位符</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="锁定">
          <el-switch v-model="form.locked" />
          <div class="form-tip">锁定后下层作用域无法覆盖此配置</div>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="配置的说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 应用模板对话框 -->
    <el-dialog v-model="templateDialogVisible" title="应用配置模板" width="600px">
      <el-form label-width="100px">
        <el-form-item label="选择模板">
          <el-select v-model="selectedTemplate" placeholder="请选择模板" style="width: 100%">
            <el-option
              v-for="tpl in templates"
              :key="tpl.id"
              :label="tpl.name"
              :value="tpl.id"
            >
              <div>
                <div>{{ tpl.name }}</div>
                <div style="font-size: 12px; color: var(--el-text-color-secondary)">{{ tpl.description }}</div>
              </div>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="templateDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleApplyTemplate">应用</el-button>
      </template>
    </el-dialog>

    <!-- 导入对话框 -->
    <el-dialog v-model="importDialogVisible" title="导入配置" width="700px">
      <el-form label-width="100px">
        <el-form-item label="格式">
          <el-radio-group v-model="importFormat">
            <el-radio label="json">JSON</el-radio>
            <el-radio label="yaml">YAML</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="数据">
          <el-input
            v-model="importData"
            type="textarea"
            :rows="15"
            placeholder="粘贴配置数据"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.enhanced-config-page {
  padding: 20px;
}

.filter-card,
.action-card {
  margin-bottom: 16px;
}

.action-card :deep(.el-card__body) {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stats {
  color: var(--el-text-color-secondary);
  font-size: 14px;
}

.form-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
</style>
