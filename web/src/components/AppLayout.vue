<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { platformApi } from '@/api'
import { allowed } from '@/router'
import { useSession } from '@/stores/session'
import { usePlatform } from '@/stores/platform'
import { notifyError } from '@/composables/useAction'
import { ElMessage } from 'element-plus'

const session = useSession()
const platform = usePlatform()
const router = useRouter()
const route = useRoute()

/** 顶部导航由路由表生成，与路由守卫使用同一份权限规则。 */
const navItems = computed(() => {
  if (session.mustChangePassword) return []
  const layout = router.getRoutes().find((r) => r.path === '/')
  return (layout?.children ?? [])
    .filter((r) => r.meta?.nav && allowed({ meta: r.meta }))
    .map((r) => ({ path: '/' + r.path, title: r.meta?.title ?? '' }))
})

const activeNav = computed(() => {
  const match = navItems.value.find((n) => route.path === n.path || route.path.startsWith(n.path + '/'))
  return match?.path ?? route.path
})

const registryText = computed(() => {
  const r = platform.info?.registry
  if (!r) return 'SWR：加载中'
  return r.address ? `SWR：${r.address} / ${r.organization || '未配置组织'}` : 'SWR：未配置'
})
const dockerText = computed(() => {
  const d = platform.info?.docker
  if (!d) return 'Docker：检测中'
  return d.available ? `Docker ${d.version} · ${d.arch}` : 'Docker 不可用'
})

async function testRegistry() {
  try {
    const r = await platformApi.testRegistry()
    ElMessage({ type: r.ok ? 'success' : 'error', message: r.ok ? 'SWR 连接成功' : r.message, duration: 6000 })
  } catch (e) {
    notifyError(e)
  }
}

async function logout() {
  try {
    await session.logout()
    await router.replace({ name: 'login' })
  } catch (e) {
    notifyError(e)
  }
}

function onCommand(command: string) {
  if (command === 'logout') void logout()
  else void router.push({ name: command })
}

onMounted(() => {
  if (!session.mustChangePassword) platform.loadInfo().catch(notifyError)
})
</script>

<template>
  <el-container direction="vertical" class="layout">
    <header class="topbar">
      <div class="brand">
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path fill="currentColor" d="M12 2 3 6.5v11L12 22l9-4.5v-11L12 2Zm0 2.2 6.7 3.3L12 10.8 5.3 7.5 12 4.2ZM5 9.1l6 3v7.6l-6-3V9.1Zm8 10.6v-7.6l6-3v7.6l-6 3Z" />
        </svg>
        <span>{{ session.siteName }}</span>
      </div>
      <el-menu mode="horizontal" :default-active="activeNav" router :ellipsis="true" class="nav">
        <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path">{{ item.title }}</el-menu-item>
      </el-menu>
      <div class="status">
        <el-tooltip v-if="platform.info" :content="platform.info.docker.error || dockerText" placement="bottom">
          <el-tag :type="platform.info.docker.available ? 'success' : 'danger'" effect="plain">{{ dockerText }}</el-tag>
        </el-tooltip>
        <el-tag
          v-if="session.can('settings.manage')"
          :type="platform.info?.registry.configured ? 'info' : 'danger'"
          effect="plain"
          class="clickable"
          title="点击测试 SWR 连接"
          @click="testRegistry"
        >{{ registryText }}</el-tag>
        <el-dropdown trigger="click" @command="onCommand">
          <el-button text>{{ session.profile?.display_name }} · {{ session.profile?.role_label }}</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="account">我的账号</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>
    <main>
      <RouterView />
    </main>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 20px;
  background: var(--app-surface);
  border-bottom: 1px solid var(--el-border-color-light);
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  white-space: nowrap;
  color: var(--el-color-primary);
}
.nav {
  flex: 1;
  min-width: 0;
  border-bottom: none;
}
.status {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.clickable {
  cursor: pointer;
}
</style>
