<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{ lines: string[]; truncated?: boolean; height?: string; label?: string }>()
const box = ref<HTMLElement>()

function lineClass(line: string): string {
  if (line.startsWith('==>')) return 'l-step'
  if (line.includes('[ERROR]')) return 'l-err'
  if (line.includes('[WARN]')) return 'l-warn'
  if (line.includes('构建成功：')) return 'l-ok'
  if (line.startsWith('$ ')) return 'l-cmd'
  return ''
}

// 只有用户停在底部附近时才自动滚动，避免打断向上翻看
watch(
  () => props.lines.length,
  async () => {
    const el = box.value
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    await nextTick()
    if (atBottom) el.scrollTop = el.scrollHeight
  },
)
</script>

<template>
  <div>
    <p v-if="truncated" class="hint">日志较长，只显示最后 20000 行。</p>
    <pre ref="box" class="log" :style="{ height: height ?? '480px' }" tabindex="0" :aria-label="label ?? '日志'"><span
      v-for="(line, i) in lines" :key="i" :class="lineClass(line)">{{ line }}
</span></pre>
  </div>
</template>

<style scoped>
.log {
  margin: 0;
  overflow: auto;
  background: var(--app-log-bg);
  color: var(--app-log-text);
  font-family: var(--app-mono);
  font-size: 12.5px;
  line-height: 1.55;
  padding: 12px 14px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
}
.l-step {
  color: #7cc4ff;
  font-weight: 600;
}
.l-err {
  color: #ff8a80;
}
.l-warn {
  color: #ffcc80;
}
.l-ok {
  color: #a5d6a7;
  font-weight: 600;
}
.l-cmd {
  color: #a9a59e;
}
</style>
