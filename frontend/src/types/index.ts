export interface TaskNode { id: string; name: string; deps: string[]; x: number; y: number; status: string; startTime?: number; endTime?: number; retries: number }
export interface DAGWorkflow { id: number; name: string; nodes: TaskNode[]; edges: [string,string][] }
export interface ExecutionLog { taskId: string; status: string; timestamp: number; message: string }
export interface CircuitBreaker { taskId: string; failureCount: number; state: string; cooldownUntil: number }
export interface ExecutionInfo { runId?: number; workflow: DAGWorkflow; logs: ExecutionLog[]; circuitBreakers: CircuitBreaker[]; completed: boolean; status?: string }

export type ExportSection = 'workflow' | 'tasks' | 'logs' | 'breakers'

export interface RunSummary {
  id: number
  workflowId: number
  name: string
  workers: number
  strategy: string
  status: string
  startTs: number
  endTs?: number | null
  completed: boolean
  logCount: number
  taskTotal: number
  taskSuccess: number
  taskFailed: number
  taskRunning: number
  taskPending: number
  taskSkipped: number
}

export interface RunDetail extends RunSummary {
  nodes: TaskNode[]
  edges: [string, string][]
  logs: ExecutionLog[]
  circuitBreakers: CircuitBreaker[]
}

export interface ExportCounts {
  runCount: number
  workflowCount: number
  taskCount: number
  logCount: number
  breakerCount: number
}

export interface ExportRecord {
  id: number
  status: 'PROCESSING' | 'SUCCESS' | 'FAILED' | 'EMPTY'
  runIds: number[]
  sections: ExportSection[]
  startTs?: number | null
  endTs?: number | null
  filename?: string | null
  sizeBytes?: number | null
  counts?: ExportCounts | null
  error?: string | null
  duplicateOf?: number | null
  createdAt: number
  finishedAt?: number | null
}

export interface ExportPreviewPerRun {
  runId: number
  name: string
  status: string
  counts: { workflow: number; tasks: number; logs: number; breakers: number }
}

export interface ExportPreview {
  counts: ExportCounts
  hasData: boolean
  runs: ExportPreviewPerRun[]
}
