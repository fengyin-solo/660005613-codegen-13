<template>
  <el-dialog v-model="visible" title="🗂 导出记录" width="880px"
             class="records-dialog" @open="onOpen">
    <el-table :data="store.exportsList" v-loading="store.exportsLoading" size="small"
              empty-text="暂无导出记录" max-height="440">
      <el-table-column label="#" width="50">
        <template #default="{ row }">#{{ row.id }}</template>
      </el-table-column>
      <el-table-column label="导出范围" min-width="280">
        <template #default="{ row }">
          <div class="scope-line">
            执行 <b>#{{ row.runIds.join(', #') }}</b>
          </div>
          <div class="scope-line sub">{{ scopeText(row.sections, row.startTs, row.endTs) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="结果" width="160">
        <template #default="{ row }">
          <el-tag size="small" :type="exportStatusType(row.status)">
            {{ EXPORT_STATUS_TEXT[row.status] }}
          </el-tag>
          <div v-if="row.status === 'SUCCESS' && row.counts" class="counts">
            {{ row.counts.runCount }}次 / 任务{{ row.counts.taskCount }} / 日志{{ row.counts.logCount }}
          </div>
          <div v-else-if="row.status === 'EMPTY'" class="counts warn">无符合条件数据，未生成文件</div>
          <div v-else-if="row.status === 'FAILED'" class="counts err" :title="row.error || ''">
            {{ row.error || '导出失败' }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="84">
        <template #default="{ row }">{{ formatSize(row.sizeBytes) }}</template>
      </el-table-column>
      <el-table-column label="时间" width="140">
        <template #default="{ row }">
          <div class="time">{{ formatTime(row.createdAt) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="showDetail(row)"
                     :disabled="row.status !== 'SUCCESS'">明细</el-button>
          <el-button link type="success" size="small" @click="store.download(row.id)"
                     :disabled="row.status !== 'SUCCESS'">下载</el-button>
          <el-button link type="warning" size="small" @click="retry(row)"
                     v-if="row.status === 'FAILED' || row.status === 'EMPTY'">重新开始</el-button>
          <el-tag v-if="row.status === 'PROCESSING'" size="small" type="warning"
                  effect="plain" class="running-tag">进行中…</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <!-- Detail: numbers are rendered straight from the written export file, so
         this page and the record row / downloaded file share one 口径. -->
    <el-dialog v-model="detailVisible" title="导出明细" width="720px" append-to-body
               class="detail-dialog">
      <div v-if="detail" v-loading="detailLoading">
        <div class="detail-head">
          <el-tag size="small" :type="exportStatusType(detail.record.status)">
            {{ EXPORT_STATUS_TEXT[detail.record.status] }}
          </el-tag>
          <span>{{ formatSize(detail.record.sizeBytes) }}</span>
          <span class="sub">{{ scopeText(detail.record.sections,
                                          detail.record.startTs, detail.record.endTs) }}</span>
        </div>
        <div class="detail-totals" v-if="detail.content">
          <div class="total-card"><b>{{ detail.content.counts.runCount }}</b><span>执行</span></div>
          <div class="total-card"><b>{{ detail.content.counts.workflowCount }}</b><span>工作流定义</span></div>
          <div class="total-card"><b>{{ detail.content.counts.taskCount }}</b><span>任务结果</span></div>
          <div class="total-card"><b>{{ detail.content.counts.logCount }}</b><span>日志</span></div>
          <div class="total-card"><b>{{ detail.content.counts.breakerCount }}</b><span>熔断器</span></div>
        </div>
        <el-collapse v-if="detail.content" class="run-collapse">
          <el-collapse-item v-for="r in detail.content.runs" :key="r.runId"
            :name="r.runId">
            <template #title>
              <span class="run-title">
                #{{ r.runId }} {{ r.name }}
                <el-tag size="small" :type="runStatusType(r.status)" effect="plain">
                  {{ r.status }}
                </el-tag>
                <span class="sub">
                  {{ r.workflow ? `节点${r.workflow.nodes.length}/边${r.workflow.edges.length}` : '' }}
                  {{ 'tasks' in r ? `· 任务${r.tasks.length}` : '' }}
                  {{ 'logs' in r ? `· 日志${r.logs.length}` : '' }}
                  {{ 'breakers' in r ? `· 熔断${r.breakers.length}` : '' }}
                </span>
              </span>
            </template>
            <pre v-if="JSONPre(r)">{{ JSONPre(r) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-dialog>

    <template #footer>
      <el-button size="small" @click="visible = false">关闭</el-button>
      <el-button size="small" type="primary" @click="refresh">刷新</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useExportStore } from '@/store/export'
import type { ExportRecord } from '@/types'
import { formatTime, formatSize, runStatusType, exportStatusType,
  EXPORT_STATUS_TEXT, scopeText } from '@/utils/format'

const store = useExportStore()
const visible = ref(false)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<{ record: ExportRecord; content: any } | null>(null)

async function onOpen() {
  await refresh()
  // Keep polling while anything is processing, so a refresh of this dialog
  // (or of the page) still tracks the job to completion. The poller stops
  // itself once no job is PROCESSING; it is not tied to the dialog lifecycle.
  store.startPolling(false)
}

async function refresh() {
  await store.loadExports()
  if (store.exportsList.some(e => e.status === 'PROCESSING')) {
    store.startPolling(false)
  }
}

async function showDetail(row: ExportRecord) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await store.getExportDetail(row.id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载明细失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function retry(row: ExportRecord) {
  try {
    await store.retryExport(row.id)
    ElMessage.success(`导出 #${row.id} 已重新开始`)
    store.startPolling(false)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '重新开始失败')
  }
}

function JSONPre(run: any): string {
  return JSON.stringify(run, null, 2)
}

defineExpose({ open: () => { visible.value = true } })
</script>

<style scoped>
.scope-line { font-size: 12px; color: #d0d0e4 }
.scope-line.sub { color: #8888aa; font-size: 11px; margin-top: 2px }
.counts { font-size: 11px; color: #a0a0c0; margin-top: 3px }
.counts.warn { color: #fbbf24 }
.counts.err { color: #ef4444; max-width: 150px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap }
.time { font-size: 11px; color: #8888aa }
.running-tag { margin-left: 6px }
.detail-head { display: flex; gap: 10px; align-items: center;
  font-size: 12px; color: #c0c0d8; margin-bottom: 12px }
.detail-head .sub { color: #8888aa; font-size: 11px }
.detail-totals { display: flex; gap: 8px; margin-bottom: 14px }
.total-card { flex: 1; background: #14142b; border: 1px solid #2a2a4a;
  border-radius: 6px; padding: 10px; text-align: center }
.total-card b { display: block; font-size: 18px; color: #bb86fc }
.total-card span { font-size: 11px; color: #8888aa }
.run-title { display: inline-flex; align-items: center; gap: 8px;
  color: #d0d0e4; font-size: 12px }
.run-title .sub { color: #8888aa; font-size: 11px }
:deep(.run-collapse pre) { font-size: 10px; color: #b0b0cc;
  background: #14142b; padding: 8px; border-radius: 4px;
  max-height: 300px; overflow: auto; margin: 0 }
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
