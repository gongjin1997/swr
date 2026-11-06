<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allowed } from '@/router'

const router = useRouter()
const route = useRoute()
const SECTIONS = ['projects', 'clusters', 'namespaces', 'services', 'bindings']

const tabs = computed(() =>
  SECTIONS.map((name) => router.getRoutes().find((r) => r.name === name))
    .filter((r) => r && allowed(r))
    .map((r) => ({ name: r!.name as string, title: r!.meta.title ?? '' })),
)
const active = computed(() => (route.name === 'service-detail' ? 'services' : String(route.name)))
</script>

<template>
  <div class="page">
    <div class="page-heading"><h2>资源管理</h2></div>
    <el-tabs :model-value="active" @tab-change="(name: string | number) => router.push({ name: String(name) })">
      <el-tab-pane v-for="tab in tabs" :key="tab.name" :name="tab.name" :label="tab.title" />
    </el-tabs>
    <RouterView />
  </div>
</template>
