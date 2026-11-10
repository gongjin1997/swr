<script setup lang="ts">
import type { NamespaceResources } from '@/api/types'
import { fmtTime } from '@/utils/format'
defineProps<{ data: NamespaceResources | null; loading: boolean; error: string }>()
defineEmits<{ refresh: [] }>()
const formatPorts = (ports: NamespaceResources['services'][number]['ports']) => ports.map(p => `${p.port} → ${p.targetPort}`).join('、')
</script>
<template>
  <el-card v-loading="loading">
    <template #header><div class="card-title"><span>Kubernetes 运行状态</span><el-button :loading="loading" @click="$emit('refresh')">刷新集群信息</el-button></div></template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="data">
      <p class="hint">{{ data.cluster_name }} / {{ data.namespace_name }} · 获取时间 {{ fmtTime(data.fetched_at) }}</p>
      <el-alert v-for="warning in data.warnings" :key="warning" :title="warning" type="warning" :closable="false" />
      <el-alert v-if="data.found === false" title="集群中未找到关联的 Deployment，请检查服务配置。" type="warning" :closable="false" />
      <el-table :data="data.deployments" empty-text="暂无 Deployment">
        <el-table-column prop="name" label="Deployment" min-width="160" />
        <el-table-column label="就绪副本" width="110"><template #default="{ row }">{{ row.ready_replicas }} / {{ row.replicas }}</template></el-table-column>
        <el-table-column label="容器 / 当前镜像" min-width="280"><template #default="{ row }"><p v-for="c in row.containers" :key="c.name" class="mono break-word">{{ c.name }} · {{ c.image }}</p></template></el-table-column>
      </el-table>
      <el-table :data="data.services" empty-text="未发现关联的 Kubernetes Service" class="section-gap">
        <el-table-column prop="name" label="Kubernetes Service" min-width="180" /><el-table-column prop="type" label="类型" width="140" />
        <el-table-column prop="cluster_ip" label="集群 IP" min-width="140" /><el-table-column label="端口" min-width="140"><template #default="{ row }">{{ formatPorts(row.ports) }}</template></el-table-column>
      </el-table>
      <el-table :data="data.pods" empty-text="未发现关联 Pod" class="section-gap"><el-table-column prop="name" label="Pod" min-width="220" /><el-table-column label="状态" width="140"><template #default="{ row }">{{ row.phase }} / {{ row.ready ? '就绪' : '未就绪' }}</template></el-table-column><el-table-column prop="node" label="节点" min-width="180" /></el-table>
      <p v-for="ingress in data.ingresses" :key="ingress.name" class="section-gap">Ingress {{ ingress.name }}：{{ ingress.hosts.join('、') }}</p>
    </template>
  </el-card>
</template>
