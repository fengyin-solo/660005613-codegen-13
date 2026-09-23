import type { ExportSection } from '@/types'

export const SECTION_LABELS: Record<ExportSection, string> = {
  workflow: '工作流定义',
  tasks: '任务执行结果',
  logs: '执行日志',
  breakers: '熔断器状态',
}

export const SECTION_ORDER: ExportSection[] = ['workflow', 'tasks', 'logs', 'breakers']

export function formatTime(ts?: number | null): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function formatSize(bytes?: number | null): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export function runStatusType(status: string): '' | 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'SUCCESS': return 'success'
    case 'FAILED': return 'danger'
    case 'RUNNING': return 'warning'
    default: return 'info'
  }
}

export function exportStatusType(status: string): '' | 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'SUCCESS': return 'success'
    case 'PROCESSING': return 'warning'
    case 'FAILED': return 'danger'
    case 'EMPTY': return 'info'
    default: return 'info'
  }
}

export const EXPORT_STATUS_TEXT: Record<string, string> = {
  PROCESSING: '导出中',
  SUCCESS: '已完成',
  FAILED: '失败',
  EMPTY: '无数据',
}

export function scopeText(sections: ExportSection[],
                          startTs?: number | null, endTs?: number | null): string {
  const parts = sections.map(s => SECTION_LABELS[s]).join('、')
  const range = startTs || endTs
    ? `时间 ${formatTime(startTs)} ~ ${formatTime(endTs)}`
    : '时间范围：全部'
  return `${parts}｜${range}`
}
