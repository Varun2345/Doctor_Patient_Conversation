# Test conversations for speaker labelling

Synthetic consultations. Made-up dialogue spoken by Microsoft neural text-to-speech voices,
no real people. Regenerate with `python tests/make_test_conversations.py`.

| File | Length | Speakers (voice) | Languages |
|---|---|---|---|
| `three-speakers-english.wav` | 67.5 s | Doctor (Indian English, male), Patient (Indian English, female), Son (British English, male) | English |
| `three-speakers-multilingual.wav` | 59.4 s | Doctor (Indian English, male), Patient (Hindi, female), Relative (Telugu, male) | English / Hindi / Telugu |

Each has a `.script.md` (the lines with timings, plus English translations) and a `.truth.json`
(exact start/end of every turn), so speaker labels can be **scored**, not just eyeballed.

## Using them in the app

**Option A – play them into the microphone.** Start a consultation, press Start, play the WAV
from a phone or speaker near the microphone, then press Stop. This also tests the microphone
and room.

**Option B – feed the file in as the microphone** (exact, no room noise). Open a separate
Chrome or Edge window with the file as a fake microphone:

```bash
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --user-data-dir=%TEMP%\fake-mic --use-fake-ui-for-media-stream --use-fake-device-for-media-stream --use-file-for-fake-audio-capture="D:\ANLP\Prep\consult-recorder\test-audio\three-speakers-english.wav" http://localhost:8765
```

The file starts playing into the "microphone" when you press Start. Let it run about 70 s,
then press Stop.

## Scoring outside the app

```bash
python tests/eval_diarization.py test-audio/three-speakers-english.wav
python tests/eval_diarization.py test-audio/three-speakers-multilingual.wav
python tests/sweep_diarization.py          # compares speaker models / settings
```

## Results (15 Nov 2026, faster-whisper `base`, sherpa-onnx WeSpeaker ResNet34)

| | English file | Mixed-language file |
|---|---|---|
| Speakers found | 3 (should be 3) | 2 (should be 3) |
| Turns labelled correctly | 9 / 13 | 9 / 13 |
| What went wrong | the two **male** voices (Doctor, Son) were merged; the patient was split in two | the two **male** voices (Doctor, Telugu relative) were merged |

Setting the number of speakers to 3 did not help. Four other open speaker models and three
clustering thresholds were also tried (`tests/output/diarization-sweep.txt`). None separated
the two male voices reliably: the "best" scores (11/13) came from splitting 3 people into 5
labels. **Synthetic TTS voices are likely more alike than real people**, so the default model
was not changed on this evidence. Record the same scripts read aloud by three real people to
judge it properly.

**Transcription of the mixed-language file:**
- *One language per recording* (the old behaviour): Whisper chose Hindi for the whole file,
  translated the Hindi lines into English, dropped every Telugu line, and repeated itself at
  the end.
- *Language detected per segment* (now the default, `WHISPER_MULTILINGUAL=true`): each line
  stays in its own language. Hindi and Telugu come out romanized, e.g.
  "Irojo udayam jwaram ledhu" for ఈ రోజు ఉదయం జ్వరం లేదు. 12 of 13 lines were captured.
  A larger model (`WHISPER_MODEL=small` or `medium`) should improve accuracy and use native scripts.
