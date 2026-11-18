# Consultation Recorder (hospital pilot)

A lightweight web app for recording whole doctor–patient consultations. A doctor signs in,
picks or adds the patient, presses **Start recording** once and **Stop & save** once.
The original audio is the main output. A basic local transcript is optional and secondary.

This is a data-collection tool. It does **not** translate, use text-to-speech, write clinical
notes or summaries, or infer who is the doctor and who is the patient.

---

## 0. Status & handover

**Works and tested** (`tests/e2e_test.py`, 40/40): sign-in/sign-up, patient required before
recording, one-tap recording with crash/outage recovery, audio stored once (GridFS), Whisper
transcript after the visit (English/Hindi/Telugu, line-by-line editing that overwrites the
stored transcript), anonymous speaker labels, per-doctor access.

**Current defaults** (`.env`): `WHISPER_MODEL=small`, `WHISPER_LANGUAGES=en,hi,te`,
`WHISPER_DEVICE=cpu`, `DIARIZATION_ENGINE=sherpa-onnx`.

**Not in the repo (create locally):**
* `.env`: copy `.env.example`; ask the team for the test MongoDB URI. Never commit it.
* `models/`: Whisper downloads itself on first use. For sherpa-onnx speaker labels run
  `python -m app.manage download-diarization-models`.
* Test account: `python -m app.manage create-doctor --email test.doctor@pilot.local --name "Dr. Test Doctor"`,
  then write `tests/output/test-account.env` with `TEST_DOCTOR_EMAIL=` and `TEST_DOCTOR_PASSWORD=`.
  `tests/reset_test_data.py` only deletes that account's data.

Tools: `tests/bench_asr.py` (Whisper models: speed and accuracy), `tests/score_diarization.py`
and `tests/sweep_diarization.py` (speaker labels), `tests/record_test_audio.py` (records
`test-audio/*.wav` through the running app), `tests/make_test_conversations.py` (regenerates them).

---

## 1. Run it (no Docker needed)

Needs Python 3.10+ and a MongoDB the app can reach. Use any current Chrome or Edge
on the recording device. Firefox should also work but hasn't been tested.

```bash
cd consult-recorder
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-transcription.txt   # optional: local transcription
copy .env.example .env            # Linux/macOS: cp .env.example .env  - then edit it
python -m app.manage check        # shows storage location, DB, warnings
python -m app.manage create-doctor --email doctor@hospital.org --name "Dr. Name"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000> and sign in.

For a separate hospital installation, start with `docs/hospital.env.example` and see
`docs/hospital-handover.md` for credential handling, offline models, local MongoDB,
and backup considerations. Do not distribute the developer's `.env`.

To replace Atlas with an empty local database without copying existing data, follow
[the local MongoDB setup guide](docs/local-mongodb-setup.md).

### App branding

Set the header title and subtitle in `.env`, then restart the server:

```dotenv
APP_TITLE=ClinCord
APP_SUBTITLE=Clinical Conversation Recording
```

The name appears in the browser tab, header, and sign-in/sign-up pages. Set
`APP_SUBTITLE=` to hide the subtitle. The waveform emblem is `branding/logo.svg`;
replace it to customize the logo. The configured `BRANDING_DIR` is searched for
`logo.svg`, `logo.png`, `logo.jpg`, `logo.jpeg`, then `logo.webp`, in that order.

### Using it from other machines on the hospital network

Browsers only allow microphone access on **HTTPS** or on `localhost`. To record from a
tablet or another PC, put the app behind HTTPS, using the hospital's existing reverse proxy
or certificate, or IIS/nginx/Caddy. Then set `COOKIE_SECURE=true`. Example with uvicorn
directly:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

To keep it running after reboots, register the uvicorn command as a Windows service
(e.g. NSSM) or as a `systemd` unit on Linux. Run a single worker process (no `--workers N`):
the background transcription queue lives in that process.

### Doctor accounts

Doctors can **create their own account** from the sign-up page (link on the sign-in screen,
or `/#/signup`). A new account starts empty and can only see its own records.

