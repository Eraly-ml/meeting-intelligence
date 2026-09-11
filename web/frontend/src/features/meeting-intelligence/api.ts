export type JobStage = 'queued' | 'preprocessing' | 'transcribing' | 'diarizing' | 'extracting' | 'validating' | 'exporting' | 'completed' | 'failed' | 'cancelled'

export interface JobRecord {
  id: string
  meeting_id: string
  stage: JobStage
  error_message?: string
  created_at: string
  updated_at: string
}

export interface Evidence {
  segment_ids: string[]
  quote?: string
  speaker?: string
  start?: number
  end?: number
}

export interface ProtocolItem {
  id: string
  text: string
  evidence: Evidence
  source_check: 'passed' | 'failed' | 'unavailable'
  review_status: string
}

export interface MeetingResult {
  job: JobRecord
  protocol: {
    metadata: { title: string; language: string; participants: string[] }
    executive_summary: string[]
    decisions: ProtocolItem[]
    open_questions: ProtocolItem[]
    risks: ProtocolItem[]
    action_items: Array<ProtocolItem & {
      task: string
      assignee?: string
      deadline_text?: string
      priority: string
    }>
  }
  transcript: { raw_text: string }
  exports: Record<string, string>
}

export interface WorkerConfig { url: string; token: string }

function headers(config: WorkerConfig): HeadersInit {
  return config.token ? { Authorization: `Bearer ${config.token}` } : {}
}

async function checked<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${response.status}: ${body}`)
  }
  return response.json() as Promise<T>
}

export async function health(config: WorkerConfig): Promise<{ status: string; version: string }> {
  return checked(await fetch(`${config.url}/health`))
}

export async function submitMeeting(
  config: WorkerConfig,
  input: { title: string; languageMode: string; outputLanguage: string; file?: File; transcript?: string },
): Promise<JobRecord> {
  const form = new FormData()
  form.set('manifest_json', JSON.stringify({
    meeting_id: crypto.randomUUID(),
    title: input.title || 'Meeting',
    language_mode: input.languageMode,
    output_language: input.outputLanguage,
  }))
  if (input.file) form.set('audio', input.file)
  else form.set('transcript', input.transcript || '')
  return checked(await fetch(`${config.url}/v1/jobs`, {
    method: 'POST', headers: { ...headers(config), 'Idempotency-Key': crypto.randomUUID() }, body: form,
  }))
}

export async function getJob(config: WorkerConfig, id: string): Promise<JobRecord> {
  return checked(await fetch(`${config.url}/v1/jobs/${id}`, { headers: headers(config) }))
}

export async function getResult(config: WorkerConfig, id: string): Promise<MeetingResult> {
  return checked(await fetch(`${config.url}/v1/jobs/${id}/result`, { headers: headers(config) }))
}

export async function downloadExport(config: WorkerConfig, id: string, format: string): Promise<void> {
  const response = await fetch(`${config.url}/v1/jobs/${id}/export/${format}`, { headers: headers(config) })
  if (!response.ok) throw new Error(`Export failed: ${response.status}`)
  const blob = await response.blob()
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `meeting-${id}.${format === 'json' ? 'json' : format === 'csv' ? 'csv' : 'pdf'}`
  link.click()
  URL.revokeObjectURL(link.href)
}
