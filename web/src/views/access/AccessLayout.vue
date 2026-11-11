<script setup lang="ts">
/** 访问控制：账号、角色、授权三个标签页共用的页面框架。 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import { allowed } from '@/router'

const router = useRouter()
const route = useRoute()
const tabs = computed(() => ['users', 'roles', 'bindings']
  .map((name) => router.getRoutes().find((r) => r.name === name))
  .filter((r) => !!r && allowed(r))
  .map((r) => ({ name: String(r!.name), title: r!.meta.title ?? '' })))
const active = computed({
  get: () => String(route.name ?? ''),
  set: (name: string) => void router.push({ name }),
})
</script>

<template>
  <div class="page">
    <PageHeader title="访问控制" description="账号只通过授权获得权限：在全局、项目或命名空间范围授予角色，权限按范围生效。" />
    <el-tabs v-model="active" class="access-tabs">
      <el-tab-pane v-for="tab in tabs" :key="tab.name" :label="tab.title" :name="tab.name" />
    </el-tabs>
    <RouterView />
  </div>
</template>

<style scoped>
.access-tabs { margin-bottom: 4px; }
.access-tabs :deep(.el-tabs__header) { margin-bottom: 14px; }
</style>
