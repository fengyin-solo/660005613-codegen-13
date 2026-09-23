<template>
  <el-dialog v-model="visible" title="🕓 执行历史" width="780px"
             class="history-dialog" @open="onOpen">
    <div class="bar">
      <span class="hint">刷新页面后，已完成的执行记录仍可在此查看。</span>
      <el-button size="small" :loading="store.runsLoading" @click="store.loadRuns()">刷新</el-button>
    </div>
    <el-table :data="store.runs" v-loading="store.runsLoading" size="small"
              empty-text="暂无执行记录" height="420"
              @selection-change="(rows: RunSummary[]) => selected = rows"
              :row-key="(r: RunSummary) => r.id" ref="tableRef">
      <el-table-column type="selection" width="42" />
      <el-table-column label="#" width="50">
        <template #default="{ row }">#{{ row.id }}</template>
      </el-table-column>
      <el-table-column prop="name" label="工作流" width="110" />
      <el-table-column label="状态" width="82">
        <template #default="{ row }">
          <el-tag size="small" :type="runStatusType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="开始 / 结束" min-width="170">
        <template #default="{ row }">
          <div class="time">{{ formatTime(row.startTs) }}</div>
          <div class="time sub">至 {{ formatTime(row.endTs) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="任务" width="130">
        <template #default="{ row }">
          <span class="ok">✓{{ row.taskSuccess }}</span>
          <span class="fail">✗{{ row.taskFailed }}</span>
          <span v-if="row.taskSkipped" class="skip">⊘{{ row.taskSkipped }}</span>
          <span v-if="row.taskRunning" class="run">…{{ row.taskRunning }}</span>
        </template>
      </el-table-column>
      <el-table-column label="日志" prop="logCount" width="56" />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="viewRun(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button size="small" @click="visible = false">关闭</el-button>
      <el-button type="primary" size="small" :disabled="!selected.length"
                 @click="exportSelected">导出选中 {{ selected.length ? `(${selected.length})` : '' }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useExportStore } from '@/store/export'
import { useDAGStore } from '@/store/dag'
import type { RunSummary } from '@/types'
import { formatTime, runStatusType } from '@/utils/format'

const emit = defineEmits<{ (e: 'export-runs', runIds: number[]): void }>()

const store = useExportStore()
const dag = useDAGStore()
const visible = ref(false)
const selected = ref<RunSummary[]>([])
const tableRef = ref()

async function onOpen() {
  await store.loadRuns()
}

async function viewRun(row: RunSummary) {
  await dag.loadRunDetail(row.id)
  ElMessage.success(`已载入执行 #${row.id} 的明细`)
  visible.value = false
}

function exportSelected() {
  const ids = selected.value.map(r => r.id)
  visible.value = false
  emit('export-runs', ids)
}

defineExpose({ open: () => { visible.value = true } })
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px }
.hint { color: #8888aa; font-size: 11px }
.time { font-size: 11px; color: #c0c0d8 }
.time.sub { color: #7a7a9a }
.ok { color: #38a169; margin-right: 8px }
.fail { color: #ef4444; margin-right: 8px }
.skip { color: #8888aa; margin-right: 8px }
.run { color: #3182ce }
:deep(.el-table), :deep(.el-table tr), :deep(.el-table th.el-table__cell) {
  background-color: #1a1a2e; color: #d0d0e4;
}
:deep(.el-table td.el-table__cell), :deep(.el-table th.el-table__cell.is-leaf) {
  border-bottom: 1px solid #23234a;
}
:deep(.el-table--enable-row-hover .el-table__body tr:hover>td.el-table__cell) {
  background-color: #23234a;
}
</style>
