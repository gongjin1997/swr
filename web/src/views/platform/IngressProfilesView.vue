<script setup lang="ts">
/** 负载均衡入口：同一环境的多个命名空间共用一个 ELB 与监听器；换证书只改这里。 */
import { Plus, Refresh } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ingressProfileApi } from '@/api'
import type { IngressProfile, IngressSpec } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import TablePager from '@/components/TablePager.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { usePagination } from '@/composables/usePagination'
import { useSession } from '@/stores/session'

const session = useSession()
const items = ref<IngressProfile[]>([])
const pager = usePagination(() => items.value)
const loading = ref(false)
const { busy, run } = useAction()
const manage = computed(() => session.canGlobal('certificate.manage'))
const ELB_CLASSES: [string, string][] = [['performance', '独享型（performance）'], ['union', '共享型（union）']]
const URL_MODES: [string, string][] = [['', '不设置（CCE 默认）'], ['STARTS_WITH', '前缀匹配（STARTS_WITH）'],
  ['EQUAL_TO', '精确匹配（EQUAL_TO）'], ['REGEX', '正则匹配（REGEX）']]
const editor = reactive({
  visible: false, item: null as IngressProfile | null, name: '', description: '', ingressClass: 'cce', elbClass: 'performance',
  elbId: '', port: 443, certificates: '', annotations: '', urlMatchMode: '',
})

async function load() {
  loading.value = true
  try {
    items.value = (await ingressProfileApi.list()).items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(item: IngressProfile | null) {
  const spec = item?.spec
  Object.assign(editor, {
    visible: true, item, name: item?.name ?? '', description: item?.description ?? '', ingressClass: spec?.ingress_class ?? 'cce',
    elbClass: spec?.elb_class ?? 'performance', elbId: spec?.elb_id ?? '', port: spec?.port ?? 443,
    certificates: (spec?.certificate_ids ?? []).join('\n'),
    annotations: Object.entries(spec?.annotations ?? {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    urlMatchMode: spec?.url_match_mode ?? '',
  })
}

function buildSpec(): IngressSpec {
  const annotations: Record<string, string> = {}
  for (const line of editor.annotations.split(/\r?\n/)) {
    const text = line.trim()
    if (!text) continue
    const index = text.indexOf('=')
    if (index <= 0) throw new Error(`注解格式应为 键=值：${text}`)
    annotations[text.slice(0, index).trim()] = text.slice(index + 1).trim()
  }
  return {
    ingress_class: editor.ingressClass.trim(), elb_class: editor.elbClass, elb_id: editor.elbId.trim(), port: editor.port,
    certificate_ids: editor.certificates.split(/[\s,]+/).map((c) => c.trim()).filter(Boolean), annotations,
    url_match_mode: editor.urlMatchMode,
  }
}

async function save() {
  const ok = await run(async () => {
    const body = { name: editor.name.trim(), description: editor.description.trim(), spec: buildSpec() }
    if (editor.item) await ingressProfileApi.update(editor.item.id, { ...body, base_version: editor.item.version })
    else await ingressProfileApi.create(body)
  }, '负载均衡入口已保存')
  if (ok) {
    editor.visible = false
    await load()
  }
}

async function remove(item: IngressProfile) {
  if (!await confirmAction(`删除负载均衡入口"${item.name}"？`, '删除', true)) return
  if (await run(() => ingressProfileApi.remove(item.id), '已删除')) await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="负载均衡入口"
      description="CCE ELB Ingress 的公共设置：ELB 实例、监听端口与 ELB 证书。多个命名空间可以共用一个入口，CCE 以最早创建的 Ingress 的监听器设置为准。">
      <el-button v-if="manage" type="primary" :icon="Plus" @click="open(null)">新建入口</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <span class="count">共 {{ items.length }} 个</span>
      </div>
      <el-table v-loading="loading" :data="pager.rows.value" empty-text="还没有负载均衡入口">
        <el-table-column label="名称" min-width="180">
          <template #default="{ row }"><b>{{ row.name }}</b><div class="hint">{{ row.description }}</div></template>
        </el-table-column>
        <el-table-column label="ELB" min-width="260">
          <template #default="{ row }"><span class="mono">{{ row.spec.elb_id }}</span><div class="hint">{{ row.spec.elb_class }} · {{ row.spec.ingress_class }}</div></template>
        </el-table-column>
        <el-table-column label="端口" width="80"><template #default="{ row }">{{ row.spec.port }}</template></el-table-column>
        <el-table-column label="ELB 证书" min-width="220">
          <template #default="{ row }"><span class="mono break-word">{{ row.spec.certificate_ids.join(', ') || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="使用环境" width="90"><template #default="{ row }">{{ row.environment_count }}</template></el-table-column>
        <el-table-column v-if="manage" label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="open(row)">编辑</el-button>
            <el-button link type="danger" :disabled="row.environment_count > 0" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
    </el-card>

    <el-dialog v-model="editor.visible" :title="editor.item ? `编辑 · ${editor.item.name}` : '新建负载均衡入口'" width="640px" :close-on-click-modal="false">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="名称"><el-input v-model="editor.name" maxlength="80" placeholder="例如 SIT 共享 ELB" /></el-form-item>
          <el-form-item label="说明"><el-input v-model="editor.description" maxlength="300" /></el-form-item>
          <el-form-item label="ELB 类型">
            <el-select v-model="editor.elbClass">
              <el-option v-for="[value, label] in ELB_CLASSES" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="IngressClass"><el-input v-model="editor.ingressClass" /></el-form-item>
          <el-form-item label="ELB 实例 ID" class="wide"><el-input v-model="editor.elbId" /></el-form-item>
          <el-form-item label="监听端口"><el-input-number v-model="editor.port" :min="1" :max="65535" /></el-form-item>
          <el-form-item label="路径匹配方式">
            <el-select v-model="editor.urlMatchMode">
              <el-option v-for="[value, label] in URL_MODES" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="ELB 证书 ID（每行一个；第一个为服务器证书，其余为 SNI 证书）">
          <el-input v-model="editor.certificates" type="textarea" :rows="3" class="mono" />
        </el-form-item>
        <el-form-item label="其他 Ingress 注解（每行一个 键=值，可选）">
          <el-input v-model="editor.annotations" type="textarea" :rows="3" class="mono" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editor.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
