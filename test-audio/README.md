# Test conversations for speaker labelling

Synthetic consultations. Made-up dialogue spoken by Microsoft neural text-to-speech voices, no real people. Regenerate with `python tests/make_test_conversations.py`.

| File | Length | Speakers (voice) | Languages |
|---|---|---|---|
| `two-speakers-english.wav` | 125.4 s | Doctor (Indian English, male: `en-IN-PrabhatNeural`), Patient (Indian English, female: `en-IN-NeerjaNeural`) | English |
| `two-speakers-multilingual.wav` | 121.0 s | Doctor (Indian English, male: `en-IN-PrabhatNeural`), Patient (Hindi, female: `hi-IN-SwaraNeural`) | English / Hindi |
| `three-speakers-english.wav` | 102.6 s | Doctor (Indian English, male: `en-IN-PrabhatNeural`), Patient (Indian English, female: `en-IN-NeerjaNeural`), Attendant (US English, male: `en-US-GuyNeural`) | English |
| `three-speakers-multilingual.wav` | 127.7 s | Doctor (male: `hi-IN-MadhurNeural`), Patient (female: `hi-IN-SwaraNeural`), Attendant (male: `te-IN-MohanNeural`) | Hindi / English and Telugu / English, mixed within turns |

Each has a `.script.md` (the lines with timings, plus English translations) and a `.truth.json` (exact start/end of every turn), so speaker labels can be scored.

The English three-speaker sample has 15 turns: six for the doctor, five for the patient, and four
for the attendant. Turns are synthesized separately,
trimmed and normalized, then joined with 0.55–1.0 second gaps, a two-second examination
pause, and faint generated background noise. Files are 48 kHz mono, 16-bit PCM WAV.
There is no overlapping speech; this is a basic speaker-separation test, not a benchmark
for real consultations or interruptions. Voice generation needs internet access.

To regenerate only this sample (leaving the existing two-speaker files untouched):

```bash
python -m pip install edge-tts av numpy
python tests/make_test_conversations.py --sample three-speakers-english
```

The code-mixed three-speaker sample (`three-speakers-multilingual.wav`) has 15 turns,
five per person. The doctor uses `hi-IN-MadhurNeural`, the patient uses
`hi-IN-SwaraNeural`, and the attendant uses `te-IN-MohanNeural`. Doctor and patient
mix Hindi with English within turns; the attendant mixes Telugu with English. Each
person retains one voice throughout. Its script includes English translations, and
the reference JSON records speaker-turn timings. Language labels describe each turn's
language pair, not word-level language boundaries. It also has no overlapping speech.

```bash
python tests/make_test_conversations.py --sample three-speakers-multilingual
```

To evaluate all samples, including this one, using the current local diarization engine:

```bash
python tests/score_diarization.py --engine sherpa-onnx
```

This reports detected speaker counts and turn assignments against the reference timings;
it does not create consultations or upload test recordings to the database.
