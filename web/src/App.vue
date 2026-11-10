<script setup lang="ts">
import { watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePreferences } from '@/stores/preferences'
import { useSession } from '@/stores/session'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
usePreferences()
const route = useRoute()
const session = useSession()
watch(() => [route.meta.title, session.siteName], () => {
  document.title = route.meta.title ? `${route.meta.title} · ${session.siteName}` : session.siteName
}, { immediate: true })
</script>

<template>
  <el-config-provider :locale="zhCn">
    <RouterView />
  </el-config-provider>
</template>
