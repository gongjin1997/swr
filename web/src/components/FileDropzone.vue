<script setup lang="ts">
/** 拖拽或点击选择上传文件；同名文件替换；超过上传上限的文件直接拒绝。 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fmtSize } from '@/utils/format'

const props = defineProps<{ maxMb?: number; destinations?: string[] }>()
const files = defineModel<File[]>({ required: true })
const input = ref<HTMLInputElement>()
const over = ref(false)

function add(list: FileList | null) {
  if (!list) return
  const next = [...files.value]
  for (const file of Array.from(list)) {
    if (props.maxMb && file.size > props.maxMb * 1024 * 1024) {
      ElMessage({ type: 'error', message: `${file.name} 超过上传限制 ${props.maxMb} MB` })
      continue
    }
    const index = next.findIndex((f) => f.name === file.name)
    if (index >= 0) {
      ElMessage({ type: 'info', message: `已替换同名文件 ${file.name}` })
      next.splice(index, 1)
    }
    next.push(file)
  }
  files.value = next
}

function onDrop(e: DragEvent) {
  over.value = false
  add(e.dataTransfer?.files ?? null)
}

function onPick() {
  add(input.value?.files ?? null)
  if (input.value) input.value.value = ''
}

function remove(index: number) {
  files.value = files.value.filter((_, i) => i !== index)
}
</script>

<template>
  <div>
    <div
      class="dropzone"
      :class="{ over }"
      role="button"
      tabindex="0"
      aria-label="选择要上传的文件"
      @click="input?.click()"
      @keydown.enter.prevent="input?.click()"
      @keydown.space.prevent="input?.click()"
      @dragover.prevent="over = true"
      @dragleave="over = false"
      @drop.prevent="onDrop"
    >
      <input ref="input" type="file" multiple hidden @change="onPick" />
      <div><b>拖拽文件到这里</b>，或点击选择</div>
      <div class="hint">jar / war / zip / 配置文件，可多选<template v-if="maxMb">，单个文件不超过 {{ maxMb }} MB</template></div>
    </div>
    <ul v-if="files.length" class="files">
      <li v-for="(file, i) in files" :key="file.name">
        <div>
          <span>{{ file.name }}</span> <span class="hint">{{ fmtSize(file.size) }}</span>
          <div v-if="destinations?.[i]" class="hint mono">→ {{ destinations[i] }}</div>
        </div>
        <el-button link type="danger" @click="remove(i)">移除</el-button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.dropzone {
  border: 1.5px dashed var(--el-border-color);
  border-radius: 8px;
  padding: 22px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.dropzone:hover,
.dropzone:focus-visible,
.dropzone.over {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  outline: none;
}
.files {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
}
.files li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
</style>
