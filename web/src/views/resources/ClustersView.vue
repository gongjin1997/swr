<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { clusterApi } from '@/api'
import type { Cluster } from '@/api/types'
import { notifyError, notifySuccess, useAction } from '@/composables/useAction'

const clusters = ref<Cluster[]>([])
const loading = ref(false)
const testing = ref<number | null>(null)
const { busy, run } = useAction()
const form = reactive({ visible: false, id: null as number | null, name: '', server: '', cce: '', token: '', ca: '' })

async function load() {
  loading.value = true
  try {
    clusters.value = (await clusterApi.list()).items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(cluster: Cluster | null) {
  Object.assign(form, {
    visible: true, id: cluster?.id ?? null, name: cluster?.name ?? '', server: cluster?.server ?? '',
    cce: cluster?.cce_cluster_id ?? '', token: '', ca: '',
  })
}

async function save() {
  const base = { name: form.name.trim(), server: form.server.trim(), auth_type: 'sa_token' as const, cce_cluster_id: form.cce.trim() }
  const credential = { token: form.token, ca_pem: form.ca }
  const ok = await run(async () => {
    const saved = form.id
      ? await clusterApi.update(form.id, form.token ? { ...base, credential } : base)
      : await clusterApi.create({ ...base, credential })
    const result = await clusterApi.test(saved.id)
    notifySuccess(`已连接 Kubernetes ${result.version}`)
  })
  if (ok) {
    form.visible = false
    await load()
  } else if (!form.id) {
    await load() // 创建成功但连接测试失败时，列表里已经有这个集群
  }
}

async function test(cluster: Cluster) {
  testing.value = cluster.id
  try {
    const r = await clusterApi.test(cluster.id)
    notifySuccess(`已连接 ${r.version}`)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    testing.value = null
  }
}

onMounted(load)
</script>

<template>
  <el-card>
    <template #header>
      <div class="card-title"><span>集群</span><el-button type="primary" @click="open(null)">纳管集群</el-button></div>
    </template>
    <el-table v-loading="loading" :data="clusters" empty-text="暂无纳管集群">
      <el-table-column label="集群" min-width="180">
        <template #default="{ row }"><b>{{ row.name }}</b><div class="hint">{{ row.cce_cluster_id }}</div></template>
      </el-table-column>
      <el-table-column label="API Server" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.server }}</span></template></el-table-column>
      <el-table-column prop="auth_type" label="认证" width="110" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }"><el-tag :type="row.status === 'ready' ? 'success' : 'info'">{{ row.status === 'ready' ? '可用' : '未验证' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="170">
        <template #default="{ row }">
          <el-button link type="primary" :loading="testing === row.id" @click="test(row)">连接测试</el-button>
          <el-button link type="primary" @click="open(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="form.visible" :title="form.id ? '编辑集群' : '纳管集群'" width="680px">
    <el-form label-position="top">
      <div class="form-grid">
        <el-form-item label="名称"><el-input v-model="form.name" maxlength="80" /></el-form-item>
        <el-form-item label="CCE 集群 ID（可选）"><el-input v-model="form.cce" maxlength="128" /></el-form-item>
      </div>
      <el-form-item label="Kubernetes API Server（HTTPS）"><el-input v-model="form.server" placeholder="https://kube.example:5443" /></el-form-item>
      <el-form-item label="ServiceAccount Token">
        <el-input v-model="form.token" type="password" show-password autocomplete="new-password" :placeholder="form.id ? '留空保持不变' : ''" />
      </el-form-item>
      <el-form-item label="CA 证书 PEM（可选）">
        <el-input v-model="form.ca" type="textarea" :rows="5" class="mono" spellcheck="false" :placeholder="form.id ? '修改 Token 时一并提交；留空表示不使用自定义 CA' : ''" />
      </el-form-item>
      <el-alert type="info" :closable="false" title="保存后平台会加密凭据。纳管不会创建集群资源，也不会授予 Kubernetes 权限。" />
    </el-form>
    <template #footer>
      <el-button @click="form.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">连接并保存</el-button>
    </template>
  </el-dialog>
</template>
