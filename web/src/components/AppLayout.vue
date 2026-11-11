<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDown, Box, Connection, Document, Expand, Files, Fold, Grid, Key, Lock, Menu, Monitor, Moon, Odometer, Promotion,
  Setting, Share, Sunny, UserFilled,
} from '@element-plus/icons-vue'
import { allowed } from '@/router'
import { useSession } from '@/stores/session'
import { usePlatform } from '@/stores/platform'
import { usePreferences, type Theme } from '@/stores/preferences'
import { notifyError } from '@/composables/useAction'
import BrandMark from './BrandMark.vue'

const session = useSession()
const platform = usePlatform()
const preferences = usePreferences()
const router = useRouter()
const route = useRoute()
const mobileOpen = ref(false)

/** 导航按"交付流程 → 资源 → 平台"组织；子页面（如服务详情）高亮所属的入口。 */
const groups = [
  { title: '概览', items: [{ name: 'dashboard', icon: Odometer }] },
  { title: '交付', items: [{ name: 'build', icon: Box }, { name: 'jobs', icon: Document }, { name: 'releases', icon: Promotion }] },
  { title: '资源', items: [{ name: 'projects', icon: Files }, { name: 'apps', icon: Grid }, { name: 'services', icon: Share }] },
  { title: '平台', items: [
    { name: 'kubernetes', icon: Connection }, { name: 'access', icon: Lock }, { name: 'audit', icon: Monitor },
    { name: 'settings', icon: Setting },
  ] },
]
const OWNER: Record<string, string> = {
  'project-detail': 'projects', 'service-detail': 'services', users: 'access', roles: 'access', bindings: 'access',
}

const menu = computed(() => session.mustChangePassword ? [] : groups.map((group) => ({
  title: group.title,
  items: group.items.flatMap((item) => {
    const record = router.getRoutes().find((r) => r.name === item.name)
    if (!record || !allowed(record)) return []
    return [{ ...item, title: record.meta.title ?? '', path: router.resolve({ name: item.name }).fullPath }]
  }),
})).filter((group) => group.items.length))

