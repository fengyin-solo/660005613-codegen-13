<template>
  <el-dialog v-model="visible" title="📤 导出执行结果" width="760px"
             class="export-dialog" @open="onOpen" @closed="onClosed">
    <div class="form-block">
      <div class="form-label">
        <span>选择执行（可多选）</span>
        <span class="hint">已选 {{ selectedRuns.length }} 次</span>
      </div>
      <el-table :data="store.runs" height="220" size="small"
                @selection-change="onSelectionChange"
                :row-key="(r: RunSummary) => r.id" ref="tableRef"
                @row-click="toggleRow"
                empty-text="暂无历史执行，请先执行一次工作流">
        <el-table-column type="selection" width="42" reserve-selection />
        <el-table-column label="#" width="52">
          <template #default="{ row }">#{{ row.id }}</template>
        </el-table-column>
        <el-table-column prop="name" label="名称" width="110" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="runStatusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="开始时间">
          <template #default="{ row }">{{ formatTime(row.startTs) }}</template>
        </el-table-column>
        <el-table-column label="任务" width="120">
          <template #default="{ row }">
            ✓{{ row.taskSuccess }} ✗{{ row.taskFailed }}
            <span v-if="row.taskSkipped"> ⊘{{ row.taskSkipped }}</span>
          </template>
        </el-table-column>
        <el-table-column label="日志" prop="logCount" width="60" />
      </el-table>
    </div>

    <div class="form-block">
      <div class="form-label">包含环节</div>
      <el-checkbox-group v-model="sections" @change="schedulePreview">
        <el-checkbox v-for="s in SECTION_ORDER" :key="s" :value="s">
          {{ SECTION_LABELS[s] }}
        </el-checkbox>
      </el-checkbox-group>
    </div>

    <div class="form-block">
      <div class="form-label">
        <span>时间范围</span>
        <el-button link type="primary" size="small" @click="timeRange = null"
                   :disabled="!timeRange">清除（全部时间）</el-button>
      </div>
      <el-date-picker
        v-model="timeRange" type="datetimerange" size="small"
        range-separator="至" start-placeholder="开始时间" end-placeholder="结束时间"
        value-format="x" :clearable="true" style="width:100%"
        @change="schedulePreview" />
      <div class="hint">仅筛选「任务执行结果」与「执行日志」；工作流定义与熔断器为整次执行的快照，不受时间范围影响。</div>
    </div>

    <div class="preview-block" v-loading="previewLoading">
      <template v-if="preview">
        <div v-if="!preview.hasData" class="preview-empty">
          ⚠ 当前范围内没有符合条件的数据，将不会生成文件。请调整执行、环节或时间范围。
        </div>
        <template v-else>
          <div class="preview-title">导出预览</div>
          <div class="preview-grid">
            <span>执行 <b>{{ preview.counts.runCount }}</b> 次</span>
            <span v-if="sections.includes('workflow')">工作流定义 <b>{{ preview.counts.workflowCount }}</b></span>
            <span v-if="sections.includes('tasks')">任务结果 <b>{{ preview.counts.taskCount }}</b> 条</span>
            <span v-if="sections.includes('logs')">日志 <b>{{ preview.counts.logCount }}</b> 条</span>
            <span v-if="sections.includes('breakers')">熔断器 <b>{{ preview.counts.breakerCount }}</b> 条</span>
          </div>
          <div v-if="reusedHint" class="preview-reuse">ℹ {{ reusedHint }}</div>
        </template>
      </template>
      <div v-else-if="!previewLoading && !selectedRuns.length" class="preview-empty">
        请先选择至少一次执行。
      </div>
    </div>

    <template #footer>
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button type="primary" size="small" :loading="store.creating"
                 :disabled="!canSubmit" @click="submit">导出</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { useExportStore } from '@/store/export'
import type { RunSummary, ExportSection, ExportRecord, ExportPreview } from '@/types'
import { SECTION_LABELS, SECTION_ORDER, formatTime, runStatusType } from '@/utils/format'

const emit = defineEmits<{ (e: 'done', record: ExportRecord, reused: boolean): void }>()

const store = useExportStore()
const visible = ref(false)
const selectedRuns = ref<RunSummary[]>([])
const sections = ref<ExportSection[]>([...SECTION_ORDER])
const timeRange = ref<[number, number] | null>(null)
const preview = ref<ExportPreview | null>(null)
const previewLoading = ref(false)
const tableRef = ref()
const presetRunIds = ref<number[]>([])
let previewTimer: number | null = null

const canSubmit = computed(() =>
  selectedRuns.value.length > 0 && sections.value.length > 0)

function onSelectionChange(rows: RunSummary[]) {
  selectedRuns.value = rows
  schedulePreview()
}

function toggleRow(row: RunSummary) {
  tableRef.value?.toggleRowSelection(row)
}

