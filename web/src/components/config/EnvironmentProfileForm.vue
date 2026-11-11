<script setup lang="ts">
/** 环境档案：对象命名、访问入口、调度、拉取密钥、日志目录与资源默认值。命名规则按示例服务名实时预览。 */
import { computed, ref } from 'vue'
import type { EnvironmentProfile, IngressProfile, NamingKey } from '@/api/types'

const profile = defineModel<EnvironmentProfile>({ required: true })
const ingressProfileId = defineModel<number | null>('ingressProfileId', { required: true })
const props = defineProps<{ namespaceName: string; ingressProfiles: IngressProfile[]; readonly?: boolean }>()

const NAMING: [NamingKey, string][] = [
  ['deployment', 'Deployment'], ['service', 'Service'], ['ingress', 'Ingress'],
  ['configmap', '环境变量 ConfigMap'], ['secret', '敏感变量 Secret'], ['properties', '服务配置文件 ConfigMap'],
]
const sample = ref('order-api')
const fill = (text: string, key: string, value: string) => text.split(`{${key}}`).join(value)
const render = (template: string) => {
  const name = sample.value || 'order-api'
  return fill(fill(fill(template, 'base', name), 'service', name), 'namespace', props.namespaceName)
}
const selectedIngress = computed(() => props.ingressProfiles.find((p) => p.id === ingressProfileId.value) ?? null)
</script>

<template>
  <el-form label-position="top" :disabled="readonly">
    <h4>对象命名</h4>
    <p class="hint">占位符：{base} 为服务的资源基础名（默认等于服务名），{service} 为服务名，{namespace} 为命名空间名。</p>
    <el-form-item label="预览用的服务名">
      <el-input v-model="sample" class="sample" :disabled="false" />
    </el-form-item>
    <div class="form-grid">
      <el-form-item v-for="[key, label] in NAMING" :key="key" :label="label">
        <el-input v-model="profile.naming[key]" />
        <span class="hint mono">{{ render(profile.naming[key]) }}</span>
      </el-form-item>
    </div>

    <h4>访问入口</h4>
    <div class="form-grid">
      <el-form-item label="负载均衡入口">
        <el-select v-model="ingressProfileId" clearable placeholder="不创建 Ingress">
          <el-option v-for="p in ingressProfiles" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <span v-if="selectedIngress" class="hint">ELB {{ selectedIngress.spec.elb_id }} · 端口 {{ selectedIngress.spec.port }}
          · 证书 {{ selectedIngress.spec.certificate_ids.length }} 个</span>
      </el-form-item>
      <el-form-item label="主机名规则">
        <el-input v-model="profile.host" placeholder="{base}-app.{namespace}" />
        <span class="hint mono">{{ render(profile.host) }}</span>
      </el-form-item>
    </div>

    <h4>调度</h4>
    <div class="form-grid">
      <el-form-item label="节点池标签">
        <el-input v-model="profile.scheduling.node_pool_label" />
      </el-form-item>
      <el-form-item label="节点池（为空表示不限）">
        <el-input v-model="profile.scheduling.node_pool" />
      </el-form-item>
      <el-form-item label="同服务 Pod 不在同一节点">
        <el-switch v-model="profile.scheduling.anti_affinity" />
      </el-form-item>
      <el-form-item label="跨可用区打散">
        <el-switch v-model="profile.scheduling.zone_spread" />
      </el-form-item>
      <el-form-item label="镜像拉取密钥">
        <el-input v-model="profile.image_pull_secret" placeholder="default-secret" />
      </el-form-item>
    </div>

    <h4>日志目录</h4>
    <div class="form-grid">
      <el-form-item label="挂载节点日志目录">
        <el-switch v-model="profile.log.enabled" />
      </el-form-item>
      <template v-if="profile.log.enabled">
        <el-form-item label="节点目录（hostPath）"><el-input v-model="profile.log.host_path" placeholder="/data01/log" /></el-form-item>
        <el-form-item label="容器内路径"><el-input v-model="profile.log.mount_path" placeholder="/applog" /></el-form-item>
        <el-form-item label="子目录表达式（subPathExpr）" class="wide">
          <el-input v-model="profile.log.sub_path_expr" placeholder="$(POD_NAMESPACE)/$(POD_NAME)" />
        </el-form-item>
      </template>
    </div>

    <h4>资源与副本默认值</h4>
    <div class="form-grid">
      <el-form-item label="副本数"><el-input-number v-model="profile.defaults.replicas" :min="0" :max="100" /></el-form-item>
      <el-form-item label="CPU 请求"><el-input v-model="profile.defaults.cpu_request" placeholder="500m" /></el-form-item>
      <el-form-item label="CPU 上限"><el-input v-model="profile.defaults.cpu_limit" placeholder="2" /></el-form-item>
      <el-form-item label="内存请求"><el-input v-model="profile.defaults.memory_request" placeholder="4Gi" /></el-form-item>
      <el-form-item label="内存上限"><el-input v-model="profile.defaults.memory_limit" placeholder="4Gi" /></el-form-item>
    </div>
  </el-form>
</template>

<style scoped>
h4 { margin: 18px 0 6px; }
h4:first-child { margin-top: 0; }
.sample { max-width: 280px; }
.hint.mono { display: block; margin-top: 4px; word-break: break-all; }
</style>
