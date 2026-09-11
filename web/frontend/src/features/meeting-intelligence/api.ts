// The Radxa owns this archive and forwards inference to its configured Mac.
// Never send the station token to a browser-configurable host.
const base = '/api/meeting-worker'

export type JobStage = 'recording' | 'queued' | 'preprocessing' | 'transcribing' | 'diarizing' | 'extracting' | 'validating' | 'exporting' | 'completed' | 'failed' | 'cancelled'
export interface JobManifest {
  meeting_id: string
  title: string
  language_mode: string
  output_language: string
  diarization?: boolean
}
export interface JobRecord {
  id: string
  meeting_id: string
  stage: JobStage
  manifest: JobManifest
  source_kind: 'audio' | 'text'
  error_code?: string | null
  error_message?: string | null
  created_at: string
  updated_at: string
  station?: { archived: boolean; source_bytes: number; filename: string; worker_submitted: boolean }
}
export interface Evidence {
  segment_ids: string[]
  quote?: string | null
  speaker?: string | null
  start?: number | null
  end?: number | null
}
export interface SourcedItem {
  id: string
  evidence: Evidence
  source_check: 'passed' | 'failed' | 'unavailable'
  review_status: string
}
export interface ProtocolItem extends SourcedItem { text: string }
export interface TranscriptSegment {
  id: string
  start?: number | null
  end?: number | null
  text: string
  speaker?: string | null
  language?: string | null
}
export interface MeetingResult {
  job: JobRecord
  protocol: {
    metadata: { title: string; language: string; participants: string[]; duration_seconds?: number | null }
    executive_summary: string[]
    executive_summary_sources?: Array<{ item_id: string; evidence: Evidence }>
    topics: Array<ProtocolItem & { title: string }>
    decisions: ProtocolItem[]
    open_questions: ProtocolItem[]
    risks: ProtocolItem[]
    action_items: Array<SourcedItem & { task: string; assignee?: string | null; deadline_text?: string | null; priority: string }>
  }
  transcript: { raw_text: string; segments: TranscriptSegment[]; language: string; model: string }
  exports: Record<string, string>
}
export interface Capabilities {
  offline?: boolean
  diarization?: boolean
  ollama_model?: string
  readiness?: Record<string, { ready: boolean; detail?: string }>
  selected_profiles?: { kk_ru: string; en: string }
  semantic_verification?: boolean
  ollama_readiness?: string
  station: {
    recording_available: boolean
    active_recording_id: string | null
    max_upload_bytes: number
    worker_connected: boolean
    worker_error?: string | null
    accepted_audio?: string[]
  }
}
export interface WorkerConfig { token: string }
export interface BrowserStatus {
  available: boolean
  state: 'ready' | 'opened' | 'unavailable'
  platform: 'meet' | 'zoom' | 'teams' | null
  message: string
  active_recording_id: string | null
  recordings: Array<{ id: string; state: 'recording' | 'stopped' | 'interrupted'; bytes: number; error?: string | null }>
}

