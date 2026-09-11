# Hardware validation — 2026-09-11

Tested on a Radxa Cubie A7A with 6 GB RAM and Debian 11, connected over the
private LAN to an Apple M5 Mac with 16 GB unified memory.

The current security/startup/audio revision passes **106 worker/station tests**, the
frontend production build, targeted ESLint, browser join fixtures and deployed
Chrome checks for automatic audio loading, exports, session-only tokens and the
encrypted viewer. An actual Cubie reboot recovered the archive, HTTPS website,
Mac worker link and browser automatically in **80.745 seconds**, preserving a
downloaded PDF byte-for-byte. See [SECURITY.md](SECURITY.md), [ACCURACY.md](ACCURACY.md)
and [REQUIREMENTS.md](REQUIREMENTS.md) for current boundaries and quality gaps.

## Silent Google Meet capture fixed

The station was admitted but Chromium's remote audio elements were paused. One
149-second WAV was entirely zero PCM; another contained 290 seconds of silence
before playback resumed. A growing recording file had incorrectly looked healthy.

The isolated Chromium now permits unattended playback, the CDP interaction uses
a user gesture, and the join loop resumes paused incoming audio elements. A fresh
automatic join showed live, enabled remote audio tracks with `paused: false`.
PulseAudio showed Chromium playing unmuted into the same sink that FFmpeg records.
The saved verification WAV lasts **484.6873125 seconds** and contains nonzero PCM
in **446 one-second windows**, beginning at second 39 after admission. Its SHA-256
is `d7d5936a3e5033f1a40cbad92297a78f6a64292d97a548ea68e0d7e7c31fecb0`.
This verifies actual Meet audio capture; it does not measure transcription WER.

Deployed Chrome decoded and played that same SHA-256-matched recording through
the app's player; the 60–70 second window had normalized RMS 0.0621 and peak
0.7883. The new join's meter showed **Audio received**, backed by live PCM around
−24 dBFS. The completed local pipeline produced 120 timestamped segments; many
remain flagged for review, so this is not a transcription-accuracy acceptance.
The earlier all-zero capture is now marked failed; its source is unchanged and
the prior database state is backed up inside the encrypted vault.

The status endpoint and UI now measure recent saved PCM, distinguish quiet audio
from stalled writes, and show an incoming-audio meter. FFmpeg flushes packets so
buffering does not hide current levels. Completely zero recordings are preserved
as failed captures and are not automatically submitted for a normal report.
Regression checks cover silence, sound, quiet periods, stopped writes and archival
of a failed capture without sending it to inference. Private evidence remains in
`.local/security/audio-fix/`.

The measurements below are historical runs before this revision. In particular,
the old HTTP fallback and ignored-certificate test setup are no longer the active
deployment. Do not use those historical descriptions as configuration instructions.

## Checks completed

- 31 Mac worker tests, including the station-to-worker multipart contract,
  authentication, durable jobs, bounded uploads, source-derived summaries,
  polarity checks, citations and Unicode/PDF exports.
- 53 Python station/native-foundation checks, including disconnected-worker
  recovery and cancellation/retry races.
- Go API/config/server tests, including the station-mode guard that disables
  legacy inference and external-provider routes.
- Frontend TypeScript production build with `VITE_MEETING_STATION=true`, and
  ESLint on changed frontend files.
- Native macOS 15 prototype build, ad-hoc signature check and standalone
  repository/network/audio checks. Full Xcode is not installed on this Mac.

The isolated Playwright browser created the station account, paired its separate
station token, uploaded a synthetic WAV, opened the transcript, loaded the full
archived audio and downloaded a valid PDF. Authenticated audio range requests
returned HTTP 206. JSON, CSV and PDF were cached on the board. The browser's
resource entries contained no external application assets.

With the Mac worker stopped, the board accepted a new transcript and displayed
it as queued. The existing PDF remained downloadable, byte-identical to the
online-worker download. After restarting the worker, the queued transcript
completed automatically. Browser reload and a subsequent board-service restart
retained the archive.

## Real model smoke test

A locally synthesized two-voice recording lasted **16.636 seconds**. With
multilingual Whisper base, Sherpa ONNX diarization and local Qwen3.5 4B, it
produced five timestamped segments, two anonymous speaker groups and a PDF.
The direct worker run took about **34 seconds**; the browser-to-board run took
about **29 seconds** under different warm-cache conditions. These are smoke
measurements, not throughput benchmarks.

