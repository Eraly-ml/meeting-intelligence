import { useEffect, useState, type FormEvent } from 'react'
import { AlertCircle, Loader2, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { browserSession, browserStatus, joinMeeting, stopBrowserRecording, type BrowserStatus, type JobRecord, type WorkerConfig } from './api'

function describe(error: unknown) { return error instanceof Error ? error.message : 'The meeting browser could not complete this request.' }

export function MeetingBrowserPanel({ open: expanded, onOpenChange, config, connected, jobs, onCreated, diarizationAvailable }: {
  open: boolean; onOpenChange: (value: boolean) => void; config: WorkerConfig; connected: boolean; jobs: JobRecord[]; onCreated: (job: JobRecord) => void; diarizationAvailable?: boolean
}) {
  const [status, setStatus] = useState<BrowserStatus>()
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('Online meeting')
  const [language, setLanguage] = useState('auto')
  const [outputLanguage, setOutputLanguage] = useState('same')
  const [diarization, setDiarization] = useState(false)
  const [viewer, setViewer] = useState('')
  const [viewerRevision, setViewerRevision] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    if (!config.token || !connected) { setStatus(undefined); setViewer(''); return }
    let disposed = false
    let pending = false
    let controller: AbortController | undefined
    async function poll() {
      if (pending || document.hidden) return
      pending = true
      controller = new AbortController()
      const timeout = window.setTimeout(() => controller?.abort(), 15000)
      try {
        const next = await browserStatus(config, controller.signal)
        if (!disposed) { setStatus(next); setPollError('') }
      } catch (cause) { if (!disposed) setPollError(describe(cause)) }
      finally { window.clearTimeout(timeout); pending = false }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 5000)
    const visible = () => { if (!document.hidden) void poll() }
    document.addEventListener('visibilitychange', visible)
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible) }
  }, [config, connected, revision])

  // Refresh the narrowly scoped viewer cookie while this panel is open. The
  // station token remains in Authorization headers, never in the iframe URL.
  useEffect(() => {
    if (!viewer || !expanded || !connected) return
    const timer = window.setInterval(() => {
      void browserSession(config).then(() => setViewerRevision(value => value + 1)).catch(cause => setError(describe(cause)))
    }, 20 * 60 * 1000)
    return () => window.clearInterval(timer)
  }, [viewer, expanded, connected, config])

  async function showViewer() {
    const session = await browserSession(config)
    if (!session.viewer_path.startsWith('/api/meeting-worker/v1/browser/view/')) throw new Error('The station returned an invalid browser viewer address.')
    setViewer(`${session.viewer_path}&autoconnect=1&resize=scale`)
    setViewerRevision(value => value + 1)
  }
  async function perform(name: string, action: () => Promise<void>) {
    setBusy(name); setError('')
    try { await action() } catch (cause) { setError(describe(cause)) }
    finally { setBusy(''); setRevision(value => value + 1) }
  }
  function open(event: FormEvent) {
    event.preventDefault()
    void perform('open', async () => {
      onCreated(await joinMeeting(config, { url: url.trim(), title: title.trim() || 'Online meeting', language_mode: language, output_language: outputLanguage, diarization }))
    })
  }
  const activeId = status?.active_recording_id
  const audioHealth = status?.recordings.find(recording => recording.id === activeId)?.audio_health
  const inCall = activeId && status?.join?.state === 'joined'
  const audioWarning = inCall && audioHealth && (audioHealth.state === 'stalled' || audioHealth.quiet_seconds >= 15)
  const pendingRecordings = (status?.recordings || []).filter(recording => recording.state !== 'recording' && jobs.some(job => job.id === recording.id && !job.station?.archived))
  const available = connected && status?.available && !pollError
  const stop = (id: string) => perform('stop', async () => { onCreated(await stopBrowserRecording(config, id)) })

  if (!expanded && !activeId && !pendingRecordings.length && !error && status?.join?.state !== 'blocked') return null

  return <section className="mi-browser-panel" aria-label="Online meeting browser">
    {expanded && <div className="mi-browser-heading">
      <h2>Join call</h2>
      <button className="mi-icon-button" type="button" onClick={() => onOpenChange(false)} aria-label="Close join call"><X /></button>
    </div>}
    {activeId && <div className="mi-recording-banner" role="status"><span className="mi-recording-dot" /><div><strong>{status?.join?.state === 'joining' ? 'Joining meeting' : status?.join?.state === 'waiting' ? 'Waiting for admission' : 'Recording meeting audio'}</strong><small>{status?.join?.message || 'Incoming audio is being saved on the Radxa.'}</small>{inCall && audioHealth && <div className={`mi-audio-signal${audioWarning ? ' mi-audio-signal-warning' : ''}`}><meter aria-label="Incoming audio level" min={-60} max={0} value={Math.max(-60, audioHealth.rms_dbfs)} /><span>{audioHealth.state === 'stalled' ? 'Recorder stopped receiving data. Leave and rejoin the call.' : audioHealth.state === 'receiving' ? 'Audio received' : audioHealth.received ? `Quiet for ${audioHealth.quiet_seconds}s` : 'No incoming audio yet'}</span>{audioWarning && audioHealth.state !== 'stalled' && <span>Check that a participant is speaking with their microphone on.</span>}</div>}</div><Button variant="outline" disabled={!!busy || !connected} onClick={() => void stop(activeId)}>{busy === 'stop' ? <Loader2 className="animate-spin" /> : <Square />}Leave &amp; process</Button></div>}
    {status?.join?.state === 'blocked' && <p className="mi-form-error" role="status">{status.join.message}</p>}
    {!!pendingRecordings.length && <div className="mi-notice mi-warning"><AlertCircle /><div><strong>A recording is waiting to be archived.</strong><p>The saved audio is still on the station. Finish importing it to start processing.</p>{pendingRecordings.map(recording => <Button key={recording.id} variant="outline" disabled={!!busy || !connected} onClick={() => void stop(recording.id)}>Finish saved recording</Button>)}</div></div>}
    {expanded && <div id="mi-browser-controls" className="mi-browser-controls">
      {!connected ? <p className="mi-muted">Pair this station to open its meeting browser.</p> : <>
        {(!status?.available || pollError) && <div className="mi-notice" role="status"><span>{pollError || status?.message || 'Checking the station browser…'}</span></div>}
        <form className="mi-form" onSubmit={open}>
          <label htmlFor="mi-meeting-link">Meeting link</label>
          <div className="mi-browser-link"><Input id="mi-meeting-link" type="url" required placeholder="https://meet.google.com/abc-defg-hij" value={url} onChange={event => setUrl(event.target.value)} disabled={!!activeId || !!busy} /><Button type="submit" disabled={!available || !!busy || !!activeId || !url.trim()}>{busy === 'open' && <Loader2 className="animate-spin" />}Join &amp; record</Button></div>
          <p className="mi-muted">The station enters as “Meeting Station (recording)” with microphone and camera off. It saves and processes the recording when the call ends. Host admission and account restrictions still apply.</p>
        </form>
        {available && <div className="mi-browser-viewer-actions"><Button variant="ghost" onClick={() => void perform('viewer', showViewer)} disabled={!!busy}>{viewer ? 'Reconnect browser view' : 'Show station browser'}</Button><span>The view controls the Radxa’s browser.</span></div>}
        {viewer && <iframe key={viewerRevision} className="mi-browser-viewer" src={viewer} title="Radxa meeting browser" allow="fullscreen" />}
        <details className="mi-browser-record-options mi-form"><summary>Recording options</summary>
          <div className="mi-form-columns"><label htmlFor="mi-browser-title">Recording title<Input id="mi-browser-title" value={title} maxLength={200} onChange={event => setTitle(event.target.value)} disabled={!!activeId || !!busy} /></label><label htmlFor="mi-browser-language">Meeting language<select id="mi-browser-language" value={language} onChange={event => setLanguage(event.target.value)} disabled={!!activeId || !!busy}><option value="auto">Detect automatically</option><option value="kk_ru">Kazakh / Russian (auto)</option><option value="kk">Kazakh</option><option value="ru">Russian</option><option value="en">English</option></select></label></div>
          <div className="mi-form-columns"><label htmlFor="mi-browser-output">Report language<select id="mi-browser-output" value={outputLanguage} onChange={event => setOutputLanguage(event.target.value)} disabled={!!activeId || !!busy}><option value="same">Same as meeting</option><option value="en">English</option><option value="ru">Russian</option><option value="kk">Kazakh</option></select></label><label className="mi-checkbox"><input type="checkbox" checked={diarization} onChange={event => setDiarization(event.target.checked)} disabled={!!activeId || !!busy || diarizationAvailable === false} /><span>Separate speakers<small>Anonymous voice labels; review speaker identities.</small></span></label></div>
          <p className="mi-muted">The meeting platform uses the internet. Transcription and report generation run locally.</p>
        </details>
      </>}
    </div>}
    {error && <div className="mi-notice mi-warning" role="alert"><AlertCircle /><span>{error}</span></div>}
  </section>
}
