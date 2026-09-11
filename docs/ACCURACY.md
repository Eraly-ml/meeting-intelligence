# Transcription accuracy audit — 11 September 2026

There is no evidence for a 100% transcription claim. Accuracy must be measured
on recordings resembling the judges' material, including names, numbers,
overlapping voices and Kazakh/Russian code-switching.

## Changes deployed

- Full multilingual Whisper large-v3 is installed locally, with its SHA-1 checked
  against the upstream model manifest:
  `ad82bf6a9043ceed055076d0fd39f5f186ff8062`.
- Explicitly English jobs use turbo q5, which performed better on the available
  English fixture. Other languages and automatic detection use full large-v3.
  This is a provisional profile choice, not proof of superiority on real meetings.
- The language controls now distinguish Kazakh, Russian, English and automatic
  detection. The mixed-language option uses automatic recognition; it does not
  guarantee correct detection of every language switch.
- Imported audio can include up to 400 characters of names/terms as spelling
  hints. Whisper receives these directly; they can bias recognition and are not
  evidence that those words were spoken. Raw ASR text is retained without LLM
  rewriting.
- Whisper runs with beam size 5 and full token JSON. Token probabilities remain
  in the transcript and JSON export. Alphanumeric fragments below 0.6 receive
  a dotted underline and a “Check audio” marker. These probabilities are
  uncalibrated model signals, not percentages of correctness or guaranteed word
  boundaries. Unmarked words can still be wrong.
- Recognition uncertainty is a separate `audio_warning`, shown as Check audio.
  Source-supported findings and summaries remain visible with their citations;
  missing evidence or failed semantic verification still requires review.
  Calendar tasks with uncertain audio remain excluded without human confirmation.
  Matching a claim to a transcript does not establish that it matches audio.
- A final local Qwen pass generates the meeting title, coherent summary and topics
  from the complete chronological transcript. Each summary sentence is separately
  source-checked. Large discussions carry earlier themes into later chunks.
  Reports preserve the requested English, Russian or Kazakh language, including
  PDF headings and action table labels. Raw transcription is never rewritten by
  the summarizer.
- Token arrays are excluded from Qwen prompts to preserve context for actual
  meeting content. Original audio and segment timestamps remain available for
  checking the wording.
- Local Silero VAD 6.2.0 is provisioned with SHA-256
  `2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987`.
  Whisper uses `-mc 0` to prevent previous decoded text from conditioning later
  audio windows into a loop. This can also reduce helpful cross-window context;
  it needs further testing on names and long multilingual meetings.
- Repeated multiword segments are retained and flagged for review. PDFs include
  timestamped transcripts. When extraction produces no findings, the first page
  says so and begins the transcript instead of printing empty section headings.
  Original audio loads automatically in the meeting view.

## Failure found in an actual recording