* `SIGNUP_CODE=<code>`: sign-up then requires this code. **Recommended for the hospital**: give
  the code only to pilot doctors. Wrong codes are rate-limited.
* `SIGNUP_ENABLED=false`: turns sign-up off. The link disappears, the API refuses, and accounts
  come only from the commands below.
* With sign-up open and no code, anyone who can reach the server can create an account. They
  can't see anyone else's data, but they could record into the system. The server logs a
  warning in this configuration.

The administrator can also manage accounts:

| Command | Purpose |
|---|---|
| `python -m app.manage create-doctor --email … --name …` | new account (prompts for password) |
| `python -m app.manage set-password --email …` | reset a password |
| `python -m app.manage deactivate-doctor --email …` | block sign-in (data is kept) |
| `python -m app.manage list-doctors` | list accounts |
| `python -m app.manage import-doctors --source-db opd_conversation_assistant` | copy accounts (bcrypt hashes) from the earlier prototype database |
| `python -m app.manage download-model` | fetch the transcription model once (weights only) |

---

## 2. Where the audio goes

```
Browser (MediaRecorder, one continuous stream, Opus 128 kbps mono)
   │  every 5 s: part N  →  saved on the device (IndexedDB)  →  PUT to server (retried until it succeeds)
   ▼
App server  DATA_DIR/recordings/<consultationId>/chunks/  (staging, local disk)
   │  on Stop: parts 0..N joined in order → ONE original file, size + SHA-256 checked
   ▼
Long-term store (AUDIO_STORE)
   gridfs → MongoDB GridFS bucket "audio" in MONGO_DB_NAME   (default)
   disk   → DATA_DIR/recordings/<consultationId>/original.webm (read-only)
```

* **One recording per consultation.** The 5-second parts are an internal upload detail.
  They are byte pieces of one continuous recording and are joined back into a single file.
  If any part is missing, the consultation is marked *incomplete* and the missing parts are
  listed. It is never shown as saved.
* **"Saved" means the server confirmed it.** The app shows "Consultation saved" only after the
  server has joined and checksummed the file. If an upload or save fails, the doctor sees
  "Recording NOT fully saved", with **Retry** and **Download device copy**.
* **Network or server outages don't stop the recording.** Parts wait on the device and upload
  when the server is back. If the tab is closed or the browser crashes, the Home screen on
  that device offers **Upload and save now** for the copy it kept. At most the last ~5 seconds
  before a crash can be lost.
* **The original is never modified.** It is stored once, read-only, with its SHA-256. After
  each processing run the checksum is re-verified, and the result is shown on the
  consultation page.
* **Only the original is kept.** There is no processed or "enhanced" copy; the consultation
  page offers the original recording and the transcript.
* **Capture settings.** By default the browser's echo cancellation, noise suppression and
  auto-gain are turned **off** (`AUDIO_CAPTURE_PROCESSING=raw`), so the original is the
  microphone signal as captured. Switch to `browser` if in-room tests show it's clearer.

MongoDB collections in `MONGO_DB_NAME`: `doctors`, `patients`, `consultations`, plus
`audio.files` / `audio.chunks` (GridFS). Field names follow the earlier prototype
(`doctorId`, `patientId`, `fullName`, camelCase, `DOC-…` / `PAT-…` IDs, bcrypt hashes).

---

## 3. Transcription and speaker labels (optional, local only)

* `TRANSCRIPTION_ENGINE=faster-whisper` runs Whisper **on this server**. No audio or text is
  sent anywhere. It runs after the recording is saved, so it can never interrupt a recording.
  Long audio is processed in windows internally and the whole file is covered.
* For a fresh installation with no models, keep `HF_HUB_OFFLINE=0` and run
  `python -m app.manage download-model` and `python -m app.manage download-diarization-models`.
  These public downloads require internet access but no account or token. After both
  succeed, set `HF_HUB_OFFLINE=1` and restart for cached-only Whisper loading.
  Alternatively, copy the complete model cache and ONNX files into the new installation.
  See `docs/hospital-handover.md` for the setup sequence.
