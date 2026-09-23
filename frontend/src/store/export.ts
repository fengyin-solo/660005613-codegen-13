import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'
import type {
  RunSummary, RunDetail, ExportRecord, ExportPreview, ExportSection,
} from '@/types'

interface CreateExportResult { reused: boolean; record: ExportRecord }

export const useExportStore = defineStore('export', () => {
  const runs = ref<RunSummary[]>([])
  const exportsList = ref<ExportRecord[]>([])
  const runsLoading = ref(false)
  const exportsLoading = ref(false)
  const creating = ref(false)
  let pollTimer: number | null = null

  async function loadRuns() {
    runsLoading.value = true
    try {
      const { data } = await axios.get<RunSummary[]>('/api/runs')
      runs.value = data
    } finally {
      runsLoading.value = false
    }
  }

  async function getRun(id: number) {
    const { data } = await axios.get<RunDetail>(`/api/runs/${id}`)
    return data
  }

  async function preview(body: {
    runIds: number[]; sections: ExportSection[]
    startTs?: number | null; endTs?: number | null
  }): Promise<ExportPreview> {
    const { data } = await axios.post<ExportPreview>('/api/export-preview', body)
    return data
  }

  async function createExport(body: {
    runIds: number[]; sections: ExportSection[]
    startTs?: number | null; endTs?: number | null
  }): Promise<CreateExportResult> {
    creating.value = true
    try {
      const { data } = await axios.post<CreateExportResult>('/api/exports', body)
      await loadExports()
      return data
    } finally {
      creating.value = false
    }
  }

  async function loadExports() {
    exportsLoading.value = true
    try {
      const { data } = await axios.get<ExportRecord[]>('/api/exports')
      exportsList.value = data
    } finally {
      exportsLoading.value = false
    }
  }

  async function getExportDetail(id: number) {
    const { data } = await axios.get<{ record: ExportRecord; content: any }>(
      `/api/exports/${id}`)
    return data
  }

  async function retryExport(id: number): Promise<ExportRecord> {
    const { data } = await axios.post<{ record: ExportRecord }>(
      `/api/exports/${id}/retry`)
    await loadExports()
    return data.record
  }

  function downloadUrl(id: number) {
    return `/api/exports/${id}/download`
  }

  function download(id: number) {
    // Plain anchor navigation goes through the vite proxy and streams the file
    // straight to disk; the server only serves a real completed file.
    const a = document.createElement('a')
    a.href = downloadUrl(id)
    a.download = ''
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  /** Keep refreshing while any export is still processing. */
  function startPolling(immediate = true) {
    stopPolling()
    if (immediate) loadExports()
    pollTimer = window.setInterval(async () => {
      await loadExports()
      if (!exportsList.value.some(e => e.status === 'PROCESSING')) {
        stopPolling()
      }
    }, 1500)
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return {
    runs, exportsList, runsLoading, exportsLoading, creating,
    loadRuns, getRun, preview, createExport, loadExports,
    getExportDetail, retryExport, download, downloadUrl,
    startPolling, stopPolling,
  }
})