A separate capture defect was also found: Chromium had paused incoming Google
Meet audio while FFmpeg kept saving silence. That playback path is fixed, and a
new automatic join produced sustained nonzero audio in the archived WAV. See
[the capture validation](VALIDATION.md#silent-google-meet-capture-fixed). Audio
levels establish captured sound, not correct words or speaker identities. Missing
speech in earlier silent intervals cannot be reconstructed by changing ASR models.

An archived 152.289-second browser recording had an empty structured protocol
and a one-page PDF with only about 130 extracted characters. Its old transcript
contained an extended recognition loop. That export defect was confirmed and
the archived PDF was repaired; original report copies remain private.

Full large-v3 plus VAD alone still repeated one phrase 35 times. Disabling past
text context reduced the maximum identical-segment count to two and produced
36 segments in 19.581 seconds. The full encrypted pipeline rerun completed in
31.171 seconds. **Nineteen segments still require review, and no structured
meeting findings were extracted.** Fewer repetitions do not prove the new words
are correct. There is no human reference transcript for this recording, so no
WER or completeness claim is made. The original source and both recognition
attempts are retained under `.local/security/empty-report/`.

The revised VAD/context settings also transcribed the 352-word synthetic English
reference with zero word errors in 4.859 seconds, producing 43 segments with
three review flags. This remains a clean-fixture regression check only.

## Same-recording comparison

Both models transcribed the same 120-second synthetic English recording, with
language `en`, no spelling hints, beam 5, local whisper.cpp and the same Mac M5.
The reference is the exact text supplied to the synthetic voices, without
speaker-name labels. Case and punctuation are ignored; letters and numbers
remain significant. The reference contains 352 normalized words.

| Local model | ASR elapsed | Substitutions / deletions / insertions | WER | Segments flagged |
| --- | ---: | --- | ---: | ---: |
| large-v3-turbo q5 | 4.855 s | 0 / 0 / 0 | 0% | 3 / 40 |
| full large-v3 | 19.005 s | 0 / 0 / 1 | 0.284% | 5 / 39 |

Full large-v3 repeated “no” once. The clean result for turbo is a measurement on
this synthetic fixture only. The three flagged turbo passages also demonstrate
that the review heuristic produces false positives. These timings measure ASR
including local decoding, not the complete report pipeline; they are single
runs and do not establish cold-start distributions or hardware scaling.

Private measurement files are in `.local/security/asr-comparison.json`, with both
raw transcripts beside it. The fixture and exact source text are retained in
`.local/two-minute-demo/`. Its audio SHA-256 is
`ab0c72e1255cf689de7bf882a15b508546c118b853bfbe546d8e637fe18ca716`.

To score another locally transcribed recording against a human reference:

```sh
python3 scripts/measure-wer.py reference.txt hypothesis.txt
```

WER is `(substitutions + deletions + insertions) / reference_words`. It can exceed
100% when a model inserts many words. It does not measure correct speakers or
the semantic accuracy of the final decisions and tasks.

## Complete encrypted pipeline before the VAD/export revision

The same exact two-minute fixture completed in **62.567 seconds** from HTTPS
submission to retrieved result and all four exports. It used mutual TLS, encrypted
Radxa storage, English turbo, local diarization and Qwen3.5 4B with semantic review.
The optional meeting browser was stopped. This was not a physical WAN-disconnect
test or a controlled comparison of encryption overhead.

The result has five summary sentences with aligned sources, four source-checked
decisions, one topic and three source-checked tasks: Alex/Monday/medium,
Dana/Tuesday/high, and an unassigned handover without invented deadline/priority.
It has two risks and two question candidates; the topic, one risk and one question
remain in Needs review. The topic is held because its cited segment contains a
low-confidence fragment, illustrating the old rule's false positives. New reports
show that uncertainty separately instead of automatically hiding the finding.
Forty transcript segments preserve three recognition warnings.
The corrected owner and deadline were retained instead of the superseded proposal.

The cached exports were PDF 37,439 bytes, JSON 60,207 bytes, CSV 903 bytes and ICS
1,392 bytes at measurement time. Archived PDFs were subsequently regenerated
with transcript appendices. Private benchmark artifacts are in
`.local/security/secure-benchmark/`.

## Evidence still needed

Use an unseen human recording with a human-checked transcript in English,
Russian, Kazakh and mixed speech. Measure WER separately for each language and
count exact errors in names, dates, quantities, owners and deadlines. Include
accented speech, noise, interruptions and overlapping speakers. Measure speaker
diarization error independently of ASR. Compare profile and spelling-hint choices
on held-out samples rather than tuning and scoring the same recording.

The local semantic verifier can omit a fact, miscategorize it, or accept a wrong
paraphrase. There is no delivered human correction/approval workflow; the review
queue and timestamp links currently support manual inspection. Speaker labels
are anonymous clusters, not verified identities.

Model documentation: [OpenAI Whisper large-v3 model card](https://huggingface.co/openai/whisper-large-v3),
[whisper.cpp CLI options](https://github.com/ggml-org/whisper.cpp/blob/master/examples/cli/README.md),
[official model checksums](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md).