* Doctors can edit the transcript. There is **one stored transcript per consultation**
  (`transcript.segments` + `transcript.text`): saving an edit replaces it, and the page shows
  only that latest version (with "Last edited by …"). Earlier versions are not kept. The audio
  is never changed. The transcript appears only on the consultation page, never on the
  recording screen.
* **Mixed languages:** `WHISPER_MULTILINGUAL=true` (default) detects the language for each
  segment, so an English-speaking doctor and a Hindi- or Telugu-speaking patient each keep
  their own language in the transcript. See `test-audio/README.md` for the comparison.
* No external transcription API exists anywhere in the code.

### Choosing the Whisper model 

Settings: `WHISPER_MODEL`, `WHISPER_DEVICE=auto` (GPU if available), `WHISPER_LANGUAGES=en,hi,te`.

**Language handling.** Each part of the recording (split at pauses) is assigned the most
likely of the *allowed* languages only, then transcribed in that language. Every line is
tagged EN / HI / TE. Without this, Whisper picked one language for the whole file
(translating Hindi and dropping Telugu), or labelled short segments as Chinese or Urdu.
Lines that still come out as a jumble of scripts are shown as
"[unclear – listen to the recording]" instead of nonsense.

Benchmark on the two synthetic test conversations (`tests/bench_asr.py`, Intel i5-13450HX,
CPU int8). RTF = processing time ÷ audio length.

| Model | English WER | Mixed file: right language | Hindi | Telugu | CPU speed (RTF) |
|---|---|---|---|---|---|
| base | 4–5% | 11/13 | Urdu script – unusable | Urdu script – unusable | 0.3 |
| **small** (current) | 3% | 12/13 | **16% CER, Devanagari** | phonetic, in Devanagari | 0.7 (English) – 1.1 (mixed) |
| large-v3-turbo | 3% | – | – | – | **6.1** (ran out of memory on the mixed file) |

* **On CPU, `small` is the practical choice.** A 10-minute consultation takes about
  7–11 minutes in the background. The doctor does not wait; they can start the next
  consultation straight away. Hindi is good; Telugu is understandable but not in Telugu script.