The final task reflected the later Monday deadline and changed owner. Whisper
base misheard the spoken name “Timur” as “Timma”; the report preserved that ASR
text. Name accuracy, mixed-language accuracy and diarization quality need
representative meeting audio. Source checks mark unsupported items for review;
they do not establish factual correctness of every generated sentence.

The MP3 and M4A encodings of the same fixture also decoded and produced five
timestamped Whisper segments. No real microphone or system-meeting audio was
captured for these tests.

A subsequent comparison used the identical source and CLI options:

| Model | ASR wall time | Peak process footprint | Name in both mentions |
|---|---:|---:|---|
| Multilingual base | 0.35 s | 361 MiB | Timma |
| Large-v3-turbo q5 | 1.20 s | 829 MiB | Timur |

Both processes reported zero swaps. These are ASR-only measurements on one
clean synthetic English recording. Turbo q5 is now selected on the M5; base
remains available locally. The 547.4 MiB model matched official SHA1
`e050f7970618a659205450ad97eb95a18d69c9ee` from the
[whisper.cpp model manifest](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md).

After activating turbo, a fresh browser upload completed the full station-to-Mac
pipeline and cached exports on the board in **21.752 seconds**. The five-segment
transcript and final task both retained **Timur**, with **Monday** as the deadline
and a passing source check. Cache/model-output differences mean this single run
does not establish a pipeline speed improvement over the earlier base runs.

Actual Metal inference also completed under `deploy/mac/inference-local.sb`.
Separate connection probes allowed this Mac's loopback/interface addresses,
blocked direct connections to the Radxa and a public IP, and allowed a Radxa
request into a temporary Mac server with a successful response. This constrains
direct outbound sockets, not delegated DNS/IPC. A physical WAN-disconnected test
and long/concurrent meetings remain unverified.

The isolated browser accepted the existing Caddy test certificate. Its service
worker registration reported a certificate-trust error; ordinary application
flows worked, but PWA installation/offline caching was not validated. The system
trust store was not changed.

## LAN access and meeting browser

The explicit `http://192.168.8.57/meeting-intelligence` address passed a fresh
Chrome session with account login, station pairing, reload and session refresh.
HTTPS also passed with the board CA explicitly trusted by the test client;
its cookies retained Secure and HttpOnly. HTTP is an unencrypted demo-LAN
fallback and does not change the Mac's system certificate trust.

The isolated Radxa Chromium 152 browser started with its sandbox enabled under
a separate UID. CDP, VNC, websockify and the controller listen only on loopback.
Controller authentication and the relay's token/Origin checks passed. Chromium
played a 440 Hz synthetic oscillator into its private PulseAudio sink; the
controller finalized a 2.7015-second, 86,526-byte mono 16 kHz WAV with RMS 3100.
This verifies browser playback capture, not remote meeting speech.

The new station package passed **45 tests**, including 25 browser-route,
authentication, recovery and controller checks in addition to the original 20.
The deployed UI passed login, pairing, viewer rendering at 1280×800 and explicit
viewer reconnection. The viewer cookie is HttpOnly, SameSite=Strict and scoped
to the viewer path. It cannot authorize the job archive: that request returned
401 without the station Bearer token.

A separate synthetic speech test used the station's browser-recording API and
played the 16.636-second fixture into the isolated PulseAudio sink. The archived
WAV contained 17.5936 seconds including capture margins, 563,072 bytes, RMS
4354.98 and peak amplitude 29849. Local processing completed 24.68 seconds after
Stop, producing five segments, two anonymous speakers and cached PDF/JSON/CSV.
The final task preserved Timur and Monday with a passing source check. This test
also exposed an unsupported executive-summary cancellation claim and a missing
explicit launch decision. A successful pipeline/export test is not evidence of
complete report accuracy.

That report-quality failure prompted a separate fix: the worker discards model
summary prose and derives each summary line from a supported structured item
after semantic verification, with aligned item IDs and evidence. A schema
clarification distinguishes explicit agreed decisions from topic headings.
The worker suite now has **31 passing tests**, including unsupported-summary,
balanced-summary, polarity and reconciliation regressions. A direct real-Qwen rerun recovered the launch
postponement as a decision, retained the Timur/Monday task, and produced exactly
those two supported summary facts. This fixes the observed fixture failure;
it does not establish perfect classification or accuracy on other meetings.