const activeName = computed(() => {
  const name = String(route.name ?? '')
  return OWNER[name] ?? name
})
const activeGroup = computed(() => menu.value.find((g) => g.items.some((i) => i.name === activeName.value))?.title ?? '个人中心')
const crumbs = computed(() => route.matched.slice(1).map((r) => r.meta.title).filter((t): t is string => !!t))
const displayName = computed(() => session.profile?.display_name || session.profile?.username || '用户')
const avatar = computed(() => displayName.value.slice(0, 1).toUpperCase())
const themes: { value: Theme; label: string; icon: typeof Sunny }[] = [
  { value: 'light', label: '浅色外观', icon: Sunny }, { value: 'dark', label: '深色外观', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
]

watch(() => route.fullPath, () => { mobileOpen.value = false })

function setTheme(value: string | number | object) {
  preferences.theme = value as Theme
}

async function command(value: string | number | object) {
  try {
    if (value === 'logout') {
      await session.logout()
      platform.reset()
      await router.replace({ name: 'login' })
    } else {
      await router.push({ name: String(value) })
    }
  } catch (error) {
    notifyError(error)
  }
}

onMounted(() => {
  if (!session.mustChangePassword) platform.loadInfo().catch(notifyError)
})
</script>

<template>
  <div class="app-layout" :class="{ collapsed: preferences.collapsed }">
    <a class="skip-link" href="#main-content">跳至主要内容</a>
    <aside class="sider" aria-label="主导航">
      <RouterLink to="/" class="brand" :title="session.siteName" :aria-label="session.siteName">
        <BrandMark />
        <span v-show="!preferences.collapsed" class="site-name">{{ session.siteName }}</span>
      </RouterLink>
      <nav class="side-menu">
        <section v-for="group in menu" :key="group.title" class="menu-group">
          <div v-if="!preferences.collapsed" class="group-title">{{ group.title }}</div>
          <el-tooltip v-for="item in group.items" :key="item.name" :content="item.title" placement="right" :disabled="!preferences.collapsed">
            <RouterLink :to="item.path" class="menu-item" :class="{ active: activeName === item.name }"
              :aria-current="activeName === item.name ? 'page' : undefined" :aria-label="item.title">
              <el-icon :size="18"><component :is="item.icon" /></el-icon><span v-show="!preferences.collapsed">{{ item.title }}</span>
            </RouterLink>
          </el-tooltip>
        </section>
      </nav>
      <div class="sider-bottom">
        <el-button text :icon="preferences.collapsed ? Expand : Fold" :aria-label="preferences.collapsed ? '展开侧栏' : '收起侧栏'"
          @click="preferences.collapsed = !preferences.collapsed"><span v-if="!preferences.collapsed">收起导航</span></el-button>
      </div>
    </aside>

    <div class="main-wrap">
      <header class="global-header">
        <div class="header-left">
          <el-button class="mobile-trigger" text circle :icon="Menu" aria-label="打开导航菜单" @click="mobileOpen = true" />
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>{{ activeGroup }}</el-breadcrumb-item>
            <el-breadcrumb-item v-for="title in crumbs" :key="title">{{ title }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-tooltip v-if="platform.info" :content="platform.info.docker.error || `Docker ${platform.info.docker.version} · ${platform.info.docker.arch}`">
            <span class="engine-state" :class="{ ready: platform.info.docker.available }"><i />{{ platform.info.docker.available ? '构建引擎就绪' : '构建引擎不可用' }}</span>
          </el-tooltip>
          <el-dropdown trigger="click" @command="setTheme">
            <el-button text circle :icon="preferences.theme === 'dark' ? Moon : Sunny" aria-label="切换外观" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="theme in themes" :key="theme.value" :command="theme.value" :icon="theme.icon"
                  :disabled="preferences.theme === theme.value">{{ theme.label }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <span class="header-divider" />
          <el-dropdown trigger="click" @command="command">
            <button class="user-menu" type="button" aria-label="账号菜单">
              <span class="avatar">{{ avatar }}</span>
              <span class="user-info"><b>{{ displayName }}</b><small>{{ session.profile?.role_label }}</small></span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="account" :icon="UserFilled">我的账号</el-dropdown-item>
                <el-dropdown-item command="change-password" :icon="Key">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>
      <main id="main-content" tabindex="-1"><RouterView /></main>
    </div>
    <el-drawer v-model="mobileOpen" title="导航" direction="ltr" size="280px" class="mobile-menu">
      <nav aria-label="移动端导航">
        <section v-for="group in menu" :key="group.title" class="menu-group">
          <div class="group-title">{{ group.title }}</div>
          <RouterLink v-for="item in group.items" :key="item.name" :to="item.path" class="menu-item"
            :class="{ active: activeName === item.name }" :aria-current="activeName === item.name ? 'page' : undefined">
            <el-icon :size="18"><component :is="item.icon" /></el-icon>{{ item.title }}
          </RouterLink>
        </section>
      </nav>
    </el-drawer>
  </div>
</template>

<style scoped>
.app-layout { --sidebar-width: var(--hi-sider-width); min-height: 100vh; }
.app-layout.collapsed { --sidebar-width: var(--hi-sider-collapsed); }
.sider { position: fixed; inset: 0 auto 0 0; width: var(--sidebar-width); background: var(--hi-sider-bg); border-right: 1px solid var(--hi-border); display: flex; flex-direction: column; z-index: 40; transition: width .2s; }
.brand { height: var(--hi-header-height); flex: none; padding: 0 17px; display: flex; align-items: center; gap: 10px; color: var(--el-color-primary); overflow: hidden; }
.brand svg { width: 30px; height: 30px; flex: none; }
.site-name { font-size: 14px; font-weight: 600; color: var(--hi-text-1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.side-menu { padding: 6px 10px 12px; overflow-y: auto; flex: 1; }
.menu-group { margin: 0 0 6px; }
.group-title { padding: 12px 10px 6px; font-size: 12px; color: var(--hi-text-3); }
.menu-item { display: flex; align-items: center; gap: 10px; height: 38px; padding: 0 12px; margin: 2px 0; color: var(--hi-sider-text); border-radius: 8px; white-space: nowrap; font-size: 14px; }
.menu-item:hover { background: var(--hi-bg-soft); }
.menu-item.active { color: var(--el-color-primary); background: var(--hi-sider-active-bg); font-weight: 600; }
.collapsed .menu-item { padding: 0; justify-content: center; }
.collapsed .menu-group + .menu-group { border-top: 1px solid var(--hi-border-light); padding-top: 10px; }
.sider-bottom { border-top: 1px solid var(--hi-border-light); padding: 12px; }
.sider-bottom .el-button { color: var(--hi-text-2); font-size: 12px; }
.main-wrap { margin-left: var(--sidebar-width); min-height: 100vh; display: flex; flex-direction: column; transition: margin-left .2s; }
.global-header { height: var(--hi-header-height); padding: 0 20px; display: flex; justify-content: space-between; align-items: center; gap: 16px; background: var(--hi-bg-card); border-bottom: 1px solid var(--hi-border); position: sticky; top: 0; z-index: 25; }
.header-left, .header-right { display: flex; align-items: center; gap: 14px; min-width: 0; }
.header-left :deep(.el-breadcrumb) { font-size: 14px; line-height: 20px; }
.header-right { flex: none; }
.header-divider { height: 24px; width: 1px; background: var(--hi-border); }
.engine-state { display: flex; gap: 7px; align-items: center; font-size: 12px; color: var(--el-color-warning); }
.engine-state i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.engine-state.ready { color: var(--el-color-success); }
.user-menu { display: flex; align-items: center; gap: 9px; cursor: pointer; background: none; border: 0; padding: 4px; color: var(--hi-text-2); font: inherit; }
.avatar { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 50%; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-weight: 600; }
.user-info { display: grid; gap: 3px; text-align: left; max-width: 160px; }
.user-info b, .user-info small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-info b { font-size: 12px; }
.user-info small { color: var(--hi-text-3); font-size: 11px; }
main { flex: 1; min-width: 0; outline: none; }
.mobile-trigger { display: none; }
.skip-link { position: fixed; left: 240px; top: -60px; padding: 10px; background: var(--hi-bg-card); z-index: 100; }
.skip-link:focus { top: 8px; }
@media (max-width: 767px) {
  .sider { display: none; }
  .main-wrap { margin-left: 0; }
  .global-header { padding: 0 12px; gap: 8px; height: 60px; }
  .header-left, .header-right { gap: 6px; }
  .mobile-trigger { display: inline-flex; }
  .engine-state, .user-info, .header-divider { display: none; }
  .user-menu { gap: 3px; }
}
</style>
