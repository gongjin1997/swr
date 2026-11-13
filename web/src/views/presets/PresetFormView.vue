<script setup lang="ts">
import { Delete, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { presetApi } from '@/api'
import type { CreatePresetRequest, Preset } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { useAction } from '@/composables/useAction'

const route = useRoute()
const router = useRouter()
const presetId = computed(() => (route.params.id ? Number(route.params.id) : null))
const isEdit = computed(() => !!presetId.value)

const preset = ref<Preset | null>(null)
const loading = ref(false)

const form = reactive<CreatePresetRequest>({
  name: '',
  description: '',
  profile_template: {},
  default_config_sets: [],
})

const profileTemplateJson = ref('')

async function loadPreset() {
  if (!presetId.value) return
  loading.value = true
  try {
    preset.value = await presetApi.get(presetId.value)
    form.name = preset.value.name
    form.description = preset.value.description
    form.profile_template = preset.value.profile_template
    form.default_config_sets = [...preset.value.default_config_sets]
    form.base_version = preset.value.version
    profileTemplateJson.value = JSON.stringify(preset.value.profile_template, null, 2)
  } catch (e) {
    ElMessage.error('加载预置失败')
    console.error(e)
  } finally {
    loading.value = false
  }
}

const saveAction = useAction()

async function handleSave() {
  await saveAction.run(async () => {
    // 解析 profile_template JSON
    try {
      form.profile_template = profileTemplateJson.value ? JSON.parse(profileTemplateJson.value) : {}
    } catch (e) {
      ElMessage.error('Profile Template JSON 格式错误')
      return
    }

    if (isEdit.value && presetId.value) {
      await presetApi.update(presetId.value, form)
      ElMessage.success('预置已更新')
      router.push('/presets')
    } else {
      await presetApi.create(form)
      ElMessage.success('预置已创建')
      router.push('/presets')
    }
  })
}

function addConfigSet() {
  form.default_config_sets.push({
    kind: 'env',
    name: '',
    entries: [],
  })
}

function removeConfigSet(index: number) {
  form.default_config_sets.splice(index, 1)
}

function addConfigEntry(setIndex: number) {
  form.default_config_sets[setIndex].entries.push({
    key: '',
    value: '',
  })
}

function removeConfigEntry(setIndex: number, entryIndex: number) {
  form.default_config_sets[setIndex].entries.splice(entryIndex, 1)
}

const rules = {
  name: [{ required: true, message: '请输入预置名称', trigger: 'blur' }],
}

onMounted(loadPreset)
</script>

<template>
  <PageHeader :title="isEdit ? '编辑预置' : '创建预置'" />

  <el-card v-loading="loading">
    <el-form :model="form" :rules="rules" label-width="140px">
      <el-form-item label="预置名称" prop="name">
        <el-input v-model="form.name" placeholder="例如：dev-standard, test-basic" style="max-width: 400px" />
      </el-form-item>

      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="预置的简要描述（可选）"
          style="max-width: 600px"
        />
      </el-form-item>

      <el-divider content-position="left">Profile 模板</el-divider>
      <el-form-item label="Profile Template">
        <el-input
          v-model="profileTemplateJson"
          type="textarea"
          :rows="8"
          placeholder='{"naming": {"deployment": "{base}-app"}}'
          style="max-width: 600px; font-family: monospace"
        />
        <div class="el-form-item__description">JSON 格式的环境档案模板</div>
      </el-form-item>

      <el-divider content-position="left">默认配置集</el-divider>
      <el-form-item label=" ">
        <el-button :icon="Plus" @click="addConfigSet">添加配置集</el-button>
      </el-form-item>

      <el-card
        v-for="(configSet, setIndex) in form.default_config_sets"
        :key="setIndex"
        shadow="never"
        style="margin-bottom: 16px; max-width: 800px"
      >
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>配置集 #{{ setIndex + 1 }}</span>
            <el-button size="small" type="danger" :icon="Delete" @click="removeConfigSet(setIndex)">
              删除
            </el-button>
          </div>
        </template>

        <el-form-item label="类型">
          <el-select v-model="configSet.kind" style="width: 200px">
            <el-option label="环境变量 (env)" value="env" />
            <el-option label="文件 (file)" value="file" />
          </el-select>
        </el-form-item>

        <el-form-item label="名称">
          <el-input v-model="configSet.name" placeholder="例如：common-env" style="width: 300px" />
        </el-form-item>

        <el-form-item label="配置项">
          <div style="width: 100%">
            <el-button size="small" :icon="Plus" @click="addConfigEntry(setIndex)">添加配置项</el-button>

            <div v-for="(entry, entryIndex) in configSet.entries" :key="entryIndex" style="margin-top: 8px">
              <el-space>
                <el-input v-model="entry.key" placeholder="KEY" style="width: 200px" />
                <el-input
                  v-if="configSet.kind === 'env'"
                  v-model="entry.value"
                  placeholder="VALUE"
                  style="width: 300px"
                />
                <el-button size="small" type="danger" :icon="Delete" @click="removeConfigEntry(setIndex, entryIndex)">
                  删除
                </el-button>
              </el-space>
            </div>
          </div>
        </el-form-item>
      </el-card>

      <el-form-item style="margin-top: 24px">
        <el-button type="primary" @click="handleSave" :loading="saveAction.busy.value">
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