The supplied Google Meet link loaded on the Radxa and accepted the disclosed
participant name at the pre-join screen, with microphone and camera disabled.
Google rejected two guest attempts with “You can't join this video call” and
HTTP 403, “The caller does not have permission,” from its meeting-device
endpoint, including a retry after the user confirmed the host was present and
guest access was enabled. The participant was **not admitted** and
no real meeting audio was captured. A host/account access change is still
needed for that acceptance test. Zoom and Teams joins have not been tested.

The user's normal Chrome session still showed `ERR_ADDRESS_UNREACHABLE` despite
its stored permission entry appearing allowed. It was running an older Chrome
version than the installed browser used by the isolated tests. The station was
then opened in Safari, and the user confirmed that its login page loaded. No
macOS privacy permissions were changed.

## Exact two-minute rubric run

The final deployed acceptance fixture is exactly **120.000 seconds** of 16 kHz
mono PCM synthetic English speech. Its SHA-256 is
`ab0c72e1255cf689de7bf882a15b508546c118b853bfbe546d8e637fe18ca716`.
The complete station upload, Mac Whisper transcription, Sherpa diarization,
local Qwen3.5 extraction and semantic checking, and board-cached exports finished
in **72.606 seconds**. The observed milestones were 3.449 seconds to
transcribing, 7.557 to diarizing, 13.782 to extracting and 72.506 to completed.
The result had 40 timestamped transcript segments, five summary sentences with
five aligned evidence sources, four decisions, one topic, three actions, two
risks and two generated question candidates. PDF, JSON and CSV sizes were
37,533, 20,700 and 1,038 bytes.

Manual comparison with the fixture manifest found the three intended actions:
Alex owns the Monday medium-priority rollout checklist, Dana owns the Tuesday
high-priority upload/recovery tests, and the agreed support handover has no owner
or deadline. The report retained the Wednesday launch, local-processing choice,
open battery-budget question and both stated risks. It did not turn the earlier
Dana/Friday proposal into the final assignment, adopt cloud processing, approve
a battery purchase or invent an ISO calendar date.

The model also classified “Battery purchase was not approved” as a second open
question with a mismatched excerpt. The semantic checker marked it failed and
`needs_review`; it was excluded from the executive summary and the main verified
question list. The custom UI places it in a separate **AI suggestions to review**
section. This is evidence that source checks contain an observed error; it is
not a claim of perfect extraction.

The optional online-meeting browser was stopped for this run. During processing,
the Mac worker had an established connection to the Radxa and used Ollama only
on loopback. The Radxa showed the station-to-Mac connection and local service
listeners, with no established external peer. This process-level observation
supports the local data path, while a physically disconnected WAN test remains
outstanding.

After deploying the custom Meeting Station frontend, a clean browser session
passed login, station pairing and archive selection. The selected benchmark
showed five summary lines, Topics & key points, the Owner/Task/Deadline/Priority
table, one verified open question and one separate review candidate. The browser
also downloaded the calendar export successfully. No visible
Scriberr text appeared in the station UI. The check also captured the full report
at desktop size and confirmed the generated page did not overflow horizontally.

The bonus calendar export was added after the timed run, so its generation is not
included in the 72.606-second measurement. The benchmark calendar was generated
locally and cached on the Radxa: it is 1,553 bytes, uses RFC 5545 CRLF line endings,
contains three source-checked VTODO entries and excludes the failed candidate.

The fixture uses clean synthesized voices with no overlap, room noise, human
accents or language switching. Its timing is a repeatable performance result,
not a word-error-rate or real-meeting accuracy measurement. An earlier exact run
with a 16K Qwen context failed visibly after 194.278 seconds when the accumulated
protocol no longer fit; the worker now uses a 32K default and the final run above
completed successfully.

## Carelink preservation

Both private archives passed full gzip CRC and saved SHA-256 verification.
Each contains 14,036 regular files, with per-file SHA-256 manifests. The
consistent archive was taken while the Carelink gateway was stopped and the
gateway was restarted afterward. These are application/configuration backups,
not disk images.

The rollback exercise started Carelink successfully, returned HTTP 200 from its
original health endpoint and restored a byte-identical original Caddyfile.
Meeting Station was then reactivated. Carelink code/data, firewall, SSH and OS
remain in place. The original configuration snapshot is never overwritten by
subsequent station activations.

Generated recordings, reports, browser logs, model hashes, credentials and
Carelink backups remain in ignored private workspace directories.
