import { useEffect, useMemo, useState } from 'react'
import { BrainCircuit, CheckCircle2, Download, FileAudio, Server, ShieldCheck } from 'lucide-react'
import { Layout } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { downloadExport, getJob, getResult, health, submitMeeting, type JobRecord, type MeetingResult, type WorkerConfig } from './api'

const terminal = new Set(['completed', 'failed', 'cancelled'])

export function MeetingIntelligencePage() {
  const [workerUrl, setWorkerUrl] = useState(localStorage.getItem('mi.workerUrl') || 'http://macbook.local:8765')
  const [token, setToken] = useState(localStorage.getItem('mi.token') || '')
  const [connected, setConnected] = useState(false)
  const [title, setTitle] = useState('Новая встреча')
  const [languageMode, setLanguageMode] = useState('kk_ru')
  const [outputLanguage, setOutputLanguage] = useState('same')
  const [file, setFile] = useState<File>()
  const [transcript, setTranscript] = useState('')
  const [job, setJob] = useState<JobRecord>()
  const [result, setResult] = useState<MeetingResult>()
  const [error, setError] = useState('')
  const config = useMemo<WorkerConfig>(() => ({ url: workerUrl.replace(/\/$/, ''), token }), [workerUrl, token])

  async function testConnection() {
    setError('')
    try {
      await health(config)
      localStorage.setItem('mi.workerUrl', config.url)
      localStorage.setItem('mi.token', token)
      setConnected(true)
    } catch (e) { setConnected(false); setError(String(e)) }
  }

  async function submit() {
    setError(''); setResult(undefined)
    try {
      const created = await submitMeeting(config, { title, languageMode, outputLanguage, file, transcript })
      setJob(created)
    } catch (e) { setError(String(e)) }
  }

  useEffect(() => {
    if (!job || terminal.has(job.stage)) return
    const timer = window.setInterval(async () => {
      try {
        const current = await getJob(config, job.id)
        setJob(current)
        if (current.stage === 'completed') setResult(await getResult(config, current.id))
      } catch (e) { setError(String(e)) }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [job, config])

  return <Layout><div className="space-y-6">
    <div className="flex flex-col gap-2"><div className="flex items-center gap-3"><BrainCircuit className="h-8 w-8 text-orange-500"/><h1 className="text-3xl font-semibold">Meeting Intelligence</h1></div><p className="text-[var(--text-secondary)]">Radxa хранит и показывает данные, Mac выполняет ASR и Qwen локально.</p></div>

    <Card><CardHeader><CardTitle className="flex items-center gap-2"><Server className="h-5 w-5"/>Mac worker</CardTitle></CardHeader><CardContent className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
      <Input aria-label="Worker URL" value={workerUrl} onChange={e => setWorkerUrl(e.target.value)} placeholder="http://macbook.local:8765"/>
      <Input aria-label="Worker token" type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Local API token"/>
      <Button onClick={testConnection}>{connected ? <><CheckCircle2/>Connected</> : 'Connect'}</Button>
    </CardContent></Card>

    <Card><CardHeader><CardTitle className="flex items-center gap-2"><FileAudio className="h-5 w-5"/>Новая обработка</CardTitle></CardHeader><CardContent className="space-y-4">
      <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="Название встречи"/>
      <div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-sm">Язык аудио<select className="w-full rounded-md border bg-transparent p-2" value={languageMode} onChange={e => setLanguageMode(e.target.value)}><option value="kk_ru">Қазақша + русский</option><option value="en">English</option><option value="auto">Auto</option></select></label><label className="space-y-1 text-sm">Язык протокола<select className="w-full rounded-md border bg-transparent p-2" value={outputLanguage} onChange={e => setOutputLanguage(e.target.value)}><option value="same">Как в разговоре</option><option value="kk">Қазақша</option><option value="ru">Русский</option><option value="en">English</option></select></label></div>
      <Input type="file" accept="audio/mp3,audio/wav,audio/x-m4a,audio/mp4,.mp3,.wav,.m4a" onChange={e => setFile(e.target.files?.[0])}/>
      <div className="text-center text-xs text-[var(--text-tertiary)]">или вставьте готовую транскрипцию для быстрой проверки</div>
      <Textarea rows={5} value={transcript} onChange={e => setTranscript(e.target.value)} placeholder="Текст встречи…" disabled={Boolean(file)}/>
      <Button onClick={submit} disabled={!connected || (!file && !transcript.trim())}>Запустить обработку</Button>
      {job && <div className="rounded-lg bg-[var(--secondary)] p-3 text-sm">Статус: <b>{job.stage}</b>{job.error_message && ` — ${job.error_message}`}</div>}
      {error && <div className="rounded-lg bg-red-500/10 p-3 text-sm text-red-600">{error}</div>}
    </CardContent></Card>

    {result && <Protocol result={result} config={config}/>}
  </div></Layout>
}

function Protocol({ result, config }: { result: MeetingResult; config: WorkerConfig }) {
  const protocol = result.protocol
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-2xl font-semibold">{protocol.metadata.title}</h2><div className="flex gap-2">{['json','csv','pdf'].map(format => <Button key={format} variant="outline" onClick={() => downloadExport(config, result.job.id, format)}><Download/>{format.toUpperCase()}</Button>)}</div></div>
    <Card><CardHeader><CardTitle>Executive summary</CardTitle></CardHeader><CardContent><ul className="list-disc space-y-2 pl-5">{protocol.executive_summary.map((line, i) => <li key={i}>{line}</li>)}</ul></CardContent></Card>
    <div className="grid gap-4 lg:grid-cols-2"><Items title="Решения" items={protocol.decisions}/><Items title="Открытые вопросы" items={protocol.open_questions}/><Items title="Риски" items={protocol.risks}/><Card><CardHeader><CardTitle>Action items</CardTitle></CardHeader><CardContent className="space-y-3">{protocol.action_items.map(item => <div key={item.id} className="rounded-lg border p-3"><div className="font-medium">{item.task}</div><div className="text-sm text-[var(--text-secondary)]">{item.assignee || 'Исполнитель не указан'} · {item.deadline_text || 'Без срока'} · {item.priority}</div><Evidence item={item}/></div>)}</CardContent></Card></div>
  </div>
}

function Items({ title, items }: { title: string; items: MeetingResult['protocol']['decisions'] }) {
  return <Card><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent className="space-y-3">{items.length ? items.map(item => <div key={item.id} className="rounded-lg border p-3"><div>{item.text}</div><Evidence item={item}/></div>) : <span className="text-sm text-[var(--text-secondary)]">Не найдено</span>}</CardContent></Card>
}

function Evidence({ item }: { item: { evidence: { quote?: string }; source_check: string } }) {
  return item.evidence.quote ? <div className="mt-2 flex gap-2 text-xs text-[var(--text-secondary)]"><ShieldCheck className="h-4 w-4 shrink-0 text-green-600"/><q>{item.evidence.quote}</q></div> : <div className="mt-2 text-xs text-amber-600">Источник требует проверки: {item.source_check}</div>
}
