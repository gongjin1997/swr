<script setup lang="ts">
import { ref } from 'vue'
import ClustersView from '@/views/resources/ClustersView.vue'
import NamespacesView from '@/views/resources/NamespacesView.vue'

const active = ref<'clusters' | 'namespaces'>('clusters')
</script>

<template>
  <div class="kubernetes-settings">
    <el-radio-group v-model="active" aria-label="Kubernetes 设置">
      <el-radio-button value="clusters">集群连接</el-radio-button>
      <el-radio-button value="namespaces">命名空间与审批</el-radio-button>
    </el-radio-group>
    <ClustersView v-if="active === 'clusters'" />
    <NamespacesView v-else />
  </div>
</template>

<style scoped>
.kubernetes-settings { display: grid; gap: 14px; min-width: 0; }
</style>
