export interface TaskNode { id: string; name: string; deps: string[]; x: number; y: number; status: string; startTime?: number; endTime?: number; retries: number }
export interface DAGWorkflow { id: number; name: string; nodes: TaskNode[]; edges: [string,string][] }
export interface ExecutionLog { taskId: string; status: string; timestamp: number; message: string }
export interface CircuitBreaker { taskId: string; failureCount: number; state: string; cooldownUntil: number }
export interface ExecutionInfo { runId?: number; workflow: DAGWorkflow; logs: ExecutionLog[]; circuitBreakers: CircuitBreaker[]; completed: boolean }

export interface StageInfo { id: string; name: string }

export interface RunSummary {
  id: number
  workflowId: number
  name: string
  workers: number
  strategy: string
  status: string
  createdAt: number
  finishedAt?: number
  startedAt?: number
  endedAt?: number
  nodeCount: number
  logCount: number
}

export interface RunDetail extends RunSummary {
  nodes: TaskNode[]
  edges: [string, string][]
  logs: ExecutionLog[]
  circuitBreakers: CircuitBreaker[]
}

export type ExportStatus = 'running' | 'success' | 'failed' | 'no_data'

export interface ExportRecord {
  id: number
  scopeHash: string
  runIds: number[]
  stages: string[]
  timeStart?: number
  timeEnd?: number
  status: ExportStatus
  filePath?: string
  fileName?: string
  fileSize: number
  nodeCount: number
  logCount: number
  breakerCount: number
  error?: string
  createdAt: number
  finishedAt?: number
  detail?: ExportPreview
}

export interface ExportPreviewRun {
  id: number
  name: string
  nodeCount: number
  logCount: number
  breakerCount: number
}

export interface ExportPreview {
  runCount: number
  nodeCount: number
  logCount: number
  breakerCount: number
  empty: boolean
  runs: ExportPreviewRun[]
}

export interface ExportRequest {
  runIds: number[]
  stages: string[]
  timeStart?: number
  timeEnd?: number
}
