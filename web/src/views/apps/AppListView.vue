<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { appApi } from '@/api'
import type { App } from '@/api/types'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { MODE_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'
import AppFormDialog from './AppFormDialog.vue'

const platform = usePlatform()
const session = useSession()
const router = useRouter()
const editing = ref<App | null>(null)
const dialog = ref(false)
const loading = ref(false)
const { run } = useAction()

async function load() {
  loading.value = true
  try {
    await platform.loadApps(true)
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(app: App | null) {
  editing.value = app
  dialog.value = true
}

async function remove(app: App) {
  if (!(await confirmAction(`确定删除应用“${app.name}”？不会删除 SWR 中的镜像和构建记录。`, '删除应用', true))) return
  await run(async () => {
    await appApi.remove(app.id)
    await load()
  }, '已删除')
}

function destination(app: App): string {
  return app.target_name ? `${app.target_dir.replace(/\/$/, '')}/${app.target_name}` : `${app.target_dir}/`
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-heading">
      <h2>应用列表</h2>
      <el-button v-if="session.can('app.manage')" type="primary" @click="open(null)">新建应用</el-button>
    </div>
    <el-card>
      <el-table v-loading="loading" :data="platform.apps" empty-text="还没有应用，点击右上角“新建应用”，可以直接粘贴现有 Dockerfile 导入">
        <el-table-column label="应用" min-width="200">
          <template #default="{ row }">
            <b>{{ row.name }}</b>
            <div v-if="row.description" class="hint">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="镜像仓库" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.image_repo }}</span></template></el-table-column>
        <el-table-column label="默认方式" width="110"><template #default="{ row }">{{ MODE_TEXT[row.default_mode] }}</template></el-table-column>
        <el-table-column label="镜像内路径" min-width="200"><template #default="{ row }"><span class="mono">{{ destination(row) }}</span></template></el-table-column>
        <el-table-column label="更新时间" width="170"><template #default="{ row }">{{ fmtTime(row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="190">
          <template #default="{ row }">
            <el-button v-if="session.can('app.manage')" link type="primary" @click="open(row)">编辑</el-button>
            <el-button v-if="session.can('build.run')" link type="primary" @click="router.push({ name: 'build', query: { app: row.id } })">去构建</el-button>
            <el-button v-if="session.can('app.manage')" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    <AppFormDialog v-model="dialog" :app="editing" @saved="load" />
  </div>
</template>
