<script setup lang="ts">
import { STATUS_TAG, STATUS_TEXT } from '@/constants'

defineProps<{ status: string }>()
</script>

<template>
  <el-tag :type="STATUS_TAG[status] ?? 'info'" disable-transitions>{{ STATUS_TEXT[status] ?? status }}</el-tag>
</template>
