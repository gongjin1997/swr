<script setup lang="ts">
/** 项目的 SWR 授权：每行一个组织 + 若干镜像仓库前缀；前缀留空表示授权该组织下所有仓库。 */
import type { RegistryOrg } from '@/api/types'

const orgs = defineModel<RegistryOrg[]>({ required: true })

function add() {
  orgs.value = [...orgs.value, { namespace: '', base_prefixes: [] }]
}

function remove(index: number) {
  orgs.value = orgs.value.filter((_, i) => i !== index)
}
</script>

<template>
  <div class="orgs">
    <div v-for="(org, i) in orgs" :key="i" class="org-row">
      <el-input v-model="org.namespace" placeholder="SWR 组织，如 team-a" class="org" />
      <el-select
        v-model="org.base_prefixes"
        multiple
        filterable
        allow-create
        default-first-option
        :reserve-keyword="false"
        placeholder="仓库前缀，回车添加；留空 = 全部仓库"
        class="prefixes"
      />
      <el-button link type="danger" @click="remove(i)">删除</el-button>
    </div>
    <el-button size="small" @click="add">添加组织</el-button>
    <div class="hint">仓库前缀匹配镜像路径，例如前缀 api 允许 api 和 api/* 下的仓库。</div>
  </div>
</template>

<style scoped>
.orgs {
  width: 100%;
}
.org-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
}
.org {
  width: 200px;
  flex: none;
}
.prefixes {
  flex: 1;
}
</style>
