# Test conversations for speaker labelling

Synthetic consultations. Made-up dialogue spoken by Microsoft neural text-to-speech voices, no real people. Regenerate with `python tests/make_test_conversations.py`.

| File | Length | Speakers (voice) | Languages |
|---|---|---|---|
| `two-speakers-english.wav` | 125.4 s | Doctor (Indian English, male: `en-IN-PrabhatNeural`), Patient (Indian English, female: `en-IN-NeerjaNeural`) | English |
| `two-speakers-multilingual.wav` | 121.0 s | Doctor (Indian English, male: `en-IN-PrabhatNeural`), Patient (Hindi, female: `hi-IN-SwaraNeural`) | English / Hindi |

Each has a `.script.md` (the lines with timings, plus English translations) and a `.truth.json` (exact start/end of every turn), so speaker labels can be scored.
