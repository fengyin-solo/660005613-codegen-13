import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'
import type { DAGWorkflow, ExecutionInfo, RunDetail } from '@/types'
export const useDAGStore = defineStore('dag', () => {
  const loading = ref(false)
  const workflow = ref<DAGWorkflow | null>(null)
  const execution = ref<ExecutionInfo | null>(null)
  const currentRunId = ref<number | null>(null)
  const wsConnected = ref(false)
  const workers = ref(3)
  const strategy = ref('fifo')

  let ws: WebSocket|null = null
  function connectWS() {
    ws = new WebSocket(`ws://${location.hostname}:8000/ws`)
    ws.onopen = () => { wsConnected.value = true }
    ws.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data) as ExecutionInfo
        execution.value = d
        if (d.runId) currentRunId.value = d.runId
      }
      catch {}
    }
    ws.onclose = () => { wsConnected.value = false }
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
      const { data } = await axios.post('/api/run', {
        workflowId: workflow.value.id, workers: workers.value, strategy: strategy.value,
      })
      currentRunId.value = data.runId
      execution.value = data
    }
    finally { loading.value = false }
  }

  /** Load a persisted run into the canvas/panels — used after refresh and
   *  when opening a historical run from the history list. */
  async function loadRunDetail(runId: number) {
    const { data } = await axios.get<RunDetail>(`/api/runs/${runId}`)
    currentRunId.value = data.id
    execution.value = {
      runId: data.id,
      workflow: {
        id: data.workflowId, name: data.name, nodes: data.nodes, edges: data.edges,
      },
      logs: data.logs,
      circuitBreakers: data.circuitBreakers,
      completed: data.completed,
      status: data.status,
    }
  }

  function disconnectWS() { ws?.close(); ws = null }
  return { loading, workflow, execution, currentRunId, wsConnected, workers, strategy,
    connectWS, createWorkflow, run, loadRunDetail, disconnectWS }
})
