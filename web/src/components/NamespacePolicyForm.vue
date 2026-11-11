<script setup lang="ts">
/** 命名空间发布策略：审批、副本上限、允许的 SWR 组织、发布冻结、发布窗口。 */
import type { NamespacePolicy, Tier } from '@/api/types'
import { WEEKDAYS } from '@/constants'

const policy = defineModel<NamespacePolicy>({ required: true })
defineProps<{ tier?: Tier }>()

const TIMEZONES = ['Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Singapore', 'Asia/Tokyo', 'Europe/London', 'UTC']

function addWindow() {
  policy.value.deploy_windows = [...policy.value.deploy_windows, { days: [1, 2, 3, 4, 5], start: '09:00', end: '18:00', tz: 'Asia/Shanghai' }]
}

function removeWindow(index: number) {
  policy.value.deploy_windows = policy.value.deploy_windows.filter((_, i) => i !== index)
}
</script>

<template>
  <div>
    <div class="form-grid">
      <el-form-item label="发布需要审批">
        <el-switch v-model="policy.require_approval" :disabled="tier === 'staging' || tier === 'prod'" />
        <div class="hint">UAT（预生产）与生产环境始终需要独立审批</div>
      </el-form-item>
      <el-form-item label="平台管理员确认">
        <el-switch v-model="policy.require_admin_approval" />
        <div class="hint">启用后只有平台管理员可确认；生产环境始终要求管理员确认</div>
      </el-form-item>
      <el-form-item label="最少审批人数"><el-input-number v-model="policy.min_approvals" :min="1" :max="5" /></el-form-item>
      <el-form-item label="最大副本数"><el-input-number v-model="policy.max_replicas" :min="1" :max="1000" /></el-form-item>
      <el-form-item label="滚动发布超时（秒）"><el-input-number v-model="policy.rollout_timeout" :min="30" :max="3600" :step="30" /></el-form-item>
    </div>
    <el-form-item label="允许的 SWR 组织">
      <el-select
        v-model="policy.allowed_registry_orgs"
        multiple
        filterable
        allow-create
        default-first-option
        :reserve-keyword="false"
        placeholder="输入组织名后回车；只有这些组织的镜像可以发布到该命名空间"
        style="width: 100%"
      />
    </el-form-item>

    <el-divider content-position="left">发布冻结</el-divider>
    <div class="form-grid">
      <el-form-item label="启用冻结"><el-switch v-model="policy.freeze.enabled" /></el-form-item>
      <el-form-item label="截止时间（留空表示直到手动解除）">
        <el-date-picker
          v-model="policy.freeze.until"
          type="datetime"
          value-format="YYYY-MM-DD[T]HH:mm:ssZ"
          :disabled="!policy.freeze.enabled"
          placeholder="选择截止时间"
          clearable
        />
      </el-form-item>
      <el-form-item label="原因" class="wide">
        <el-input v-model="policy.freeze.reason" maxlength="300" :disabled="!policy.freeze.enabled" />
      </el-form-item>
    </div>

    <el-divider content-position="left">发布窗口（不设置表示任何时间都可发布）</el-divider>
    <div v-for="(w, i) in policy.deploy_windows" :key="i" class="window">
      <el-checkbox-group v-model="w.days">
        <el-checkbox v-for="[day, label] in WEEKDAYS" :key="day" :value="day">{{ label }}</el-checkbox>
      </el-checkbox-group>
      <div class="window-row">
        <el-time-picker v-model="w.start" format="HH:mm" value-format="HH:mm" placeholder="开始" style="width: 120px" />
        <span>至</span>
        <el-time-picker v-model="w.end" format="HH:mm" value-format="HH:mm" placeholder="结束" style="width: 120px" />
        <el-select v-model="w.tz" filterable allow-create style="width: 180px">
          <el-option v-for="tz in TIMEZONES" :key="tz" :label="tz" :value="tz" />
        </el-select>
        <el-button link type="danger" @click="removeWindow(i)">删除</el-button>
      </div>
      <div class="hint">结束早于开始表示跨午夜，例如 22:00 至 06:00</div>
    </div>
    <el-button size="small" @click="addWindow">新增窗口</el-button>
  </div>
</template>

<style scoped>
.window {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  margin-bottom: 10px;
}
.window-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
</style>