export class APIError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}
function headers(config: WorkerConfig): HeadersInit { return { Authorization: `Bearer ${config.token}` } }
function errorMessage(body: string, status: number): string {
  try {
    const parsed = JSON.parse(body)
    if (typeof parsed.detail === 'string') return parsed.detail
    if (Array.isArray(parsed.detail)) return parsed.detail.map((item: { msg?: string }) => item.msg || 'Invalid input').join('; ')
    if (typeof parsed.error === 'string') return parsed.error
  } catch { /* A proxy may return a non-JSON error. */ }
  return status === 401 ? 'The station token was not accepted. Reconnect with the token from station setup.'
    : status === 413 ? 'This recording exceeds the station’s upload limit.'
    : `The station could not complete this request (${status}). Please try again.`
}
async function response(config: WorkerConfig, path: string, init: RequestInit = {}): Promise<Response> {
  const result = await fetch(base + path, { ...init, headers: { ...headers(config), ...init.headers }, signal: init.signal || AbortSignal.timeout(20000) })
  if (!result.ok) throw new APIError(errorMessage(await result.text(), result.status), result.status)
  return result
}
async function json<T>(config: WorkerConfig, path: string, init?: RequestInit): Promise<T> {
  return (await response(config, path, init)).json() as Promise<T>
}
// getRandomValues works on ordinary LAN HTTP; randomUUID does not.
export function createID(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
export const capabilities = (config: WorkerConfig) => json<Capabilities>(config, '/v1/capabilities')
export const listJobs = (config: WorkerConfig, limit = 50) => json<JobRecord[]>(config, `/v1/jobs?limit=${limit}`)
export const getJob = (config: WorkerConfig, id: string) => json<JobRecord>(config, `/v1/jobs/${encodeURIComponent(id)}`)
export const getResult = (config: WorkerConfig, id: string) => json<MeetingResult>(config, `/v1/jobs/${encodeURIComponent(id)}/result`)
export const retryJob = (config: WorkerConfig, id: string) => json<JobRecord>(config, `/v1/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' })
export const cancelJob = (config: WorkerConfig, id: string) => json<JobRecord>(config, `/v1/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
export const stopRecording = (config: WorkerConfig, id: string) => json<JobRecord>(config, `/v1/recordings/${encodeURIComponent(id)}/stop`, { method: 'POST' })
export const startRecording = (config: WorkerConfig, title: string, languageMode: string, diarization: boolean) => json<JobRecord>(config, '/v1/recordings/start', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, language_mode: languageMode, diarization }),
})
export const browserStatus = (config: WorkerConfig, signal?: AbortSignal) => json<BrowserStatus>(config, '/v1/browser/status', { signal })
export const openMeetingBrowser = (config: WorkerConfig, url: string) => json<BrowserStatus>(config, '/v1/browser/open', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }),
})
export const browserSession = (config: WorkerConfig) => json<{ viewer_path: string; expires_in: number }>(config, '/v1/browser/session', { method: 'POST' })
export const startBrowserRecording = (config: WorkerConfig, input: { title: string; language_mode: string; output_language: string; diarization: boolean }) => json<JobRecord>(config, '/v1/browser/recordings/start', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
})
export const stopBrowserRecording = (config: WorkerConfig, id: string) => json<JobRecord>(config, `/v1/browser/recordings/${encodeURIComponent(id)}/stop`, {
  method: 'POST', signal: AbortSignal.timeout(120000),
})
export function submitMeeting(config: WorkerConfig, input: {
  id: string; title: string; languageMode: string; outputLanguage: string; diarization: boolean; file?: File; transcript?: string
}, onProgress: (percent: number) => void, signal: AbortSignal): Promise<JobRecord> {
  const form = new FormData()
  form.set('manifest_json', JSON.stringify({ meeting_id: input.id, title: input.title || 'Meeting', language_mode: input.languageMode, output_language: input.outputLanguage, diarization: input.diarization }))
  if (input.file) form.set('audio', input.file)
  else form.set('transcript', input.transcript || '')
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', base + '/v1/jobs')
    xhr.setRequestHeader('Authorization', `Bearer ${config.token}`)
    xhr.setRequestHeader('Idempotency-Key', input.id)
    xhr.timeout = 30 * 60 * 1000
    const abort = () => xhr.abort()
    signal.addEventListener('abort', abort, { once: true })
    const cleanup = () => signal.removeEventListener('abort', abort)
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)) }
    xhr.onload = () => {
      cleanup()
      if (xhr.status < 200 || xhr.status >= 300) return reject(new APIError(errorMessage(xhr.responseText, xhr.status), xhr.status))
      try { resolve(JSON.parse(xhr.responseText) as JobRecord) } catch { reject(new Error('The station returned an unreadable response. Refresh the archive before trying again.')) }
    }
    xhr.onerror = () => { cleanup(); reject(new Error('The upload connection was interrupted. Retry to resume the same submission.')) }
    xhr.ontimeout = () => { cleanup(); reject(new Error('The upload timed out. Check the station connection and retry.')) }
    xhr.onabort = () => { cleanup(); reject(new DOMException('Upload cancelled', 'AbortError')) }
    if (signal.aborted) { cleanup(); reject(new DOMException('Upload cancelled', 'AbortError')); return }
    xhr.send(form)
  })
}
export async function loadAudio(config: WorkerConfig, id: string, signal: AbortSignal): Promise<Blob> {
  return (await response(config, `/v1/jobs/${encodeURIComponent(id)}/audio`, { signal })).blob()
}
export async function downloadExport(config: WorkerConfig, id: string, format: 'json' | 'csv' | 'pdf' | 'ics'): Promise<void> {
  const blob = await (await response(config, `/v1/jobs/${encodeURIComponent(id)}/export/${format}`)).blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `meeting-${id}.${format}`
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
