import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { AlertCircle, ArrowRight, AudioLines, Check, CheckCircle2, Clock3, Download, FileAudio, FileText, Headphones, Loader2, LockKeyhole, Mic, Monitor, RefreshCw, Search, ShieldCheck, Square, Upload, X } from 'lucide-react'
import { Layout } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { APIError, capabilities, cancelJob, createID, downloadExport, getResult, listJobs, loadAudio, retryJob, startRecording, stopRecording, submitMeeting, type Capabilities, type JobRecord, type JobStage, type MeetingResult, type ProtocolItem, type SourcedItem, type WorkerConfig } from './api'
import './meeting-intelligence.css'
import { MeetingBrowserPanel } from './MeetingBrowserPanel'

const tokenKey = 'mi.stationToken'
const selectionKey = 'mi.selectedJob'
const finished = new Set<JobStage>(['completed', 'failed', 'cancelled'])
const stageNames: Record<JobStage, string> = { recording: 'Recording', queued: 'Queued', preprocessing: 'Preparing audio', transcribing: 'Transcribing', diarizing: 'Separating voices', extracting: 'Writing report', validating: 'Checking sources', exporting: 'Preparing exports', completed: 'Ready', failed: 'Needs attention', cancelled: 'Cancelled' }
const stages: JobStage[] = ['queued', 'preprocessing', 'transcribing', 'diarizing', 'extracting', 'validating', 'exporting', 'completed']

