# Technical brief: implementation and acceptance

Source: the supplied `TZ_AI_Meeting_Intelligence.pdf`, pages 1–2. This matrix maps
the brief to the deployed station and identifies evidence still required; it
does not award points or treat code coverage as measured model accuracy.

The core demonstration is **upload audio or paste a transcript → local Mac
inference → structured report → downloads from the Radxa archive**. It uses two
user-owned devices on a private LAN. Models, recordings and report generation
remain on those devices. Provisioning downloads are completed beforehand.

## Required features

| Brief requirement | Current implementation | Validation and remaining acceptance |
| --- | --- | --- |
| Local transcription of MP3, WAV and M4A | Station accepts and archives these formats; Mac FFmpeg normalizes audio for local whisper.cpp. Existing text can skip ASR. See [station API](../station/src/meeting_station/main.py) and [worker pipeline](../mac-worker/src/meeting_worker/pipeline.py). | The exact 120-second WAV completed through the deployed pipeline. MP3 and M4A encodings of the earlier synthetic speech fixture also decoded and produced timestamped transcripts. Representative human speech in all formats remains a quality test. |
| Executive summary of 3–5 key sentences | Summary derives up to five supported facts, with aligned source references, after semantic review. See [protocol extraction](../mac-worker/src/meeting_worker/protocol.py). | The exact benchmark produced five source-checked sentences. Sparse speech may yield fewer than three because the worker does not invent filler solely to meet the target. |
| Decisions, topics/key points, open questions | Separate structured collections, chronological reconciliation and source citations; report views and PDF/JSON expose them. The main UI separates failed candidates from source-checked facts. | The benchmark retained the corrected Wednesday launch, local-processing choice, key topic and battery-budget question. A second generated “question” failed its source check and appeared only in the review queue, demonstrating the boundary rather than perfect model classification. |
| Action table: responsible person, task, deadline if stated, priority | Dedicated fields in the [schema](../mac-worker/src/meeting_worker/schemas.py), report and exports. Unknown owners/deadlines remain empty; unspecified priority remains `not_specified`. Relative deadlines retain their spoken form. | The benchmark produced all three expected actions: Alex/Monday/medium, Dana/Tuesday/high and an unassigned support handover with no deadline or priority. It did not preserve the superseded Dana/Friday proposal or invent calendar dates. |
| Download CSV, JSON **or** PDF | All three are generated on the Mac and cached on the Radxa before the job becomes complete. CSV contains actions with review status; JSON contains protocol and transcript; PDF contains the report, evidence and timestamped transcript. | Exports were retrieved through the deployed HTTPS app. A real empty-protocol PDF defect was fixed: it now states the extraction failure and starts the transcript on the first page. Existing archived PDFs were regenerated. Tests cover Unicode, report content and CSV formula escaping. Cached PDF retrieval and preservation after reboot passed. |
| Local ASR and local LLM; no external commercial API requests | Installed Whisper and diarization models; loopback-only Ollama/Qwen; station permits private worker destinations and ignores environment proxies. Station assets are served locally. | The optional meeting browser was stopped for the exact run. Process-level socket inspection showed the worker connected only to the Radxa and loopback Ollama; the Radxa had no established external peer. This is strong boundary evidence, but it is not a physical WAN-disconnection test. |

The model review is another local model judgment, not human confirmation or a
guarantee of correctness. Timestamps, original audio, citations and review flags
make results inspectable. No claim of “100% transcription accuracy” is justified.

## Bonus features

| Brief bonus | Station implementation | Current limit |
| --- | --- | --- |
| Speaker diarization | Optional local Sherpa ONNX segmentation and embeddings; anonymous speaker labels. | Synthetic voice changes and installed-model readiness do not establish real speaker accuracy. Test human speakers and overlap; names are not identified automatically. |
| Russian, Kazakh, English and mixed speech | Multilingual Whisper profile, language selection and Unicode exports. | Recognition and reporting quality need representative recordings in every requested language and code-switching. The prepared two-minute English fixture is not multilingual validation. |
| RAG/chat about a meeting | Not implemented in the deployed station flow. | Upstream Scriberr features do not count as a delivered station integration. |
| Task tracker export or calendar `.ics` | The Mac creates a local RFC 5545 calendar containing source-checked actions and the Radxa caches it beside the report. Explicit calendar dates become `DUE` values; relative spoken deadlines remain notes rather than invented dates. | The deployed benchmark download contains three VTODO entries and excludes the failed candidate. Direct Trello, Notion and Jira integrations are not implemented. |
| Risks and blockers | Structured risks with source evidence and review status. | Verify stated risks are retained, speculative risks are not invented, and open questions remain distinguishable from decisions. |

## Demonstration and scoring evidence

The brief allocates 30 points to required functionality, 20 to AI accuracy,
20 to locality, 15 to bonus features, 10 to UI/performance on a two-minute recording,
and 5 to presentation with a random test recording. These are rubric weights,
not a claim about points earned.

## Latest audit after security changes — 11 September 2026

