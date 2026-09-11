import { useEffect, useState, type FormEvent } from 'react'
import { AlertCircle, ChevronDown, ChevronUp, Loader2, Monitor, Radio, Square, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { browserSession, browserStatus, openMeetingBrowser, startBrowserRecording, stopBrowserRecording, type BrowserStatus, type JobRecord, type WorkerConfig } from './api'

function describe(error: unknown) { return error instanceof Error ? error.message : 'The meeting browser could not complete this request.' }

export function MeetingBrowserPanel({ config, connected, jobs, onCreated, diarizationAvailable }: {
  config: WorkerConfig; connected: boolean; jobs: JobRecord[]; onCreated: (job: JobRecord) => void; diarizationAvailable?: boolean
}) {
  const [status, setStatus] = useState<BrowserStatus>()
  const [expanded, setExpanded] = useState(false)
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
      setStatus(await openMeetingBrowser(config, url.trim()))
      await showViewer()
    })
  }
  const activeId = status?.active_recording_id
  const pendingRecordings = (status?.recordings || []).filter(recording => recording.state !== 'recording' && jobs.some(job => job.id === recording.id && !job.station?.archived))
  const available = connected && status?.available && !pollError
  const start = () => perform('start', async () => {
    onCreated(await startBrowserRecording(config, { title: title.trim() || 'Online meeting', language_mode: language, output_language: outputLanguage, diarization }))
  })
  const stop = (id: string) => perform('stop', async () => { onCreated(await stopBrowserRecording(config, id)) })

  return <section className="mi-browser-panel" aria-label="Online meeting browser">
    <div className="mi-browser-heading">
      <div className="mi-browser-icon"><Video /></div>
      <div><div className="mi-eyebrow">OPTIONAL · ONLINE CAPTURE</div><h2>Bring your station into the call.</h2><p>Meeting platforms need internet. Recording uploads and AI processing work locally.</p></div>
      <Button variant="outline" onClick={() => setExpanded(value => !value)} aria-expanded={expanded} aria-controls="mi-browser-controls">
        {expanded ? <ChevronUp /> : <ChevronDown />}{expanded ? 'Hide controls' : 'Join online meeting'}
      </Button>
    </div>
    {activeId && <div className="mi-recording-banner" role="status"><span className="mi-recording-dot" /><div><strong>Recording meeting audio</strong><small>Incoming sound is being saved on the Radxa. Stop to transcribe and create the report.</small></div><Button variant="outline" disabled={!!busy || !connected} onClick={() => void stop(activeId)}>{busy === 'stop' ? <Loader2 className="animate-spin" /> : <Square />}Stop &amp; process meeting</Button></div>}
    {!!pendingRecordings.length && <div className="mi-notice mi-warning"><AlertCircle /><div><strong>A recording is waiting to be archived.</strong><p>The saved audio is still on the station. Finish importing it to start processing.</p>{pendingRecordings.map(recording => <Button key={recording.id} variant="outline" disabled={!!busy || !connected} onClick={() => void stop(recording.id)}>Finish saved recording</Button>)}</div></div>}
    {expanded && <div id="mi-browser-controls" className="mi-browser-controls">
      {!connected ? <p className="mi-muted">Pair this station to open its meeting browser.</p> : <>
        {(!status?.available || pollError) && <div className="mi-notice" role="status"><Monitor /><span>{pollError || status?.message || 'Checking the station browser…'}</span></div>}
        <form className="mi-form" onSubmit={open}>
          <label htmlFor="mi-meeting-link">Meeting link</label>
          <div className="mi-browser-link"><Input id="mi-meeting-link" type="url" required placeholder="https://meet.google.com/abc-defg-hij" value={url} onChange={event => setUrl(event.target.value)} disabled={!!activeId || !!busy} /><Button type="submit" disabled={!available || !!busy || !!activeId || !url.trim()}>{busy === 'open' ? <Loader2 className="animate-spin" /> : <Video />}Open meeting on Radxa</Button></div>
          <p className="mi-muted">Google Meet is the primary demo path. Zoom and Teams links open their browser join pages; account and host policies may require extra steps.</p>
        </form>
        {status?.state === 'opened' && <div className="mi-browser-instructions"><strong>Complete joining in the station browser.</strong><p>Use “Meeting Station (recording)” as the participant name, keep its camera and microphone off, and wait for the host to admit it. Start recording after joining.</p></div>}
        {available && <div className="mi-browser-viewer-actions"><Button variant="ghost" onClick={() => void perform('viewer', showViewer)} disabled={!!busy}><Monitor />{viewer ? 'Reconnect browser view' : 'Show station browser'}</Button><span>The view controls the Radxa’s browser.</span></div>}
        {viewer && <iframe key={viewerRevision} className="mi-browser-viewer" src={viewer} title="Radxa meeting browser" allow="fullscreen" />}
        <div className="mi-browser-record-options mi-form">
          <div className="mi-form-columns"><label htmlFor="mi-browser-title">Recording title<Input id="mi-browser-title" value={title} maxLength={200} onChange={event => setTitle(event.target.value)} disabled={!!activeId || !!busy} /></label><label htmlFor="mi-browser-language">Meeting language<select id="mi-browser-language" value={language} onChange={event => setLanguage(event.target.value)} disabled={!!activeId || !!busy}><option value="auto">Detect automatically</option><option value="kk_ru">Kazakh / Russian</option><option value="en">English</option></select></label></div>
          <div className="mi-form-columns"><label htmlFor="mi-browser-output">Report language<select id="mi-browser-output" value={outputLanguage} onChange={event => setOutputLanguage(event.target.value)} disabled={!!activeId || !!busy}><option value="same">Same as meeting</option><option value="en">English</option><option value="ru">Russian</option><option value="kk">Kazakh</option></select></label><label className="mi-checkbox"><input type="checkbox" checked={diarization} onChange={event => setDiarization(event.target.checked)} disabled={!!activeId || !!busy || diarizationAvailable === false} /><span>Separate speakers<small>Anonymous voice labels; review speaker identities.</small></span></label></div>
          <div className="mi-browser-record-actions"><Button disabled={!available || !!busy || !!activeId || status?.state !== 'opened'} onClick={() => void start()}>{busy === 'start' ? <Loader2 className="animate-spin" /> : <Radio />}Start meeting recording</Button><span>The meeting uses the platform’s internet connection. Transcription and AI run locally after recording stops.</span></div>
        </div>
      </>}
    </div>}
    {error && <div className="mi-notice mi-warning" role="alert"><AlertCircle /><span>{error}</span></div>}
  </section>
}
