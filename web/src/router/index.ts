import { createRouter, createWebHistory, type RouteLocationNormalized, type RouteRecordRaw } from 'vue-router'
import { onAuthFailure } from '@/api/http'
import { useSession } from '@/stores/session'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    /** 无需登录 */
    public?: boolean
    /** 满足任一权限即可访问 */
    perms?: string[]
    /** 需要启用部署模块 */
    deploy?: boolean
    /** 在顶部导航中显示 */
    nav?: boolean
  }
}

const resourceChildren: RouteRecordRaw[] = [
  { path: 'projects', name: 'projects', component: () => import('@/views/resources/ProjectsView.vue'),
    meta: { title: '项目', perms: ['project.view'] } },
  { path: 'clusters', name: 'clusters', component: () => import('@/views/resources/ClustersView.vue'),
    meta: { title: '集群', perms: ['cluster.manage'] } },
  { path: 'namespaces', name: 'namespaces', component: () => import('@/views/resources/NamespacesView.vue'),
    meta: { title: '命名空间', perms: ['namespace.view'] } },
  { path: 'services', name: 'services', component: () => import('@/views/resources/ServicesView.vue'),
    meta: { title: '服务', perms: ['service.config', 'namespace.view'] } },
  { path: 'services/:id(\\d+)', name: 'service-detail', component: () => import('@/views/resources/ServiceDetailView.vue'),
    meta: { title: '服务详情', perms: ['service.config', 'namespace.view', 'app.view'] }, props: (r) => ({ id: Number(r.params.id) }) },
  { path: 'bindings', name: 'bindings', component: () => import('@/views/resources/BindingsView.vue'),
    meta: { title: '成员授权', perms: ['user.manage', 'project.member.manage', 'namespace.member.manage'] } },
]

export const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true, title: '登录' } },
  {
    path: '/',
    component: () => import('@/components/AppLayout.vue'),
    children: [
      // 首页按权限跳转（在全局守卫里处理，因为需要先加载用户权限）
      { path: '', name: 'home', component: { render: () => null } },
      { path: 'build', name: 'build', component: () => import('@/views/build/BuildView.vue'),
        meta: { title: '构建镜像', perms: ['build.run'], nav: true } },
      { path: 'jobs', name: 'jobs', component: () => import('@/views/jobs/JobListView.vue'),
        meta: { title: '构建记录', perms: ['job.view'], nav: true } },
      { path: 'apps', name: 'apps', component: () => import('@/views/apps/AppListView.vue'),
        meta: { title: '应用管理', perms: ['app.view'], nav: true } },
      { path: 'resources', name: 'resources', component: () => import('@/views/resources/ResourcesLayout.vue'),
        meta: { title: '资源管理', perms: ['project.view', 'namespace.view'], nav: true }, children: resourceChildren },
      { path: 'deploy', name: 'deploy', component: () => import('@/views/deploy/LegacyDeployView.vue'),
        meta: { title: '部署中心', perms: ['deploy.view'], deploy: true, nav: true } },
      { path: 'settings', name: 'settings', component: () => import('@/views/settings/SettingsView.vue'),
        meta: { title: '系统设置', perms: ['settings.manage'], nav: true } },
      { path: 'admin/users', name: 'users', component: () => import('@/views/admin/UsersView.vue'),
        meta: { title: '账号管理', perms: ['user.manage'], nav: true } },
      { path: 'admin/audit', name: 'audit', component: () => import('@/views/admin/AuditView.vue'),
        meta: { title: '操作审计', perms: ['audit.view'], nav: true } },
      { path: 'account', name: 'account', component: () => import('@/views/account/AccountView.vue'),
        meta: { title: '我的账号' } },
      { path: 'account/password', name: 'change-password', component: () => import('@/views/account/ChangePasswordView.vue'),
        meta: { title: '修改密码' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export function allowed(route: { meta: RouteRecordRaw['meta'] }): boolean {
  const session = useSession()
  const meta = route.meta ?? {}
  if (meta.deploy && !session.deployEnabled) return false
  return !meta.perms || session.canAny(...meta.perms)
}

function firstAllowed(records: RouteRecordRaw[]): string | undefined {
  return records.find((r) => r.name && !String(r.path).includes(':') && allowed({ meta: r.meta }))?.name as string | undefined
}

/** 登录后跳回的地址只接受站内路径，防止开放重定向。 */
export function safeNext(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/login')) return '/'
  return value
}

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to: RouteLocationNormalized) => {
  const session = useSession()
  if (to.meta.public) {
    if (to.name === 'login' && !session.profile) await session.loadSiteName()
    return true
  }
  if (!session.profile) {
    try {
      await session.load()
    } catch {
      return { name: 'login', query: { next: to.fullPath } }
    }
  }
  if (session.mustChangePassword && to.name !== 'change-password') return { name: 'change-password' }
  if (to.name === 'home') return { name: session.can('build.run') ? 'build' : 'jobs' }
  if (to.name === 'resources') return { name: firstAllowed(resourceChildren) ?? 'account' }
  const denied = to.matched.find((record) => !allowed(record))
  if (denied) {
    const fallback = router.getRoutes().find((r) => r.meta.nav && allowed(r))
    return fallback && fallback.name !== to.name ? { name: fallback.name } : { name: 'account' }
  }
  return true
})

router.afterEach((to) => {
  const session = useSession()
  document.title = to.meta.title ? `${to.meta.title} · ${session.siteName}` : session.siteName
})

onAuthFailure(
  () => {
    const current = router.currentRoute.value
    useSession().clear()
    if (current.name !== 'login') void router.replace({ name: 'login', query: { next: current.fullPath } })
  },
  () => {
    void useSession().load().finally(() => router.replace({ name: 'change-password' }))
  },
)

export default router
