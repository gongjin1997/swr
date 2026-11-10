<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { clusterApi, legacyDeployApi } from '@/api'
import type { Cluster, DeployProvider } from '@/api/types'
import { notifyError } from '@/composables/useAction'
const providers = ref<DeployProvider[]>([])
const clusters = ref<Cluster[]>([])
onMounted(async () => {
  try {
    const [p, c] = await Promise.all([legacyDeployApi.providers(), clusterApi.list()])
    providers.value = p.filter(item => item.type === 'kubernetes')
    clusters.value = c.items
  } catch (error) { notifyError(error) }
})
</script>
<template>
  <el-card>
    <template #header><span class="card-title">发布适配器</span></template>
    <el-descriptions v-for="provider in providers" :key="provider.type" :column="1" border>
      <el-descriptions-item label="适配器">{{ provider.label }}</el-descriptions-item>
      <el-descriptions-item label="连接方式">Kubernetes API · kubeconfig / Token / 客户端证书</el-descriptions-item>
      <el-descriptions-item label="关联集群">{{ clusters.length }} 个集群，{{ clusters.filter(c => c.status === 'ready').length }} 个已连接</el-descriptions-item>
      <el-descriptions-item label="发布入口"><RouterLink to="/deploy">部署中心</RouterLink></el-descriptions-item>
    </el-descriptions>
    <RouterLink :to="{ name: 'settings', query: { section: 'kubernetes' } }"><el-button type="primary" class="section-gap">管理集群连接</el-button></RouterLink>
  </el-card>
</template>
