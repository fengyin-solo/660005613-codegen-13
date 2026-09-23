<template>
  <div class="app-root">
    <header class="top-bar">
      <h1>🔀 分布式任务工作流DAG编排与执行引擎</h1>
      <div class="tools">
        <el-input v-model="wfName" placeholder="工作流名称" size="small" style="width:160px"/>
        <el-button size="small" @click="create" :loading="store.loading">创建DAG</el-button>
        <el-select v-model="store.workers" size="small" style="width:100px">
          <el-option :value="1" label="1 Worker"/><el-option :value="3" label="3 Workers"/><el-option :value="5" label="5 Workers"/>
        </el-select>
        <el-select v-model="store.strategy" size="small" style="width:100px">
          <el-option value="fifo" label="FIFO"/><el-option value="priority" label="优先级"/><el-option value="max_concurrent" label="最大并发"/>
        </el-select>
        <el-button type="success" size="small" @click="run" :disabled="!store.workflow" :loading="store.loading">▶ 执行</el-button>
        <el-button size="small" @click="exportCurrent" :disabled="!store.currentRunId">📤 导出本次</el-button>
        <el-button size="small" @click="openHistory">🕓 执行历史</el-button>
        <el-button size="small" @click="openRecords" type="primary" plain>🗂 导出记录</el-button>
        <span class="ws-dot" :class="{on:store.wsConnected}"></span>
      </div>
    </header>
    <div class="main-grid">
      <div class="dag-area">
        <DAGCanvas />
      </div>
      <div class="side-area">
        <LogPanel />
        <CircuitBreakerPanel />
      </div>
    </div>

    <ExportDialog ref="exportDialog" @done="onExportDone" />
    <ExportRecordsDialog ref="recordsDialog" />
    <RunHistoryDialog ref="historyDialog" @export-runs="(ids) => exportDialog?.openPreset(ids)" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import DAGCanvas from './components/DAGCanvas.vue'
import LogPanel from './components/LogPanel.vue'
import CircuitBreakerPanel from './components/CircuitBreakerPanel.vue'
import ExportDialog from './components/ExportDialog.vue'
import ExportRecordsDialog from './components/ExportRecordsDialog.vue'
import RunHistoryDialog from './components/RunHistoryDialog.vue'
import { useDAGStore } from './store/dag'
import { useExportStore } from './store/export'
import type { ExportRecord } from './types'
const store = useDAGStore()
const exportStore = useExportStore()
const wfName = ref('data-pipeline')
const exportDialog = ref<InstanceType<typeof ExportDialog>>()
const recordsDialog = ref<InstanceType<typeof ExportRecordsDialog>>()
const historyDialog = ref<InstanceType<typeof RunHistoryDialog>>()
function create() { store.createWorkflow(wfName.value) }
function run() { store.run() }
function exportCurrent() {
  if (store.currentRunId) exportDialog.value?.openPreset([store.currentRunId])
}
function openHistory() { historyDialog.value?.open() }
function openRecords() { recordsDialog.value?.open() }
function onExportDone(_record: ExportRecord) {
  // New/restarted jobs: keep the records list tracking to completion even
  // without opening the records dialog.
  exportStore.loadExports().then(() => {
    if (exportStore.exportsList.some(e => e.status === 'PROCESSING')) {
      exportStore.startPolling(false)
    }
  })
}
onMounted(() => {
  store.connectWS()
  // Refresh recovery: any export still processing on the server is tracked;
  // records themselves come from SQLite, so they remain viewable regardless.
  exportStore.loadExports().then(() => {
    if (exportStore.exportsList.some(e => e.status === 'PROCESSING')) {
      exportStore.startPolling(false)
    }
  })
})
onUnmounted(() => { store.disconnectWS(); exportStore.stopPolling() })
</script>

<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0c0c1d;color:#e0e0e0}
.app-root{height:100vh;display:flex;flex-direction:column}
.top-bar{display:flex;justify-content:space-between;align-items:center;padding:10px 20px;background:#1a1a2e;border-bottom:1px solid #2a2a4a}
.top-bar h1{font-size:1rem;color:#bb86fc}
.tools{display:flex;gap:6px;align-items:center}
.ws-dot{width:8px;height:8px;border-radius:50%;background:#ef4444}.ws-dot.on{background:#22c55e}
.main-grid{display:grid;grid-template-columns:1fr 320px;flex:1;overflow:hidden}
.dag-area{background:#0f0f23;position:relative;overflow:hidden}
.side-area{display:flex;flex-direction:column;gap:8px;padding:8px;overflow-y:auto;background:#14142b}
</style>