**The six mandatory workflows are implemented, but full acceptance is not yet
proven.** The 3–5 sentence summary target is conditional on enough supported
facts; empty or uncertain audio can produce fewer. No rubric item justifies
inventing facts to fill the requested structure.

| Area | Latest status |
| --- | --- |
| Mandatory upload/text → local ASR/LLM → structured protocol → downloads | Implemented. The encrypted two-minute WAV run passed with five sourced summary sentences, four accepted decisions and three correctly assigned/unassigned tasks. |
| Privacy/locality | HTTPS, mutual TLS, encrypted station archive, FileVault Mac, rotated tokens and SSH key authentication deployed. Inference stays local; physical WAN-disconnected acceptance remains outstanding. |
| Diarization bonus | Implemented with local models; real speaker error and overlapping speech not measured. |
| Russian/Kazakh/English and mixed speech bonus | Multilingual models and explicit language controls are present. Representative human and code-switching quality remains unvalidated. |
| RAG/chat bonus | **Missing** from the deployed station. |
| Task integration/calendar bonus | Local ICS export implemented. Trello/Notion/Jira are absent; the brief permits a calendar alternative. |
| Risks/blockers bonus | Implemented with evidence and review flags; one risk in the latest fixture remains held for audio review. |
| Two-minute performance and random recording demonstration | Latest encrypted run: **62.567 seconds**, all four exports. A previously unseen human recording still needs to be demonstrated. |
| Actual captured recording | A 152.289-second recording exposed repetition and an empty protocol. Context reset removed the repeated-output loop, but 19 segments still need review and the rerun produced no structured findings. Real-meeting accuracy and completeness therefore remain **unproven**. |
| Unattended startup and joining (outside the rubric) | Boot recovery is installed; an actual reboot restored the website, encrypted archive, Mac connection and browser in 80.745 seconds. Join automation handles name, media controls, request, admission state and call-end archival. Provider restrictions can still prevent admission. |

Encryption is an added privacy control, not a separately listed mandatory feature
in the PDF. Telegram, automatic joining of every meeting platform, live streaming
and a native Mac capture client are also not mandatory PDF requirements. They
must not be counted as delivered merely because prototypes or upstream code exist.

The [accuracy audit](ACCURACY.md) records the actual model comparison, confidence
limitations and required human tests. The [security audit](SECURITY.md) records
the encryption boundaries, tested rejection paths and recovery procedure.

For a reviewable acceptance run:

1. Upload a previously unseen audio file or paste a transcript without preparing
   the model response. Show the archived source, progress and final protocol.
2. Follow report citations into the transcript and audio. Compare all decisions,
   owners, deadlines, priorities and open questions against what was actually said.
3. Download CSV, JSON and PDF; refresh the page and reopen the archived result.
4. Repeat the core workflow with WAN disconnected and the Radxa–Mac LAN intact.
   Keep the optional meeting browser stopped for this demonstration.
5. Measure the full two-minute job from station submission to locally cached
   completed result and exports. Record queue delay, model/profile settings,
   diarization setting, cold/warm state and total elapsed time. Do not combine
   individual stage timings from different runs or extrapolate a short clip.

A private **120-second synthetic English meeting** is retained under
`.local/two-minute-demo/`: `source.wav`, `script.txt` and `manifest.json` include
the exact spoken turns, installed voice names, source hash, audio timing and
expected facts. It includes an owner/deadline correction, a changed launch date,
explicit priorities, an unassigned handover, a rejected cloud proposal, an open
budget question and two stated risks. It is a throughput and regression fixture,
not evidence of performance on noisy human meetings. The deployed Radxa-to-Mac
pipeline completed it in **72.606 seconds**, including local Whisper, diarization,
Qwen extraction, semantic review and all three cached exports. Its SHA-256 is
`ab0c72e1255cf689de7bf882a15b508546c118b853bfbe546d8e637fe18ca716`.
The report contained 40 transcript segments, five summary lines with five aligned
sources, four decisions, one topic, three action items, two verified risks and one
verified open question. One unsupported question candidate was retained in the
separate review queue. The clean synthetic fixture is suitable for repeatable
throughput and correction tests; it does not measure noisy human-meeting accuracy.

## Optional online meeting capture

The brief does not require automatic meeting joining or a Telegram bot. The
Radxa browser is an additional input path: it can open a meeting link, support
manual sign-in/admission through the station viewer, and record its playback
before sending saved audio into the same local pipeline. The Join & record flow
now performs guest entry and records before requesting admission. It distinguishes
waiting, joined, rejected and ended states; rejected captures are retained as
failures rather than normal reports. Platform policies and host admission still
apply. A loaded page or synthetic audio signal does not prove successful admission.

This input path requires internet access to the meeting provider. It therefore
must not be presented as the fully offline workflow. Inference and storage remain
local after capture. The current deployment handles one browser meeting at a
time and transcribes after recording stops; live transcripts, automatic meeting
discovery and Telegram control are not implemented. See the [architecture](ARCHITECTURE.md)
and [deployment runbook](RUNBOOK.md) for the boundaries and restoration procedure.
