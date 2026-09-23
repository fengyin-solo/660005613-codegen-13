<template>
  <el-dialog v-model="visible" title="导出记录" width="760px" @open="onOpen">
    <div class="records-toolbar">
      <span class="hint">记录持久保存，刷新页面后仍可查看与下载</span>
      <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
    </div>
    <el-table :data="store.exports" size="small" v-loading="loading"
              :row-key="(r: ExportRecord) => r.id"
              :expand-row-keys="expandedKeys" @expand-change="onExpandChange">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="detail-box" v-loading="detailLoading[row.id]">
            <template v-if="detailMap[row.id]">
              <div class="detail-scope">
                <div><b>执行：</b>#{{ detailMap[row.id].runIds.join('、#') }}</div>
                <div><b>环节：</b>{{ stageNames(detailMap[row.id].stages).join('、') }}</div>
                <div><b>时间范围：</b>{{ rangeText(detailMap[row.id]) }}</div>
              </div>
              <el-table :data="detailMap[row.id].detail?.runs || []" size="small" border>
                <el-table-column prop="id" label="执行 #" width="80" />
                <el-table-column prop="name" label="名称" />
                <el-table-column prop="nodeCount" label="环节明细" width="90" />
                <el-table-column prop="logCount" label="日志" width="80" />
                <el-table-column prop="breakerCount" label="熔断状态" width="90" />
              </el-table>
              <div class="detail-totals">
                合计：{{ detailMap[row.id].detail?.nodeCount }} 条环节明细 /
                {{ detailMap[row.id].detail?.logCount }} 条日志 /
                {{ detailMap[row.id].detail?.breakerCount }} 条熔断状态
                <span v-if="countsMismatch(detailMap[row.id])" class="mismatch">
                  （与文件不一致，数据可能已变化，建议重新导出）
                </span>
              </div>
            </template>
            <div v-else-if="!detailLoading[row.id]" class="hint">暂无明细</div>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="id" label="#" width="50" />
      <el-table-column label="导出范围" min-width="220">
        <template #default="{ row }">
          <div class="scope-cell">
            <span>#{{ row.runIds.join('、#') }} · {{ row.stages.length }} 个环节</span>
            <span class="sub">{{ rangeText(row) }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="文件大小" width="100">
        <template #default="{ row }">{{ row.status === 'success' ? fmtSize(row.fileSize) : '—' }}</template>
      </el-table-column>
      <el-table-column label="结果" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="statusTag(row.status)">{{ statusText(row.status) }}</el-tag>
          <div v-if="row.status === 'failed'" class="err-text">{{ row.error }}</div>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="135">
        <template #default="{ row }">{{ fmtTime(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="170">
        <template #default="{ row }">
          <el-button link size="small" type="primary"
                     :href="store.downloadExportUrl(row.id)"
                     :disabled="row.status !== 'success'">下载</el-button>
          <el-button link size="small" type="warning" v-if="row.status === 'failed'"
                     @click="retry(row)">重新导出</el-button>
          <span v-else class="hint" style="margin-left:8px">
            {{ row.status === 'no_data' ? '无数据' : row.status === 'success' ? '已完成' : '进行中' }}
          </span>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="!loading && !store.exports.length" class="empty">暂无导出记录</div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { useDAGStore } from '../store/dag'
import type { ExportRecord } from '@/types'

const store = useDAGStore()
const visible = ref(false)
const loading = ref(false)
const expandedKeys = ref<number[]>([])
const detailMap = reactive<Record<number, ExportRecord>>({})
const detailLoading = reactive<Record<number, boolean>>({})

async function onOpen() { await refresh() }
async function refresh() {
  loading.value = true
  try { await store.fetchExports() } finally { loading.value = false }
}

async function onExpandChange(row: ExportRecord, expanded: ExportRecord[] | boolean) {
  const isOpen = Array.isArray(expanded) ? expanded.some(r => r.id === row.id) : !!expanded
  expandedKeys.value = isOpen ? [row.id] : []
  if (isOpen && !detailMap[row.id]) await loadDetail(row.id)
}

async function loadDetail(id: number) {
  detailLoading[id] = true
  try { detailMap[id] = await store.fetchExportDetail(id) }
  finally { detailLoading[id] = false }
}

async function retry(row: ExportRecord) {
  try {
    const rec = await store.retryExport(row.id)
    if (rec.status === 'no_data') {
      ElMessage.info('该范围内仍没有符合条件的数据，未生成文件')
    } else if (rec.status === 'success') {
      ElMessage.success('重新导出完成，开始下载')
      const a = document.createElement('a')
      a.href = store.downloadExportUrl(rec.id); a.download = rec.fileName || ''
      document.body.appendChild(a); a.click(); a.remove()
    }
    await loadDetail(row.id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '重新导出失败')
  }
}

function stageNames(ids: string[]) {
  const map = new Map(store.stages.map(s => [s.id, s.name]))
  return ids.map(i => map.get(i) || i)
}
function rangeText(row: ExportRecord) {
  if (!row.timeStart && !row.timeEnd) return '全部时间'
  const s = row.timeStart ? fmtTime(row.timeStart) : '最早'
  const e = row.timeEnd ? fmtTime(row.timeEnd) : '现在'
  return `${s} 至 ${e}`
}
function countsMismatch(rec: ExportRecord) {
  const d = rec.detail
  return d && (d.nodeCount !== rec.nodeCount || d.logCount !== rec.logCount ||
               d.breakerCount !== rec.breakerCount)
}
function statusText(s: string) {
  return ({ success: '成功', failed: '失败', no_data: '无符合数据', running: '导出中' } as Record<string, string>)[s] || s
}
function statusTag(s: string): any {
  return ({ success: 'success', failed: 'danger', no_data: 'info', running: 'warning' } as Record<string, any>)[s]
}
function fmtSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}
function fmtTime(ts?: number) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

defineExpose({ visible })
</script>

<style scoped>
.records-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px }
.hint { font-size: 11px; color: #888 }
.scope-cell { display: flex; flex-direction: column; gap: 2px }
.scope-cell .sub { font-size: 10px; color: #888 }
.err-text { font-size: 10px; color: #e53e3e; max-width: 100px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap }
.detail-box { padding: 8px 16px; background: rgba(255,255,255,0.02) }
.detail-scope { display: flex; flex-direction: column; gap: 4px; font-size: 12px;
  margin-bottom: 8px; color: #ccc }
.detail-totals { margin-top: 8px; font-size: 12px; color: #bb86fc }
.mismatch { color: #d69e2e }
.empty { text-align: center; color: #4a5568; padding: 20px; font-size: 12px }
</style>