* **Larger models need a GPU.** large-v3-turbo on CPU was 6× slower than real time
  (about an hour for a 10-minute consultation). With an NVIDIA GPU it should be far faster.
  Published figures put turbo near large-v2 quality and several times faster than large-v3
  ([1](https://medium.com/@bnjmn_marie/whisper-large-v3-turbo-as-good-as-large-v2-but-6x-faster-97f0803fa933)).
  Not yet measured here: the CUDA libraries (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) were
  still downloading.
* **For Indian languages specifically**, AI4Bharat's IndicConformer (MIT licence, 22 Indian
  languages, [model card](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual))
  is a candidate for the Hindi/Telugu parts, with Whisper kept for English. It needs PyTorch
  and has not been evaluated here.
* A bigger Whisper does **not** change speaker labels; those come from the separate
  diarization model below.

### Speaker labels (diarization)

Open-source Whisper does not identify speakers. OpenAI's diarizing model
(`gpt-4o-transcribe-diarize`) works only by uploading audio to OpenAI, so it is not used.
This app adds speaker labels **locally** with [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
(CPU, ONNX Runtime; no PyTorch, no Hugging Face token):

```bash
pip install -r requirements-diarization.txt
python -m app.manage download-diarization-models   # one-time: two model files, ~34 MB
# .env:  DIARIZATION_ENGINE=sherpa-onnx
```

How it works, after the recording is saved:

1. Whisper transcribes the single original file with **word-level timestamps**.
2. sherpa-onnx works out *who spoke when* from the same file: pyannote segmentation 3.0
   plus WeSpeaker voice embeddings, clustered into speakers.
3. Each word goes to the speaker talking at that moment. A transcript line is **split where
   the speaker changes**, even mid-sentence. Single very short flips are smoothed away.
4. The transcript appears once both steps are done, as
   `[00:15] Speaker B: Only paracetamol, twice a day.` A summary line shows how long each
   speaker talked.

* **The audio is still ONE file.** Labels are text attached to the transcript, and nothing is
  cut per speaker. The speaker timeline is kept in `diarization.turns`.
* Labels are **anonymous** (Speaker A, B, C... in order of first speaking). The app never
  says which one is the doctor or the patient.
* **Editing** is line by line: each line has its time (click it to play the recording from
  there), a speaker drop-down, and its text. Renaming a speaker once (for example
  "Speaker A" to "Doctor") changes every one of its lines. Saving overwrites the stored
  transcript; the audio is never changed.
* While transcription runs, the consultation page checks quietly in the background and
  redraws **once**, when the transcript and speaker labels are both ready.
* The diarization engine estimates the number of speakers automatically; no speaker
  count needs to be entered in `.env`. Automatic counting can still merge similar voices.
* Public Whisper weights are downloaded from Hugging Face, and sherpa-onnx models
  from GitHub. Neither requires an account or access token. No hosted transcription
  fallback is used.
* Diarization runs in a **separate low-priority process**, because sherpa-onnx would
  otherwise freeze the web server (and uploads from doctors who are recording) while it works.
* Speed on this test PC: about 20 s of speaker labelling for 80 s of audio (8 threads),
  so roughly 7-8 minutes for a 30-minute consultation. It happens in the background.
* If speaker labelling fails, the transcript is still shown, without labels, and the
  failure is noted. The audio is never affected.
* Known limits: when two people talk **at the same time** into one microphone, those words
  get one label. Similar-sounding voices or a third person speaking briefly can be merged
  or mislabelled. Accuracy must be checked on real consultations.

---

## 4. Access and privacy model

* Doctors sign in with email + password: bcrypt hashes and a signed, HTTP-only, SameSite=strict
  session cookie that expires after `SESSION_HOURS`. Failed sign-ins are rate-limited.
* **Each doctor sees only their own patients and consultations.** Another doctor's records
  return "not found". There is no admin, department, or shared view.
* Recordings and transcripts cannot be deleted from the app.
* **A patient must be chosen or added before recording** (`REQUIRE_PATIENT=true`, a team
  decision). This differs from the original brief, which asked for recording to be possible
  without patient details; set `REQUIRE_PATIENT=false` to allow that again. A patient needs
  only a name; age and sex are optional. There is no hospital-ID field. Patients belong to
  the doctor who created them, so if two doctors see the same person, each has their own
  patient record.
* **Sign-up has no password rules** (any non-empty password is accepted), as requested by
  the team. For a real deployment, consider a minimum length and a `SIGNUP_CODE`.
* The consent setting (`CONSENT_MODE`) only shows a reminder or a tick-box. **It does not by
  itself satisfy the hospital's consent, ethics, or legal requirements.**
* When the database host looks external to the hospital (e.g. MongoDB Atlas), the server
  logs a warning at startup and `python -m app.manage check` reports it. There is no
  on-screen banner; it was removed at the team's request.

---

## 5. Testing that was done

`tests/e2e_test.py` drives the real UI in Chromium. The "microphone" is a synthetic
two-voice dialogue (Windows TTS) with a 14-second silent pause. The test checks:
start/stop, the saved length against the decoded audio length (no gaps), a mid-recording
network outage, recovery after a tab crash, SHA-256 integrity before and after transcript
editing, transcription after the long pause, recording without patient details, patient
history, the microphone-denied message, and that doctors can't see each other's data.
Screenshots are in `docs/screenshots/`.

Last full run: **40/40 checks passed** against the Atlas test cluster (`AUDIO_STORE=gridfs`,
faster-whisper `base`, sherpa-onnx speaker labels on, number of speakers automatic). The
80-second recording decoded to 80.1 s with a 14-second upload outage in the middle. Two
speakers were detected, and every transcript line had the right label, including after the
14-second pause. The server answered within 0.6 s throughout processing. The crash test
recovered 20.1 s of a ~22 s recording from the device. `tests/test_labeling.py` unit-tests
the word-to-speaker step (mid-sentence splits, smoothing, gaps).

To run the tests (Windows; synthetic voices only):

```bash
pip install playwright==1.49.* && python -m playwright install chromium
powershell -File tests/make_dialogue.ps1 -OutDir tests\output\audio
python tests/build_dialogue.py tests/output/audio
python tests/e2e_test.py          # server must be running on :8765; test account in tests/output/test-account.env
python tests/signup_test.py       # sign-up: open, code-protected, disabled (15 checks; removes its accounts)
python tests/test_labeling.py     # speaker-label merging, no models needed
```

`tests/reset_test_data.py` removes only what the automated tests created (the synthetic
`test.doctor@pilot.local` account's patients, consultations and audio, and temporary test
accounts). Other users' records are left alone. It refuses to run when the database is
marked hospital-internal.

This used synthetic speech and a virtual microphone. **It does not replace a test in the
consultation room with the real microphone, a real distance between speakers, and real
background noise.** See "Decisions" below.

---

## 6. Assumptions, limitations, and decisions the hospital or team must make

**Must decide before real patients are recorded**

1. **Where the database runs.** The Atlas cluster used for testing is a cloud service *outside*
   the hospital. Real recordings, patient names and transcripts must go to a MongoDB (or disk)
   inside the hospital: set `MONGO_URI`, and choose `AUDIO_STORE=gridfs` or `disk`. The app
   no longer shows an on-screen warning about this, so it must be checked at deployment
   (`python -m app.manage check`).
2. **Access permissions and sign-up.** The pilot is "each doctor sees only their own
   records". Decide whether sign-up stays open, uses a `SIGNUP_CODE`, or is turned off in
   favour of administrator-created accounts. Email addresses are not verified. Whether
   department colleagues, the PI, or administrators may see records is undecided and is not
   built.
3. **Consent process.** This is the hospital's process. Choose `CONSENT_MODE` and the wording.
4. **Whether any model may be fetched from the internet** (one-time Whisper and
   speaker-label model downloads), or must be copied in offline.
5. **HTTPS certificate** for using the app from devices other than the server itself.
6. **Retention, backup and deletion policy** for recordings. Nothing is deleted
   automatically, and there is no delete button.
7. **The PI's name.** Set `PI_NAME` to show it in the footer. Until then the PI line
   is simply hidden. The ClinCord waveform emblem is `branding/logo.svg`. The original
   IIIT Hyderabad emblem remains available as `branding/logo.png`; remove or rename
   `logo.svg` to use that PNG instead. The footer credits come from `TEAM_NAMES`
   (default: Varun, Akash, Jaswanth).

**Known limitations**

* If the server can't be reached at the moment Start is pressed, recording does not start,
  and the doctor sees why. Once recording has started, outages are handled.
* Leaving the browser tab, or letting a tablet sleep, can pause capture on some mobile
  browsers. The app asks for a screen wake-lock and blocks in-app navigation while recording,
  but the device should stay on the recorder screen.
* Tested in Chromium. Safari records in MP4 and is untested.
* Recordings are Opus at 128 kbps (lossy, but transparent for speech). Lossless WAV would
  need a different capture path and about 350 MB per hour.
* Transcript quality depends on the model size. The `small` and `base` models are weak on
  Indian languages and code-mixed speech; try `medium` or `large-v3` with a GPU.
* Speaker labels were tested only on two clearly different synthetic voices (male and
  female TTS), which is the easy case. Real consultations, with similar voices, accents,
  code-mixed speech, overlap, and relatives joining in, need checking.
* No earlier prototype code was available on the build machine. Only the prototype's
  MongoDB structure (`opd_conversation_assistant`) was inspected, to align field names.
  Its translation, TTS, turn-by-turn and SOAP-summary features were deliberately not carried
  over.
