import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'
import type { DAGWorkflow, ExecutionInfo, RunSummary, StageInfo, ExportRecord, ExportPreview, ExportRequest } from '@/types'
export const useDAGStore = defineStore('dag', () => {
  const loading = ref(false)
  const workflow = ref<DAGWorkflow | null>(null)
  const execution = ref<ExecutionInfo | null>(null)
  const wsConnected = ref(false)
  const workers = ref(3)
  const strategy = ref('fifo')

  const runs = ref<RunSummary[]>([])
  const stages = ref<StageInfo[]>([])
  const exports = ref<ExportRecord[]>([])

  let ws: WebSocket|null = null
  function connectWS() {
    ws = new WebSocket(`ws://${location.hostname}:8000/ws`)
    ws.onopen = () => { wsConnected.value = true }
    ws.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data); execution.value = d
        if (d.completed) fetchRuns()
      }
      catch {}
    }
  }

  async function createWorkflow(name: string) {
    loading.value = true
    try { const { data } = await axios.post('/api/workflow', { name }) ; workflow.value = data }
    finally { loading.value = false }
  }

  async function run() {
    if (!workflow.value) return
    loading.value = true
    try {
      const { data } = await axios.post('/api/run', { workflowId: workflow.value.id, workers: workers.value, strategy: strategy.value })
      execution.value = data
      await fetchRuns()
    }
    finally { loading.value = false }
  }

  async function fetchRuns() {
    const { data } = await axios.get<RunSummary[]>('/api/runs')
    runs.value = data
  }

  async function fetchStages() {
    if (stages.value.length) return
    const { data } = await axios.get<StageInfo[]>('/api/stages')
    stages.value = data
  }

  async function fetchExports() {
    const { data } = await axios.get<ExportRecord[]>('/api/exports')
    exports.value = data
  }

  async function previewExport(req: ExportRequest): Promise<ExportPreview> {
    const { data } = await axios.post<ExportPreview>('/api/exports/preview', req)
    return data
  }

  async function createExport(req: ExportRequest): Promise<ExportRecord & { reused?: boolean }> {
    const { data } = await axios.post('/api/exports', req)
    await fetchExports()
    return data
  }

  async function fetchExportDetail(id: number): Promise<ExportRecord> {
    const { data } = await axios.get<ExportRecord>(`/api/exports/${id}`)
    return data
  }

  async function retryExport(id: number): Promise<ExportRecord> {
    const { data } = await axios.post<ExportRecord>(`/api/exports/${id}/retry`)
    await fetchExports()
    return data
  }

  function downloadExportUrl(id: number): string {
    return `/api/exports/${id}/download`
  }

  function disconnectWS() { ws?.close(); ws = null }
  return {
    loading, workflow, execution, wsConnected, workers, strategy,
    runs, stages, exports,
    connectWS, createWorkflow, run, disconnectWS,
    fetchRuns, fetchStages, fetchExports, previewExport, createExport,
    fetchExportDetail, retryExport, downloadExportUrl,
  }
})
