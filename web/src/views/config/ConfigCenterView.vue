<script setup lang="ts">
/** 配置中心：维护平台层（所有环境继承）与项目层（项目下的环境继承）配置。环境层在环境详情中维护。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { projectApi } from '@/api'
import type { Project } from '@/api/types'
import ConfigLayerPanel from '@/components/config/ConfigLayerPanel.vue'
import PageHeader from '@/components/PageHeader.vue'
import { notifyError } from '@/composables/useAction'

const route = useRoute()
const router = useRouter()
const projects = ref<Project[]>([])
const projectId = ref<number | null>(null)
const tab = computed<'platform' | 'project'>({
  get: () => (route.query.tab === 'project' ? 'project' : 'platform'),
  set: (value) => { void router.replace({ query: { ...route.query, tab: value } }) },
})

onMounted(async () => {
  try {
    projects.value = (await projectApi.list()).items
    const fromQuery = Number(route.query.project)
    projectId.value = projects.value.find((p) => p.id === fromQuery)?.id ?? projects.value[0]?.id ?? null
  } catch (e) {
    notifyError(e)
  }
})

function selectProject(id: number | null) {
  projectId.value = id
  void router.replace({ query: { ...route.query, tab: 'project', project: id ?? undefined } })
}
</script>

<template>
  <div class="page">
    <PageHeader title="配置中心"
      description="配置分层继承：平台层 → 项目层 → 环境层 → 服务。上层可以锁定键，下层不能覆盖。环境层配置在“环境”详情中维护。" />
    <el-card>
      <el-tabs v-model="tab">
        <el-tab-pane label="平台层" name="platform">
          <p class="hint">所有环境都会继承平台层配置，例如附件中所有服务都相同的监控、安全代理与 JVM 参数，以及统一的证书目录。</p>
          <ConfigLayerPanel scope-type="platform" :scope-id="0" />
        </el-tab-pane>
        <el-tab-pane label="项目层" name="project" lazy>
          <div class="filter-bar">
            <el-select :model-value="projectId" filterable placeholder="选择项目" aria-label="选择项目" @update:model-value="selectProject">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <span class="hint">项目层用于同一产品在各环境都相同的参数，例如应用标识、项目标识。</span>
          </div>
          <ConfigLayerPanel v-if="projectId" scope-type="project" :scope-id="projectId" />
          <el-empty v-else description="没有可见的项目" />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>
