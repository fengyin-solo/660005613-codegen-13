<template>
  <el-dialog v-model="visible" title="导出执行结果" width="640px" @open="onOpen" @closed="onClosed">
    <div class="export-form">
      <section>
        <div class="sec-title">
          <span>① 选择执行</span>
          <span class="hint">可多选，仅已完成的执行可导出</span>
        </div>
        <el-table :data="store.runs" max-height="180" size="small"
                  @selection-change="onSelectionChange" ref="runTable"
                  :row-key="(r: RunSummary) => r.id">
          <el-table-column type="selection" width="42"
                           :selectable="(r: RunSummary) => r.status === 'completed'" />
          <el-table-column prop="id" label="#" width="50" />
          <el-table-column label="执行时间" min-width="150">
            <template #default="{ row }">{{ fmtTime(row.startedAt || row.createdAt) }}</template>
          </el-table-column>
          <el-table-column label="Worker / 策略" width="120">
            <template #default="{ row }">{{ row.workers }} · {{ strategyText(row.strategy) }}</template>
          </el-table-column>
          <el-table-column label="环节/日志" width="90">
            <template #default="{ row }">{{ row.nodeCount }}/{{ row.logCount }}</template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'completed' ? 'success' : 'warning'">
                {{ row.status === 'completed' ? '已完成' : '执行中' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section>
        <div class="sec-title">
          <span>② 选择环节</span>
          <div>
            <el-button link type="primary" size="small" @click="selectAllStages">全选</el-button>
            <el-button link size="small" @click="selectedStages = []">清空</el-button>
          </div>
        </div>
        <el-checkbox-group v-model="selectedStages" size="small" class="stage-group">
          <el-checkbox v-for="s in store.stages" :key="s.id" :value="s.id" border size="small">
            {{ s.name }}
          </el-checkbox>
        </el-checkbox-group>
      </section>

      <section>
        <div class="sec-title">
          <el-checkbox v-model="limitTime"><span>③ 限定时间范围</span></el-checkbox>
          <span class="hint">不勾选则包含全部时间</span>
        </div>
        <el-date-picker v-if="limitTime" v-model="dateRange" type="datetimerange"
                        range-separator="至" start-placeholder="开始时间" end-placeholder="结束时间"
                        format="MM-DD HH:mm:ss" value-format="x" size="small" style="width:100%" />
      </section>

      <section class="preview" v-loading="previewLoading">
        <template v-if="hasSelection">
          <el-alert v-if="preview && preview.empty" type="warning" :closable="false" show-icon
                    title="当前范围内没有符合条件的数据，将不会生成文件" />
          <div v-else-if="preview" class="preview-stats">
            <el-tag type="info" size="small">执行 {{ preview.runCount }} 次</el-tag>
            <el-tag type="info" size="small">环节明细 {{ preview.nodeCount }} 条</el-tag>
            <el-tag type="info" size="small">日志 {{ preview.logCount }} 条</el-tag>
            <el-tag type="info" size="small">熔断状态 {{ preview.breakerCount }} 条</el-tag>
          </div>
        </template>
        <div v-else class="hint">请选择至少一次执行和一个环节</div>
      </section>
    </div>
    <template #footer>
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button type="primary" size="small" :loading="exporting"
                 :disabled="!canExport" @click="doExport">
        {{ exporting ? '导出中...' : '生成并下载' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useDAGStore } from '../store/dag'
import type { RunSummary, ExportPreview } from '@/types'

const store = useDAGStore()
const visible = ref(false)
const selectedRuns = ref<RunSummary[]>([])
const selectedStages = ref<string[]>([])
const limitTime = ref(false)
const dateRange = ref<[string | number, string | number] | null>(null)
const preview = ref<ExportPreview | null>(null)
const previewLoading = ref(false)
const exporting = ref(false)
const runTable = ref()

const hasSelection = computed(() => selectedRuns.value.length > 0 && selectedStages.value.length > 0)
const canExport = computed(() => !!preview.value && !preview.value.empty && !previewLoading.value)

const request = computed(() => ({
  runIds: selectedRuns.value.map(r => r.id).sort((a, b) => a - b),
  stages: [...selectedStages.value].sort(),
  timeStart: limitTime.value && dateRange.value ? Number(dateRange.value[0]) / 1000 : undefined,
  timeEnd: limitTime.value && dateRange.value ? Number(dateRange.value[1]) / 1000 : undefined,
}))

let timer: ReturnType<typeof setTimeout> | null = null
watch(request, () => {
  if (timer) clearTimeout(timer)
  if (!hasSelection.value) { preview.value = null; return }
  previewLoading.value = true
  timer = setTimeout(async () => {
    try { preview.value = await store.previewExport(request.value) }
    catch (e: any) { ElMessage.warning(e?.response?.data?.detail || '范围预览失败') }
    finally { previewLoading.value = false }
  }, 300)
}, { deep: true })

async function onOpen() {
  await Promise.all([store.fetchRuns(), store.fetchStages()])
  // default: all stages, no runs preselected
  selectedStages.value = store.stages.map(s => s.id)
  limitTime.value = false
  dateRange.value = null
  preview.value = null
}

function onClosed() {
  selectedRuns.value = []
  runTable.value?.clearSelection?.()
}

function onSelectionChange(rows: RunSummary[]) { selectedRuns.value = rows }
function selectAllStages() { selectedStages.value = store.stages.map(s => s.id) }

function strategyText(s: string) {
  return { fifo: 'FIFO', priority: '优先级', max_concurrent: '最大并发' }[s] || s
}
function fmtTime(ts?: number) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function doExport() {
  exporting.value = true
  try {
    const rec = await store.createExport(request.value)
    if (rec.status === 'no_data') {
      ElMessage.info('所选范围内没有符合条件的数据，未生成文件')
      visible.value = false
      return
    }
    ElMessage.success(rec.reused ? '相同范围已导出过，直接使用已有文件' : '导出完成')
    // download to local
    const a = document.createElement('a')
    a.href = store.downloadExportUrl(rec.id)
    a.download = rec.fileName || ''
    document.body.appendChild(a); a.click(); a.remove()
    visible.value = false
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导出失败，可在导出记录中重试')
  } finally {
    exporting.value = false
  }
}

defineExpose({ visible })
</script>

<style scoped>
.export-form section { margin-bottom: 14px }
.sec-title { display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; font-weight: 600; color: #bb86fc; margin-bottom: 8px }
.hint { font-size: 11px; color: #888; font-weight: 400 }
.stage-group { display: flex; flex-wrap: wrap; gap: 4px 8px; max-height: 110px; overflow-y: auto }
.stage-group :deep(.el-checkbox) { margin-right: 0 }
.preview { min-height: 40px; display: flex; align-items: center }
.preview-stats { display: flex; gap: 8px; flex-wrap: wrap }
</style>