function readSession(key: string) { try { return sessionStorage.getItem(key) || '' } catch { return '' } }
function saveSession(key: string, value: string) { try { if (value) sessionStorage.setItem(key, value); else sessionStorage.removeItem(key) } catch { /* The current page still works when browser storage is unavailable. */ } }
function message(error: unknown) { return error instanceof Error ? error.message : 'The station could not complete this request.' }
function time(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const value = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(value / 3600)
  return `${hours ? `${hours}:` : ''}${Math.floor(value / 60 % 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}`
}
function date(value: string, full = false) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', ...(full ? { year: 'numeric', hour: '2-digit', minute: '2-digit' } : {}) }) }
function size(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB` }
function title(job: JobRecord) { return job.manifest?.title || 'Untitled meeting' }
function Badge({ stage }: { stage: JobStage }) { return <span className={`mi-badge mi-stage-${stage}`}><span />{stageNames[stage] || stage}</span> }

export function MeetingIntelligencePage() {
  const [token, setToken] = useState(() => readSession(tokenKey))
  const [pairOpen, setPairOpen] = useState(() => !readSession(tokenKey))
  const [caps, setCaps] = useState<Capabilities>()
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [selected, setSelected] = useState(() => readSession(selectionKey))
  const [connected, setConnected] = useState(false)
  const [connectionError, setConnectionError] = useState('')
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [limit, setLimit] = useState(50)
  const [refreshKey, setRefreshKey] = useState(0)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [recordOpen, setRecordOpen] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const config = useMemo<WorkerConfig>(() => ({ token }), [token])
  const choose = useCallback((id: string) => { setSelected(id); saveSession(selectionKey, id) }, [])
  const refresh = useCallback(() => setRefreshKey(value => value + 1), [])
  const selectedJob = jobs.find(job => job.id === selected)
  const activeId = caps?.station.active_recording_id
  const activeJob = jobs.find(job => job.id === activeId)
  const missingSpeechModels = caps?.station.worker_connected && caps.selected_profiles && caps.readiness
    ? Object.values(caps.selected_profiles).some(profile => !caps.readiness?.[profile]?.ready) : false
  const diarizationAvailable = caps?.station.worker_connected ? caps.diarization : undefined

  useEffect(() => {
    document.title = 'Meeting Intelligence · Scriberr'
    // Older versions saved the Mac worker secret in localStorage. The station
    // now keeps that secret server-side and uses a separate tab-session token.
    try { localStorage.removeItem('mi.token'); localStorage.removeItem('mi.workerUrl') } catch { /* Storage may be disabled. */ }
  }, [])

  useEffect(() => {
    if (!token) return
    let disposed = false
    let pending = false
    async function poll() {
      if (pending || document.hidden) return
      pending = true
      try {
        const results = await Promise.allSettled([capabilities(config), listJobs(config, limit)])
        if (disposed) return
        const rejected = results.find(result => result.status === 'rejected')
        if (rejected?.status === 'rejected') throw rejected.reason
        const station = results[0]
        const archive = results[1]
        if (station.status === 'fulfilled') setCaps(station.value)
        if (archive.status === 'fulfilled') {
          setJobs(archive.value)
          setSelected(previous => {
            const next = archive.value.some(job => job.id === previous) ? previous : archive.value[0]?.id || ''
            saveSession(selectionKey, next)
            return next
          })
        }
        setConnected(true)
        setConnectionError('')
      } catch (cause) {
        if (disposed) return
        setConnected(false)
        setConnectionError(message(cause))
        if (cause instanceof APIError && cause.status === 401) {
          saveSession(tokenKey, ''); setToken(''); setJobs([]); setCaps(undefined); setPairOpen(true)
        }
      } finally { pending = false }
    }
    void poll()
    const timer = window.setInterval(() => { void poll() }, 4000)
    const onVisible = () => { if (!document.hidden) void poll() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { disposed = true; window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [token, config, limit, refreshKey])

  const acceptJob = useCallback((job: JobRecord) => {
    setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)])
    choose(job.id)
    refresh()
  }, [choose, refresh])

  async function perform(action: () => Promise<JobRecord>) {
    setActionBusy(true); setError('')
    try { acceptJob(await action()) } catch (cause) { setError(message(cause)) } finally { setActionBusy(false) }
  }
  function disconnect() {
    saveSession(tokenKey, ''); setToken(''); setConnected(false); setJobs([]); setCaps(undefined); choose(''); setPairOpen(false)
  }
  const visible = jobs.filter(job => title(job).toLocaleLowerCase().includes(search.toLocaleLowerCase()))

  return <Layout><div className="mi-page">
    <header className="mi-heading">
      <div><div className="mi-eyebrow">YOUR LOCAL MEETING STATION</div><h1>Conversations, with a next step.</h1><p>A shared archive. Clear decisions. Everything on your devices.</p></div>
      <div className="mi-heading-actions"><Button variant="outline" onClick={() => setRecordOpen(true)} disabled={!connected || !caps?.station.recording_available || Boolean(activeId)} title={!caps?.station.recording_available ? 'A microphone must be configured on the station' : undefined}><Mic />Record microphone</Button><Button onClick={() => setUploadOpen(true)} disabled={!connected}><Upload />Upload audio</Button></div>
    </header>

    <div className="mi-station-strip">
      <div className="mi-station-device"><Monitor /><div><strong>Radxa Cubie A7A</strong><span>{connected ? 'Archive connected' : token ? 'Connecting to station…' : 'Station not paired'}</span></div><span className={`mi-status-dot ${connected ? 'is-online' : ''}`} /></div>
      <div className="mi-network-label"><span />LOCAL NETWORK<ArrowRight /></div>
      <div className="mi-station-device"><AudioLines /><div><strong>Mac AI engine</strong><span>{!connected ? 'Awaiting station' : caps?.station.worker_connected ? 'Worker connected' : 'Waiting for Mac connection'}</span></div><span className={`mi-status-dot ${connected && caps?.station.worker_connected ? 'is-online' : ''}`} /></div>
      <Button variant="ghost" size="sm" className="mi-pair-button" onClick={() => setPairOpen(true)}><LockKeyhole />{token ? 'Connection' : 'Pair station'}</Button>
    </div>
    {connected && !caps?.station.worker_connected && <div className="mi-notice"><Clock3 /><div><strong>Your archive is available.</strong> Recordings can be saved while the Mac is offline. Processing continues when the engine reconnects.</div></div>}
    {connected && missingSpeechModels && <div className="mi-notice mi-warning"><AlertCircle /><div><strong>The Mac needs a speech model.</strong> A selected transcription model is not available. Recordings can still be archived and retried after the Mac setup is complete.</div></div>}
    {connectionError && <div className="mi-notice mi-warning" role="status"><AlertCircle /><span>{connectionError} {token ? 'Reconnecting automatically.' : ''}</span><Button variant="ghost" size="sm" onClick={token ? refresh : () => setPairOpen(true)}>{token ? 'Retry' : 'Connect'}</Button></div>}
    {error && <div className="mi-notice mi-warning" role="alert"><AlertCircle /><span>{error}</span><button className="mi-icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X /></button></div>}
    {activeId && <div className="mi-recording-banner"><span className="mi-recording-dot" /><div><strong>{activeJob ? title(activeJob) : 'Recording on the station'}</strong><small>Audio is being saved on your Radxa.</small></div><RecordingClock started={activeJob?.created_at} /><Button variant="outline" onClick={() => void perform(() => stopRecording(config, activeId))} disabled={actionBusy || !connected}><Square />Stop &amp; process</Button></div>}

    <MeetingBrowserPanel config={config} connected={connected} jobs={jobs} onCreated={acceptJob} diarizationAvailable={diarizationAvailable} />

    <div className="mi-workspace">
      <aside className="mi-archive" aria-label="Meeting archive">
        <div className="mi-archive-heading"><h2>All meetings <span>{jobs.length}</span></h2><button className="mi-icon-button" aria-label="Refresh archive" title="Refresh archive" onClick={refresh} disabled={!token}><RefreshCw /></button></div>
        <label className="mi-search"><Search /><input type="search" placeholder="Find a meeting…" aria-label="Find a meeting by title" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <div className="mi-meeting-list">{visible.map(job => <button key={job.id} className={`mi-meeting ${job.id === selected ? 'is-selected' : ''}`} aria-pressed={job.id === selected} onClick={() => choose(job.id)}><span className="mi-meeting-icon">{job.source_kind === 'text' ? <FileText /> : <AudioLines />}</span><span className="mi-meeting-copy"><strong>{title(job)}</strong><span>{date(job.created_at)}<span className="mi-list-divider">·</span>{job.source_kind === 'text' ? 'Transcript' : 'Recording'}</span><Badge stage={job.stage} /></span>{job.id === selected && <span className="mi-selection-bar" />}</button>)}
          {!visible.length && <div className="mi-list-empty"><Headphones /><strong>{search ? 'No matching meetings' : 'Your archive starts here'}</strong><p>{search ? 'Try another meeting title.' : token && !connected ? 'Connecting to your station…' : 'Upload a recording or start a meeting to save it here.'}</p></div>}
        </div>
        {jobs.length >= limit && limit < 200 && <button className="mi-load-more" onClick={() => setLimit(value => Math.min(200, value + 50))}>Load older meetings</button>}
        <div className="mi-archive-footer"><LockKeyhole />Stored on your station</div>
      </aside>
      <section className="mi-detail" aria-label="Meeting details">
        {selectedJob ? <MeetingDetail key={selectedJob.id} job={selectedJob} config={config} connected={connected} onError={setError} onRetry={() => void perform(() => retryJob(config, selectedJob.id))} onCancel={() => void perform(() => cancelJob(config, selectedJob.id))} busy={actionBusy} /> : <div className="mi-welcome"><div className="mi-welcome-art" aria-hidden="true"><span className="mi-art-orbit" /><div className="mi-art-paper"><AudioLines /><i /><i /><i /><span><Check /></span></div></div><div className="mi-eyebrow">FROM CONVERSATION TO CLARITY</div><h2>Good meetings don’t end<br />when the call does.</h2><p>Bring a recording. Leave with a transcript,<br />the decisions, and what happens next.</p><Button onClick={() => connected ? setUploadOpen(true) : setPairOpen(true)}>{connected ? <Upload /> : <LockKeyhole />}{connected ? 'Upload your first recording' : 'Connect to your station'}</Button><small>MP3, WAV, M4A, WebM or CAF</small></div>}
      </section>
    </div>
    <footer className="mi-footer"><span>Meeting Intelligence <span>/</span> Powered by your devices</span><span><LockKeyhole />Local transcription and a private archive</span></footer>

    <PairDialog open={pairOpen} onOpenChange={setPairOpen} connected={connected} onDisconnect={disconnect} onConnect={(value, station) => { saveSession(tokenKey, value); setToken(value); setCaps(station); setConnected(true); setConnectionError(''); setPairOpen(false); refresh() }} />
    <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} config={config} maxBytes={caps?.station.max_upload_bytes} diarizationAvailable={diarizationAvailable} onCreated={acceptJob} />
    <RecordDialog open={recordOpen} onOpenChange={setRecordOpen} config={config} diarizationAvailable={diarizationAvailable} onCreated={acceptJob} />
  </div></Layout>
}

function PairDialog({ open, onOpenChange, connected, onConnect, onDisconnect }: { open: boolean; onOpenChange: (value: boolean) => void; connected: boolean; onConnect: (value: string, caps: Capabilities) => void; onDisconnect: () => void }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function connect(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { const station = await capabilities({ token: value.trim() }); onConnect(value.trim(), station); setValue('') } catch (cause) { setError(message(cause)) } finally { setBusy(false) }
  }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="mi-dialog"><DialogHeader><div className="mi-modal-icon"><LockKeyhole /></div><DialogTitle>Connect to your station.</DialogTitle><DialogDescription>Enter the station token from your Radxa setup. The shared archive lives on this device.</DialogDescription></DialogHeader><form onSubmit={event => void connect(event)} className="mi-form"><label htmlFor="mi-token">Station token</label><Input id="mi-token" type="password" required autoComplete="off" spellCheck={false} placeholder="Paste your station token" value={value} onChange={event => setValue(event.target.value)} /><ErrorText error={error} /><Button type="submit" disabled={busy || !value.trim()}>{busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}Connect to station</Button>{connected && <Button type="button" variant="ghost" onClick={onDisconnect}>Disconnect this browser</Button>}<small>The token stays in this browser tab’s session.</small></form></DialogContent></Dialog>
}

function UploadDialog({ open, onOpenChange, config, maxBytes, diarizationAvailable, onCreated }: { open: boolean; onOpenChange: (value: boolean) => void; config: WorkerConfig; maxBytes?: number; diarizationAvailable?: boolean; onCreated: (job: JobRecord) => void }) {
  const [meetingTitle, setMeetingTitle] = useState('')
  const [language, setLanguage] = useState('kk_ru')
  const [outputLanguage, setOutputLanguage] = useState('same')
  const [diarization, setDiarization] = useState(false)
  const [file, setFile] = useState<File>()
  const [transcript, setTranscript] = useState('')
  const [mode, setMode] = useState<'audio' | 'text'>('audio')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [percent, setPercent] = useState(0)
  const [dragging, setDragging] = useState(false)
  const submission = useRef('')
  const uploadController = useRef<AbortController | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => () => uploadController.current?.abort(), [])
  function chooseFile(value?: File) {
    setError(''); submission.current = ''
    if (value && !/\.(mp3|wav|m4a|webm|caf)$/i.test(value.name)) { setError('Choose an MP3, WAV, M4A, WebM or CAF recording.'); return }
    if (value && maxBytes && value.size > maxBytes) { setError(`This recording exceeds the station’s ${size(maxBytes)} limit.`); return }
    setFile(value)
    if (value && !meetingTitle.trim()) setMeetingTitle(value.name.replace(/\.[^.]+$/, '').slice(0, 200))
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('')
    if (mode === 'audio' && !file) { setError('Choose a recording to upload.'); return }
    if (mode === 'text' && !transcript.trim()) { setError('Paste a transcript to process.'); return }
    setBusy(true); setPercent(0)
    const controller = new AbortController(); uploadController.current = controller
    try {
      if (!submission.current) submission.current = createID()
      const job = await submitMeeting(config, { id: submission.current, title: meetingTitle.trim(), languageMode: language, outputLanguage, diarization: diarization && diarizationAvailable !== false, file: mode === 'audio' ? file : undefined, transcript: mode === 'text' ? transcript : undefined }, setPercent, controller.signal)
      onCreated(job); onOpenChange(false); setFile(undefined); setTranscript(''); setMeetingTitle(''); submission.current = ''
      if (fileInput.current) fileInput.current.value = ''
    } catch (cause) { if (!(cause instanceof DOMException && cause.name === 'AbortError')) setError(message(cause)) } finally { setBusy(false); uploadController.current = null }
  }
  return <Dialog open={open} onOpenChange={value => { if (!busy) onOpenChange(value) }}><DialogContent className="mi-dialog" showCloseButton={!busy}><DialogHeader><div className="mi-modal-icon"><Upload /></div><DialogTitle>A recording, with a next step.</DialogTitle><DialogDescription>Save a conversation to the Radxa. Your Mac engine handles transcription and the meeting report.</DialogDescription></DialogHeader><form className="mi-form" onSubmit={event => void submit(event)}><fieldset disabled={busy}>
    <label htmlFor="mi-meeting-title">Meeting title</label><Input id="mi-meeting-title" required maxLength={200} placeholder="e.g. Friday product planning" value={meetingTitle} onChange={event => { setMeetingTitle(event.target.value); submission.current = '' }} />
    <div className="mi-input-mode"><button type="button" className={mode === 'audio' ? 'is-active' : ''} onClick={() => { setMode('audio'); submission.current = '' }}><FileAudio />Audio recording</button><button type="button" className={mode === 'text' ? 'is-active' : ''} onClick={() => { setMode('text'); submission.current = '' }}><FileText />Existing transcript</button></div>
    {mode === 'audio' ? <label className={`mi-dropzone ${dragging ? 'is-dragging' : ''}`} onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!busy) chooseFile(event.dataTransfer.files[0]) }}><input ref={fileInput} type="file" accept=".mp3,.wav,.m4a,.webm,.caf" aria-label="Choose a recording" onChange={event => chooseFile(event.target.files?.[0])} /><Headphones /><strong>{file ? file.name : 'Choose a recording or drop it here'}</strong><span>{file ? size(file.size) : 'MP3, WAV, M4A, WebM or CAF'}</span></label> : <Textarea aria-label="Meeting transcript" rows={6} placeholder="Paste the conversation here…" value={transcript} onChange={event => { setTranscript(event.target.value); submission.current = '' }} />}
    <div className="mi-form-columns"><label>Audio language<select value={language} onChange={event => { setLanguage(event.target.value); submission.current = '' }}><option value="kk_ru">Қазақша + русский</option><option value="en">English</option><option value="auto">Auto detect</option></select></label><label>Report language<select value={outputLanguage} onChange={event => { setOutputLanguage(event.target.value); submission.current = '' }}><option value="same">Same as conversation</option><option value="kk">Қазақша</option><option value="ru">Русский</option><option value="en">English</option></select></label></div>
    {mode === 'audio' && <label className="mi-checkbox"><input type="checkbox" checked={diarization && diarizationAvailable !== false} disabled={diarizationAvailable === false} onChange={event => { setDiarization(event.target.checked); submission.current = '' }} /><span>Separate speakers<small>{diarizationAvailable === false ? 'Speaker separation is not available on the Mac.' : 'Uses the diarization models on your Mac.'}</small></span></label>}
    </fieldset>
    {busy && <div className="mi-upload-progress" role="status"><div><span>{percent >= 100 ? 'Saving to the station…' : 'Uploading to the station…'}</span><span>{percent}%</span></div><progress value={percent} max={100} aria-label="Upload progress" /></div>}
    <ErrorText error={error} /><Button type="submit" disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <Upload />}Save &amp; process</Button><small>{maxBytes ? `Up to ${size(maxBytes)}. ` : ''}Audio stays on your local network.</small>
  </form></DialogContent></Dialog>
}

function RecordDialog({ open, onOpenChange, config, diarizationAvailable, onCreated }: { open: boolean; onOpenChange: (value: boolean) => void; config: WorkerConfig; diarizationAvailable?: boolean; onCreated: (job: JobRecord) => void }) {
  const [meetingTitle, setMeetingTitle] = useState('')
  const [language, setLanguage] = useState('kk_ru')
  const [diarization, setDiarization] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function record(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { onCreated(await startRecording(config, meetingTitle.trim(), language, diarization && diarizationAvailable !== false)); onOpenChange(false); setMeetingTitle('') } catch (cause) { setError(message(cause)) } finally { setBusy(false) }
  }
  return <Dialog open={open} onOpenChange={value => { if (!busy) onOpenChange(value) }}><DialogContent className="mi-dialog" showCloseButton={!busy}><DialogHeader><div className="mi-modal-icon"><Mic /></div><DialogTitle>Be in the conversation.</DialogTitle><DialogDescription>Record with the microphone connected to your Radxa station. Audio is saved there until you stop.</DialogDescription></DialogHeader><form className="mi-form" onSubmit={event => void record(event)}><fieldset disabled={busy}><label htmlFor="mi-record-title">Meeting title</label><Input id="mi-record-title" required maxLength={200} placeholder="e.g. Team check-in" value={meetingTitle} onChange={event => setMeetingTitle(event.target.value)} /><label>Audio language<select value={language} onChange={event => setLanguage(event.target.value)}><option value="kk_ru">Қазақша + русский</option><option value="en">English</option><option value="auto">Auto detect</option></select></label><label className="mi-checkbox"><input type="checkbox" checked={diarization && diarizationAvailable !== false} disabled={diarizationAvailable === false} onChange={event => setDiarization(event.target.checked)} /><span>Separate speakers<small>{diarizationAvailable === false ? 'Speaker separation is not available on the Mac.' : 'Uses the diarization models on your Mac.'}</small></span></label><div className="mi-record-source"><Mic /><div><strong>Station microphone</strong><span>Connected to Radxa Cubie A7A</span></div><LockKeyhole /></div></fieldset><ErrorText error={error} /><Button type="submit" disabled={busy || !meetingTitle.trim()}>{busy ? <Loader2 className="animate-spin" /> : <Mic />}Start recording</Button></form></DialogContent></Dialog>
}

function RecordingClock({ started }: { started?: string }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  return <span className="mi-recording-clock">{started ? time((now - new Date(started).getTime()) / 1000) : 'Recording'}</span>
}
function ErrorText({ error }: { error: string }) { return error ? <p className="mi-form-error" role="alert"><AlertCircle />{error}</p> : null }

function MeetingDetail({ job, config, connected, onError, onRetry, onCancel, busy }: { job: JobRecord; config: WorkerConfig; connected: boolean; onError: (value: string) => void; onRetry: () => void; onCancel: () => void; busy: boolean }) {
  const [result, setResult] = useState<MeetingResult>()
  const [resultError, setResultError] = useState('')
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [tab, setTab] = useState<'overview' | 'transcript'>('overview')
  const [query, setQuery] = useState('')
  const [highlight, setHighlight] = useState<string[]>([])
  const [audioURL, setAudioURL] = useState('')
  const [audioLoading, setAudioLoading] = useState(false)
  const [audioError, setAudioError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [format, setFormat] = useState<'json' | 'csv' | 'pdf'>('pdf')
  const audio = useRef<HTMLAudioElement>(null)
  const audioController = useRef<AbortController | null>(null)
  const audioObject = useRef('')
  const segmentElements = useRef(new Map<string, HTMLDivElement>())

  useEffect(() => {
    if (job.stage !== 'completed') { setResult(undefined); return }
    let disposed = false
    setLoading(true); setResultError('')
    getResult(config, job.id).then(value => { if (!disposed) setResult(value) }).catch(cause => { if (!disposed) setResultError(message(cause)) }).finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [config, job.id, job.stage, job.updated_at, reload])
  useEffect(() => () => { audioController.current?.abort(); if (audioObject.current) URL.revokeObjectURL(audioObject.current) }, [])

  async function fetchAudio() {
    if (audioLoading || audioURL) return
    setAudioLoading(true); setAudioError('')
    const controller = new AbortController(); audioController.current = controller
    try {
      const blob = await loadAudio(config, job.id, controller.signal)
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob); audioObject.current = url; setAudioURL(url)
    } catch (cause) { if (!controller.signal.aborted) setAudioError(message(cause)) } finally { if (!controller.signal.aborted) setAudioLoading(false) }
  }
  function seek(seconds: number | null | undefined) {
    if (seconds == null || !audio.current) return
    audio.current.currentTime = seconds
    void audio.current.play().catch(() => setAudioError('Press play to listen from this timestamp.'))
  }
  function jump(item: SourcedItem) {
    const ids = item.evidence.segment_ids || []
    setTab('transcript'); setQuery(''); setHighlight(ids)
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      const first = ids.map(id => segmentElements.current.get(id)).find(Boolean)
      first?.scrollIntoView({ behavior: 'smooth', block: 'center' }); first?.focus({ preventScroll: true })
    }))
    if (item.evidence.start != null && audioURL) seek(item.evidence.start)
  }
  async function exportFile() {
    setExporting(true); onError('')
    try { await downloadExport(config, job.id, format) } catch (cause) { onError(message(cause)) } finally { setExporting(false) }
  }
  const segments = useMemo(() => (result?.transcript.segments || []).slice().sort((a, b) => (a.start ?? 0) - (b.start ?? 0)), [result])
  const speakers = useMemo(() => Array.from(new Set(segments.map(segment => segment.speaker).filter((speaker): speaker is string => Boolean(speaker)))), [segments])
  const filtered = segments.filter(segment => segment.text.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
  const processing = !finished.has(job.stage) && job.stage !== 'recording'
  const tabKeys = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft' || event.key === 'Home' || event.key === 'End') {
      event.preventDefault(); const next = event.key === 'Home' ? 'overview' : event.key === 'End' ? 'transcript' : tab === 'overview' ? 'transcript' : 'overview'; setTab(next); document.getElementById(`mi-tab-${next}`)?.focus()
    }
  }
  return <div>
    <header className="mi-detail-header"><div className="mi-detail-topline"><Badge stage={job.stage} /><span>{date(job.created_at, true)}</span></div><h2>{title(job)}</h2><div className="mi-detail-meta"><span>{job.source_kind === 'text' ? <FileText /> : <Headphones />}{job.source_kind === 'text' ? 'Imported transcript' : 'Audio recording'}</span>{job.station?.source_bytes ? <span>{size(job.station.source_bytes)}</span> : null}{result?.protocol.metadata.duration_seconds != null && <span><Clock3 />{time(result.protocol.metadata.duration_seconds)}</span>}{result?.transcript.language && <span>{result.transcript.language.toUpperCase()}</span>}</div>{result && <div className="mi-exports"><select aria-label="Export format" value={format} onChange={event => setFormat(event.target.value as typeof format)}><option value="pdf">PDF report</option><option value="csv">CSV action items</option><option value="json">JSON result</option></select><Button variant="outline" size="sm" disabled={exporting || !connected} onClick={() => void exportFile()}>{exporting ? <Loader2 className="animate-spin" /> : <Download />}Export</Button></div>}</header>

    {job.error_message && <div className="mi-job-error" role="status"><AlertCircle /><div><strong>{job.error_code === 'ENGINE_OFFLINE' ? 'Waiting for the Mac engine' : 'Processing needs attention'}</strong><p>{job.error_message}</p></div>{(job.stage === 'failed' || job.stage === 'cancelled') && <Button variant="outline" size="sm" disabled={busy || !connected} onClick={onRetry}><RefreshCw />Retry</Button>}</div>}
    {(job.stage === 'failed' || job.stage === 'cancelled') && !job.error_message && <div className="mi-job-error"><AlertCircle /><span>{job.stage === 'cancelled' ? 'Processing was cancelled. Your source is still saved.' : 'Processing failed. Your source is still saved.'}</span><Button variant="outline" size="sm" disabled={busy || !connected} onClick={onRetry}><RefreshCw />Retry</Button></div>}
    {processing && <div className="mi-processing"><div><Loader2 className="animate-spin" /><strong>{stageNames[job.stage]}</strong><Button variant="ghost" size="sm" disabled={busy || !connected} onClick={onCancel}>Cancel processing</Button></div><div className="mi-processing-track" aria-label={`Processing stage: ${stageNames[job.stage]}`}>{stages.filter(stage => stage !== 'diarizing' || job.manifest?.diarization).map(stage => <span key={stage} className={stages.indexOf(stage) <= stages.indexOf(job.stage) ? 'is-done' : ''} />)}</div><p>{job.stage === 'queued' ? 'Your source is saved. The Mac processes one meeting at a time.' : 'Working locally on your Mac. You can leave this page and return to the archive.'}</p></div>}
    {job.source_kind === 'audio' && job.stage !== 'recording' && <div className="mi-audio"><div className="mi-audio-top"><Headphones /><div><strong>Original recording</strong><small>{job.station?.filename || 'Saved on the station'}</small></div>{!audioURL && <Button variant="outline" size="sm" disabled={audioLoading || !connected} onClick={() => void fetchAudio()}>{audioLoading ? <Loader2 className="animate-spin" /> : null}{audioLoading ? 'Loading…' : 'Load audio'}</Button>}</div>{audioURL && <audio ref={audio} src={audioURL} controls preload="metadata" aria-label="Meeting recording" onError={() => setAudioError('This browser cannot play this audio format. The original recording remains saved.')} />}<ErrorText error={audioError} /></div>}

    <div className="mi-tabs" role="tablist" aria-label="Meeting content"><button id="mi-tab-overview" role="tab" aria-selected={tab === 'overview'} aria-controls="mi-overview" tabIndex={tab === 'overview' ? 0 : -1} onKeyDown={tabKeys} onClick={() => setTab('overview')}>Overview</button><button id="mi-tab-transcript" role="tab" aria-selected={tab === 'transcript'} aria-controls="mi-transcript" tabIndex={tab === 'transcript' ? 0 : -1} onKeyDown={tabKeys} onClick={() => setTab('transcript')}>Transcript <span>{segments.length}</span></button></div>
    {loading && <div className="mi-detail-empty" role="status"><Loader2 className="animate-spin" /><strong>Opening the meeting report…</strong></div>}
    {resultError && <div className="mi-detail-empty" role="alert"><AlertCircle /><p>{resultError}</p><Button variant="outline" onClick={() => setReload(value => value + 1)}>Try again</Button></div>}
    {!loading && !result && !resultError && <div className="mi-detail-empty"><FileText /><strong>{job.stage === 'recording' ? 'Enjoy the conversation.' : 'The details are on their way.'}</strong><p>{job.stage === 'recording' ? 'Stop the station recording when you’re ready to create the transcript and report.' : job.stage === 'failed' || job.stage === 'cancelled' ? 'Retry processing to generate the transcript and meeting report.' : 'The transcript, decisions, and action items will appear here when processing finishes.'}</p></div>}
    {result && <>
      <div id="mi-overview" role="tabpanel" aria-labelledby="mi-tab-overview" hidden={tab !== 'overview'} className="mi-report">
        <ReportSection title="The conversation, in brief" icon={<FileText />}><div className="mi-summary">{result.protocol.executive_summary.length ? result.protocol.executive_summary.map((line, index) => <p key={index}>{line}</p>) : <p className="mi-muted">No summary was produced for this meeting.</p>}</div></ReportSection>
        {Boolean(result.protocol.topics?.length) && <div className="mi-topics">{result.protocol.topics.map(topic => <span key={topic.id}>{topic.title || topic.text}</span>)}</div>}
        <ReportSection title="Decisions" count={result.protocol.decisions.length} icon={<CheckCircle2 />}><ItemList items={result.protocol.decisions} onJump={jump} empty="No confirmed decisions were found." /></ReportSection>
        <ReportSection title="What happens next" count={result.protocol.action_items.length} icon={<ArrowRight />}><div className="mi-items">{result.protocol.action_items.length ? result.protocol.action_items.map(item => <article className="mi-action-item" key={item.id}><span className="mi-task-box" /><div><p>{item.task}</p><div className="mi-action-meta"><span>{item.assignee || 'Unassigned'}</span><span><Clock3 />{item.deadline_text || 'No deadline specified'}</span>{item.priority !== 'not_specified' && <span className={`mi-priority mi-priority-${item.priority}`}>{item.priority}</span>}</div><EvidenceView item={item} onJump={jump} /></div></article>) : <p className="mi-muted">No action items were found.</p>}</div></ReportSection>
        <div className="mi-report-columns"><ReportSection title="Open questions" icon={<AlertCircle />}><ItemList items={result.protocol.open_questions} onJump={jump} empty="No open questions were found." /></ReportSection><ReportSection title="Risks to keep in view" icon={<ShieldCheck />}><ItemList items={result.protocol.risks} onJump={jump} empty="No explicit risks were found." /></ReportSection></div>
        <p className="mi-report-footnote"><ShieldCheck />Source checks compare extracted items with the transcript. Review decisions and task owners before sharing.</p>
      </div>
      <div id="mi-transcript" role="tabpanel" aria-labelledby="mi-tab-transcript" hidden={tab !== 'transcript'} className="mi-transcript"><label className="mi-search"><Search /><input aria-label="Search this transcript" type="search" placeholder="Search this transcript…" value={query} onChange={event => setQuery(event.target.value)} /></label>{segments.length ? <><div className="mi-transcript-rows">{filtered.map(segment => <div key={segment.id} ref={element => { if (element) segmentElements.current.set(segment.id, element); else segmentElements.current.delete(segment.id) }} tabIndex={-1} className={`mi-segment ${highlight.includes(segment.id) ? 'is-highlighted' : ''}`}><div className="mi-segment-meta"><span className={`mi-speaker mi-speaker-${segment.speaker ? speakers.indexOf(segment.speaker) % 4 : 0}`}>{segment.speaker ? `Speaker ${speakers.indexOf(segment.speaker) + 1}` : 'Speaker unknown'}</span>{segment.start != null && <button className="mi-timestamp" title={audioURL ? 'Play from this timestamp' : 'Load the recording to listen from this timestamp'} disabled={!audioURL} onClick={() => seek(segment.start)}>{time(segment.start)}{segment.end != null ? ` – ${time(segment.end)}` : ''}</button>}</div><p dir="auto">{segment.text}</p></div>)}</div>{!filtered.length && <p className="mi-muted mi-no-matches">No transcript passages match this search.</p>}</> : <p className="mi-raw-transcript" dir="auto">{result.transcript.raw_text || 'No speech was found in this recording.'}</p>}<p className="mi-report-footnote">Speaker labels distinguish voices; they do not identify people.</p></div>
    </>}
  </div>
}

function ReportSection({ title: heading, icon, count, children }: { title: string; icon: ReactNode; count?: number; children: ReactNode }) {
  return <section className="mi-report-section"><h3>{icon}{heading}{count !== undefined && <span>{count}</span>}</h3>{children}</section>
}
function ItemList({ items, onJump, empty }: { items: ProtocolItem[]; onJump: (item: SourcedItem) => void; empty: string }) {
  return <div className="mi-items">{items.length ? items.map(item => <article className="mi-report-item" key={item.id}><p>{item.text}</p><EvidenceView item={item} onJump={onJump} /></article>) : <p className="mi-muted">{empty}</p>}</div>
}
function EvidenceView({ item, onJump }: { item: SourcedItem; onJump: (item: SourcedItem) => void }) {
  const hasSource = Boolean(item.evidence.segment_ids?.length)
  const reviewRequired = item.review_status === 'needs_review' || item.review_status === 'rejected'
  return <div className={`mi-evidence ${item.source_check === 'passed' && !reviewRequired ? 'is-checked' : 'needs-review'}`}><div>{item.source_check === 'passed' ? <ShieldCheck /> : <AlertCircle />}<span>{item.source_check === 'passed' ? 'Source checked' : 'Source needs review'}</span>{reviewRequired && <span className="mi-review-label">{item.review_status === 'rejected' ? 'Rejected in review' : 'Needs review'}</span>}{item.review_status === 'human_confirmed' && <span className="mi-review-label is-confirmed">Human confirmed</span>}{hasSource && <button onClick={() => onJump(item)}>{item.evidence.start != null ? time(item.evidence.start) : 'View transcript'}<ArrowRight /></button>}</div>{item.evidence.quote && <blockquote dir="auto">“{item.evidence.quote}”</blockquote>}</div>
}
