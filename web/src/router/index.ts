import { createRouter, createWebHistory, type RouteLocationNormalized, type RouteRecordRaw } from 'vue-router'
import { onAuthFailure } from '@/api/http'
import { useSession } from '@/stores/session'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    /** 无需登录 */
    public?: boolean
    /** 满足任一权限即可访问（在任一项目/命名空间拥有即可，具体资源由页面与后端再判断） */
    perms?: string[]
    /** 需要启用部署模块 */
    deploy?: boolean
  }
}

const accessChildren: RouteRecordRaw[] = [
  { path: 'users', name: 'users', component: () => import('@/views/access/UsersView.vue'),
    meta: { title: '账号', perms: ['user.manage'] } },
  { path: 'roles', name: 'roles', component: () => import('@/views/access/RolesView.vue'),
    meta: { title: '角色', perms: ['role.manage', 'user.manage'] } },
  { path: 'bindings', name: 'bindings', component: () => import('@/views/access/BindingsView.vue'),
    meta: { title: '授权', perms: ['user.manage', 'project.member.manage', 'namespace.member.manage'] } },
]

/** 旧地址（书签、文档中的链接）跳转到新位置。 */
const legacyRedirects: RouteRecordRaw[] = [
  { path: 'deploy', redirect: (to) => ({ name: 'releases', query: to.query }) },
  { path: 'resources', redirect: { name: 'projects' } },
  { path: 'resources/projects', redirect: (to) => ({ name: 'projects', query: to.query }) },
  { path: 'resources/services', redirect: (to) => ({ name: 'services', query: to.query }) },
  { path: 'resources/services/:id(\\d+)', redirect: (to) => ({ name: 'service-detail', params: { id: to.params.id } }) },
  { path: 'resources/clusters', redirect: { name: 'kubernetes' } },
  { path: 'resources/namespaces', redirect: { name: 'kubernetes', query: { tab: 'namespaces' } } },
  { path: 'resources/bindings', redirect: { name: 'bindings' } },
  { path: 'admin/users', redirect: { name: 'users' } },
  { path: 'admin/roles', redirect: { name: 'roles' } },
  { path: 'admin/audit', redirect: { name: 'audit' } },
  { path: 'settings', redirect: (to) => ({ name: 'settings', query: to.query }) },
]

export const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true, title: '登录' } },
  {
    path: '/',
    component: () => import('@/components/AppLayout.vue'),
    children: [
      { path: '', name: 'home', redirect: { name: 'dashboard' } },
      { path: 'dashboard', name: 'dashboard', component: () => import('@/views/dashboard/DashboardView.vue'),
        meta: { title: '工作台' } },
      // 交付
      { path: 'build', name: 'build', component: () => import('@/views/build/BuildView.vue'),
        meta: { title: '构建镜像', perms: ['build.run'] } },
      { path: 'jobs', name: 'jobs', component: () => import('@/views/jobs/JobListView.vue'),
        meta: { title: '构建记录', perms: ['job.view'] } },
      { path: 'releases', name: 'releases', component: () => import('@/views/releases/ReleasesView.vue'),
        meta: { title: '发布中心', perms: ['deploy.view', 'namespace.view', 'app.view'], deploy: true } },
      // 资源
      { path: 'projects', name: 'projects', component: () => import('@/views/projects/ProjectsView.vue'),
        meta: { title: '项目', perms: ['project.view'] } },
      { path: 'projects/:id(\\d+)', name: 'project-detail', component: () => import('@/views/projects/ProjectDetailView.vue'),
        meta: { title: '项目详情', perms: ['project.view'] }, props: (r) => ({ id: Number(r.params.id) }) },
      { path: 'apps', name: 'apps', component: () => import('@/views/apps/AppListView.vue'),
        meta: { title: '应用', perms: ['app.view'] } },
      { path: 'services', name: 'services', component: () => import('@/views/services/ServicesView.vue'),
        meta: { title: '服务', perms: ['namespace.view', 'app.view'] } },
      { path: 'services/:id(\\d+)', name: 'service-detail', component: () => import('@/views/services/ServiceDetailView.vue'),
        meta: { title: '服务详情', perms: ['namespace.view', 'app.view'] }, props: (r) => ({ id: Number(r.params.id) }) },
      // 平台
      { path: 'platform/kubernetes', name: 'kubernetes', component: () => import('@/views/platform/KubernetesView.vue'),
        meta: { title: '集群与命名空间', perms: ['cluster.manage', 'namespace.member.manage'] } },
      { path: 'platform/access', name: 'access', component: () => import('@/views/access/AccessLayout.vue'),
        meta: { title: '访问控制', perms: ['user.manage', 'role.manage', 'project.member.manage', 'namespace.member.manage'] },
        children: accessChildren },
      { path: 'platform/audit', name: 'audit', component: () => import('@/views/platform/AuditView.vue'),
        meta: { title: '操作审计', perms: ['audit.view'] } },
      { path: 'platform/settings', name: 'settings', component: () => import('@/views/settings/SettingsView.vue'),
        meta: { title: '系统设置', perms: ['settings.manage'] } },
      // 个人
      { path: 'account', name: 'account', component: () => import('@/views/account/AccountView.vue'),
        meta: { title: '我的账号' } },
      { path: 'account/password', name: 'change-password', component: () => import('@/views/account/ChangePasswordView.vue'),
        meta: { title: '修改密码' } },
      ...legacyRedirects,
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export function allowed(route: { meta?: RouteRecordRaw['meta'] }): boolean {
  const session = useSession()
  const meta = route.meta ?? {}
  if (meta.deploy && !session.deployEnabled) return false
  return !meta.perms || session.canAny(...meta.perms)
}

/** 登录后跳回的地址只接受站内路径，防止开放重定向。 */
export function safeNext(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/login')) return '/'
  return value
}

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (to, from) => (to.path === from.path ? false : { top: 0 }),
})

router.beforeEach(async (to: RouteLocationNormalized) => {
  const session = useSession()
  if (to.meta.public) {
    await session.loadSiteName()
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
  if (to.name === 'access') {
    const first = accessChildren.find((r) => allowed(r))
    return first ? { name: first.name as string } : { name: 'dashboard' }
  }
  if (to.matched.some((record) => !allowed(record))) return { name: 'dashboard' }
  return true
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