function body() {
  return {
    runIds: selectedRuns.value.map(r => r.id),
    sections: sections.value,
    startTs: timeRange.value ? Number(timeRange.value[0]) / 1000 : null,
    endTs: timeRange.value ? Number(timeRange.value[1]) / 1000 : null,
  }
}

function scopeSignature() {
  return JSON.stringify(body())
}

async function onOpen() {
  sections.value = [...SECTION_ORDER]
  timeRange.value = null
  preview.value = null
  await store.loadRuns()
  // Restore selection from preset ids after table data is rendered.
  const preset = presetRunIds.value
  await nextTick()
  tableRef.value?.clearSelection?.()
  if (preset.length) {
    const matched = store.runs.filter(r => preset.includes(r.id))
    matched.forEach(r => tableRef.value?.toggleRowSelection(r, true))
    selectedRuns.value = matched
  } else {
    selectedRuns.value = []
  }
  schedulePreview()
}

async function openPreset(runIds: number[]) {
  presetRunIds.value = runIds
  visible.value = true
}

function onClosed() {
  if (previewTimer !== null) window.clearTimeout(previewTimer)
}

// Debounced live preview — the same /export-preview backend routine that
// builds the real file, so what you see is exactly what gets exported.
function schedulePreview() {
  if (!canSubmit.value) {
    preview.value = null
    return
  }
  if (previewTimer !== null) window.clearTimeout(previewTimer)
  previewTimer = window.setTimeout(runPreview, 250)
}

const lastSignature = ref('')
async function runPreview() {
  const b = body()
  const sig = scopeSignature()
  lastSignature.value = sig
  previewLoading.value = true
  try {
    const p = await store.preview(b)
    if (scopeSignature() === sig) preview.value = p
  } catch (e: any) {
    preview.value = null
    ElMessage.warning(e?.response?.data?.detail || '预览失败')
  } finally {
    previewLoading.value = false
  }
}

const reusedHint = computed(() => {
  const ids = new Set(selectedRuns.value.map(r => r.id))
  const existing = store.exportsList.find(ex =>
    ex.status === 'SUCCESS' &&
    ex.runIds.length === ids.size &&
    ex.runIds.every(id => ids.has(id)) &&
    JSON.stringify([...ex.sections].sort()) === JSON.stringify([...sections.value].sort()) &&
    (ex.startTs ?? null) === (body().startTs ?? null) &&
    (ex.endTs ?? null) === (body().endTs ?? null))
  if (existing) return `相同范围已导出过（记录 #${existing.id}），本次将直接复用该文件，不会重复生成。`
  return ''
})

async function submit() {
  if (!preview.value) return
  if (!preview.value.hasData) {
    ElMessage.warning('当前范围没有符合条件的数据，无法导出')
    return
  }
  try {
    const { reused, record } = await store.createExport(body())
    if (reused && record.status === 'SUCCESS') {
      ElMessage.success(`已复用导出记录 #${record.id} 的文件，未重复生成`)
    } else if (record.status === 'PROCESSING') {
      ElMessage.success(reused ? `已重新开始导出 #${record.id}` : `导出 #${record.id} 已开始`)
    } else {
      ElMessage.success(`导出 #${record.id} 已创建`)
    }
    emit('done', record, reused)
    visible.value = false
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导出创建失败')
  }
}

defineExpose({
  open: () => { presetRunIds.value = []; visible.value = true },
  openPreset,
})
</script>

<style scoped>
.form-block { margin-bottom: 14px }
.form-label {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 12px; color: #bb86fc; margin-bottom: 6px; font-weight: 600;
}
.hint { color: #7a7a9a; font-size: 11px; font-weight: 400; margin-top: 4px }
.preview-block {
  background: #14142b; border: 1px solid #2a2a4a; border-radius: 6px;
  padding: 10px 12px; min-height: 52px; font-size: 12px;
}
.preview-title { color: #bb86fc; font-weight: 600; margin-bottom: 6px }
.preview-grid { display: flex; flex-wrap: wrap; gap: 14px; color: #c0c0d8 }
.preview-grid b { color: #e0e0f0 }
.preview-empty { color: #fbbf24; font-size: 12px }
.preview-reuse { margin-top: 8px; color: #8be9fd; font-size: 11px }
:deep(.el-table), :deep(.el-table tr), :deep(.el-table th.el-table__cell) {
  background-color: #14142b; color: #d0d0e4;
}
:deep(.el-table td.el-table__cell), :deep(.el-table th.el-table__cell.is-leaf) {
  border-bottom: 1px solid #23234a;
}
:deep(.el-table--enable-row-hover .el-table__body tr:hover>td.el-table__cell) {
  background-color: #1d1d3d;
}
:deep(.el-checkbox__label) { color: #d0d0e4; font-size: 12px }
</style>
