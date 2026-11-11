<script setup lang="ts">
/**
 * 集群与命名空间：集群连接（kubeconfig / Token / 客户端证书）与命名空间纳管、环境、发布策略、成员。
 * 集群标签页只对平台集群管理员显示；命名空间管理员只看到命名空间标签页，用于管理成员。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import { useSession } from '@/stores/session'
import ClustersView from './ClustersView.vue'
import NamespacesView from './NamespacesView.vue'

const route = useRoute()
const router = useRouter()
const session = useSession()
const showClusters = computed(() => session.canGlobal('cluster.manage'))
const tab = computed({
  get: () => (route.query.tab === 'namespaces' || !showClusters.value ? 'namespaces' : 'clusters'),
  set: (value: string) => void router.replace({ query: { tab: value === 'clusters' ? undefined : value } }),
})
</script>

<template>
  <div class="page">
    <PageHeader title="集群与命名空间" description="纳管已有的 Kubernetes 集群与命名空间，设置环境与发布策略，并分配给项目。平台不会创建集群资源。" />
    <el-tabs v-model="tab">
      <el-tab-pane v-if="showClusters" label="集群" name="clusters" lazy><ClustersView /></el-tab-pane>
      <el-tab-pane label="命名空间" name="namespaces" lazy><NamespacesView /></el-tab-pane>
    </el-tabs>
  </div>
</template>
