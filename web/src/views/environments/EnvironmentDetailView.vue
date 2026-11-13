<script setup lang="ts">
/** 环境详情：概览（生效配置）、基础配置、环境档案、环境对比、发布策略、成员。 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { environmentApi, ingressProfileApi, presetApi } from '@/api'
import type { EnvironmentDetail, EnvironmentProfile, EnvironmentSummary, IngressProfile, Preset } from '@/api/types'
import MembersPanel from '@/components/access/MembersPanel.vue'
import ConfigLayerPanel from '@/components/config/ConfigLayerPanel.vue'
import EffectiveConfigTable from '@/components/config/EffectiveConfigTable.vue'
import EnvironmentCompare from '@/components/config/EnvironmentCompare.vue'
import EnvironmentProfileForm from '@/components/config/EnvironmentProfileForm.vue'
import PageHeader from '@/components/PageHeader.vue'
import { notifyError, useAction } from '@/composables/useAction'
import { TIER_TAG, TIER_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const props = defineProps<{ id: number }>()
const route = useRoute()
const router = useRouter()
const session = useSession()
const env = ref<EnvironmentDetail | null>(null)
const environments = ref<EnvironmentSummary[]>([])
const ingressProfiles = ref<IngressProfile[]>([])
const profileDraft = ref<EnvironmentProfile | null>(null)
const ingressDraft = ref<number | null>(null)
const loading = ref(false)
const effective = ref<InstanceType<typeof EffectiveConfigTable> | null>(null)
const { busy, run } = useAction()

// 应用预置对话框
const applyPresetDialog = ref(false)
const presets = ref<Preset[]>([])
const selectedPresetId = ref<number | null>(null)

const TABS = ['overview', 'config', 'profile', 'compare', 'policy', 'members'] as const
type Tab = (typeof TABS)[number]
const showMembers = computed(() => session.canNamespace(props.id, 'namespace.member.manage') || session.canGlobal('user.manage'))
const tab = computed<Tab>({
  get: () => {
    const value = String(route.query.tab ?? 'overview') as Tab
    return TABS.includes(value) && (value !== 'members' || showMembers.value) ? value : 'overview'
  },
  set: (value) => { void router.replace({ query: { ...route.query, tab: value } }) },
})

function approvalText(e: EnvironmentDetail): string {
  if (e.policy.require_admin_approval || e.tier === 'prod') return '平台管理员确认'
  if (e.policy.require_approval || e.tier === 'staging') return `需 ${e.policy.min_approvals ?? 1} 人审批`
  return '无需审批'
}

function resetDraft(detail: EnvironmentDetail) {
  profileDraft.value = JSON.parse(JSON.stringify(detail.profile)) as EnvironmentProfile
  ingressDraft.value = detail.ingress_profile_id
}

async function load() {
  loading.value = true
  try {
    const detail = await environmentApi.get(props.id)
    env.value = detail
    resetDraft(detail)
    const canListIngress = session.canGlobal('certificate.view') || session.can('environment.config')
    const [list, profiles] = await Promise.all([
      environmentApi.list().catch(() => ({ items: [] as EnvironmentSummary[], total: 0 })),
      canListIngress ? ingressProfileApi.list().catch(() => ({ items: [] as IngressProfile[], total: 0 }))
        : Promise.resolve({ items: [] as IngressProfile[], total: 0 }),
    ])
    environments.value = list.items
    ingressProfiles.value = profiles.items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

async function saveProfile() {
  const current = env.value
  const draft = profileDraft.value
  if (!current || !draft) return
  await run(async () => {
    const saved = await environmentApi.saveProfile(current.id, {
      base_version: current.profile_version, profile: draft, ingress_profile_id: ingressDraft.value,
    })
    env.value = saved
    resetDraft(saved)
  }, '环境档案已保存')
}

function refreshEffective() {
  effective.value?.reload()
}

async function showApplyPresetDialog() {
  try {
    const result = await presetApi.list()
    presets.value = result.items
    selectedPresetId.value = null
    applyPresetDialog.value = true
  } catch (e) {
    notifyError(e)
  }
}

async function applyPreset() {
  if (!selectedPresetId.value || !env.value) return
  await run(async () => {
    await environmentApi.applyPreset(env.value!.id, {
      preset_id: selectedPresetId.value!,
    })
    applyPresetDialog.value = false
    await load()
    refreshEffective()
  }, '预置已应用')
}

watch(() => props.id, load)
onMounted(load)
</script>

<template>
  <div v-loading="loading" class="page">
    <PageHeader :title="env ? `环境 ${env.name}` : '环境'" description="环境 = 已纳管的命名空间 + 环境档案 + 环境层配置。">
      <template v-if="env" #meta>
        <div class="meta">
          <el-tag size="small" :type="TIER_TAG[env.tier]" effect="plain">{{ TIER_TEXT[env.tier] }}</el-tag>
          <span class="hint">{{ env.cluster_name }} / {{ env.name }}</span>
          <span class="hint">服务 {{ env.service_count }} 个</span>
        </div>
      </template>
      <el-button v-if="env && session.canNamespace(env.id, 'environment.config')" @click="showApplyPresetDialog">应用预置</el-button>
      <el-button v-if="env && session.canNamespace(env.id, 'service.create')" type="primary" plain
        @click="router.push({ name: 'environment-import', params: { id: env.id } })">导入已有服务</el-button>
      <RouterLink :to="{ name: 'services', query: { namespace: String(id) } }"><el-button>查看服务</el-button></RouterLink>
      <RouterLink :to="{ name: 'environments' }"><el-button>返回列表</el-button></RouterLink>
    </PageHeader>

    <el-card v-if="env">
      <el-tabs v-model="tab">
        <el-tab-pane label="概览" name="overview">
          <el-descriptions :column="3" border size="small" class="section-gap">
            <el-descriptions-item label="集群">{{ env.cluster_name }}</el-descriptions-item>
            <el-descriptions-item label="命名空间">{{ env.name }}</el-descriptions-item>
            <el-descriptions-item label="级别">{{ TIER_TEXT[env.tier] }}</el-descriptions-item>
            <el-descriptions-item label="所属项目">{{ env.project_ids.map((id) => session.projectName(id)).join('、') || '未分配' }}</el-descriptions-item>
            <el-descriptions-item label="环境档案">{{ env.profile_saved ? `v${env.profile_version}` : '未设置（使用默认值）' }}</el-descriptions-item>
            <el-descriptions-item label="负载均衡入口">{{ env.ingress_profile?.name || '未设置' }}</el-descriptions-item>
            <el-descriptions-item label="发布审批">{{ approvalText(env) }}</el-descriptions-item>
            <el-descriptions-item label="基础配置">{{ env.config_sets.length }} 个</el-descriptions-item>
            <el-descriptions-item label="最近修改">{{ fmtTime(env.config_sets.map((s) => s.updated_at).sort().pop()) }}</el-descriptions-item>
          </el-descriptions>
          <h3>生效的配置</h3>
          <EffectiveConfigTable ref="effective" :namespace-id="env.id" :project-ids="env.project_ids" />
        </el-tab-pane>

        <el-tab-pane label="基础配置" name="config" lazy>
          <ConfigLayerPanel scope-type="environment" :scope-id="env.id" @changed="refreshEffective" />
        </el-tab-pane>

        <el-tab-pane label="环境档案" name="profile" lazy>
          <el-alert v-if="!env.profile_saved" type="info" :closable="false" show-icon class="section-gap"
            title="尚未保存环境档案，下面是平台默认值。保存后新建服务会使用这些设置；也可以通过“导入已有服务”从现有工作负载推断。" />
          <EnvironmentProfileForm v-if="profileDraft" v-model="profileDraft" v-model:ingress-profile-id="ingressDraft"
            :namespace-name="env.name" :ingress-profiles="ingressProfiles" :readonly="!env.can_edit" />
          <div v-if="env.can_edit" class="toolbar profile-actions">
            <el-button @click="resetDraft(env)">撤销修改</el-button>
            <el-button type="primary" :loading="busy" @click="saveProfile">保存为 v{{ env.profile_version + 1 }}</el-button>
          </div>
        </el-tab-pane>

        <el-tab-pane label="环境对比" name="compare" lazy>
          <EnvironmentCompare :namespace-id="env.id" :namespace-name="env.name" :environments="environments"
            :project-ids="env.project_ids" />
        </el-tab-pane>

        <el-tab-pane label="发布策略" name="policy" lazy>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="审批">{{ approvalText(env) }}</el-descriptions-item>
            <el-descriptions-item label="允许的镜像组织">{{ env.policy.allowed_registry_orgs?.join('、') || '未限定（发布会被拒绝）' }}</el-descriptions-item>
            <el-descriptions-item label="最大副本数">{{ env.policy.max_replicas }}</el-descriptions-item>
            <el-descriptions-item label="发布超时">{{ env.policy.rollout_timeout }} 秒</el-descriptions-item>
            <el-descriptions-item label="发布窗口">{{ env.policy.deploy_windows?.length ? `${env.policy.deploy_windows.length} 个` : '不限' }}</el-descriptions-item>
            <el-descriptions-item label="冻结">{{ env.policy.freeze?.enabled ? `已冻结：${env.policy.freeze.reason || '无说明'}` : '未冻结' }}</el-descriptions-item>
          </el-descriptions>
          <p class="hint">发布策略由平台管理员在
            <RouterLink :to="{ name: 'kubernetes', query: { tab: 'namespaces' } }">集群与命名空间</RouterLink> 中修改。</p>
        </el-tab-pane>

        <el-tab-pane v-if="showMembers" label="成员" name="members" lazy>
          <MembersPanel scope-type="namespace" :scope-id="env.id" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 应用预置对话框 -->
    <el-dialog v-model="applyPresetDialog" title="应用预置" width="600px">
      <el-form label-width="100px">
        <el-form-item label="选择预置" required>
          <el-select v-model="selectedPresetId" placeholder="选择预置模板" style="width: 100%">
            <el-option
              v-for="preset in presets"
              :key="preset.id"
              :label="preset.name"
              :value="preset.id"
            >
              <div>
                <div>{{ preset.name }}</div>
                <div style="font-size: 12px; color: var(--el-text-color-secondary)">{{ preset.description }}</div>
              </div>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="applyPresetDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!selectedPresetId" :loading="busy" @click="applyPreset">应用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.meta { display: flex; gap: 10px; align-items: center; margin-top: 6px; flex-wrap: wrap; }
.profile-actions { justify-content: flex-end; margin-top: 12px; }
h3 { margin: 18px 0 8px; font-size: 15px; }
</style>
