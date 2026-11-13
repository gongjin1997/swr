<script setup lang="ts">
/** 表格分页条：前端分页（usePagination）与后端分页共用。总数不超过最小页长时不显示。 */
import { computed } from 'vue'
import { PAGE_SIZES } from '@/composables/usePagination'

const props = withDefaults(defineProps<{ total: number; sizes?: number[]; compact?: boolean }>(), { sizes: () => PAGE_SIZES })
const page = defineModel<number>('page', { required: true })
const size = defineModel<number>('size', { required: true })
const emit = defineEmits<{ change: [] }>()
const visible = computed(() => props.total > Math.min(...props.sizes))

function changePage(value: number) {
  page.value = value
  emit('change')
}

function changeSize(value: number) {
  size.value = value
  page.value = 1
  emit('change')
}
</script>

<template>
  <div v-if="visible" class="pager">
    <el-pagination :current-page="page" :page-size="size" :total="total" :page-sizes="sizes" background
      :size="compact ? 'small' : 'default'" :layout="compact ? 'total, prev, pager, next' : 'total, sizes, prev, pager, next, jumper'"
      @current-change="changePage" @size-change="changeSize" />
  </div>
</template>

<style scoped>
.pager { display: flex; justify-content: flex-end; padding-top: 14px; overflow-x: auto; }
</style>
