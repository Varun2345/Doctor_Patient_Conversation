"""Generate two ~1-minute SYNTHETIC consultations for testing speaker labels.

  test-audio/three-speakers-english.wav       3 speakers, all English
  test-audio/three-speakers-multilingual.wav  3 speakers: English / Hindi / Telugu

Each comes with <name>.truth.json (who spoke when, exact timings) and <name>.script.md.
Voices: Microsoft neural TTS via the `edge-tts` package. Only the made-up script text
below is sent to that online service; no app or patient data is involved.

    python tests/make_test_conversations.py
"""
from __future__ import annotations

import asyncio
import io
import json
import random
import struct
import wave
from pathlib import Path

import av
import edge_tts
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "test-audio"
RATE = 48000
random.seed(11)
np.random.seed(11)

DOCTOR = ("Doctor", "en-IN-PrabhatNeural", "English")
PATIENT_EN = ("Patient", "en-IN-NeerjaNeural", "English")
SON = ("Son", "en-GB-RyanNeural", "English")
PATIENT_HI = ("Patient", "hi-IN-SwaraNeural", "Hindi")
RELATIVE_TE = ("Relative", "te-IN-MohanNeural", "Telugu")

ENGLISH = [
    (DOCTOR, "Good morning. Please come in and have a seat. What seems to be the problem today?"),
    (PATIENT_EN, "Good morning, doctor. I have had a cough and a sore throat for almost a week now."),
    (SON, "She also had a fever two nights ago. It went up to a hundred and one."),
    (DOCTOR, "I see. Any difficulty breathing, or pain in the chest?"),
    (PATIENT_EN, "No chest pain. But I feel very tired in the afternoons."),
    (SON, "And she is not eating much. Just some rice and curd."),
    (DOCTOR, "Okay. Let me listen to your chest. Please take a deep breath."),
    ("pause", 3.0),
    (PATIENT_EN, "Is it something serious, doctor?"),
    (DOCTOR, "Your chest sounds clear. It looks like a viral infection."),
    (SON, "Does she need any tests?"),
    (DOCTOR, "Let us do a blood test, just to be safe. Drink warm fluids and rest for three days."),
    (PATIENT_EN, "Thank you, doctor."),
    (SON, "We will come back on Friday with the report."),
]

MULTILINGUAL = [
    (DOCTOR, "Good morning. Please sit down. Tell me, what is the problem?"),
    (PATIENT_HI, "नमस्ते डॉक्टर साहब। मुझे तीन दिन से पेट में दर्द हो रहा है।"),
    (RELATIVE_TE, "ఆమెకు రాత్రి వాంతులు కూడా అయ్యాయి డాక్టర్ గారు."),
    (DOCTOR, "Is the pain on the right side, or all over the stomach?"),
    (PATIENT_HI, "दाहिनी तरफ़ ज़्यादा है, और खाना खाने के बाद बढ़ जाता है।"),
    (RELATIVE_TE, "ఆమె రెండు రోజులుగా సరిగ్గా తినడం లేదు."),
    (DOCTOR, "Any fever? Did you take any medicine?"),
    (PATIENT_HI, "हल्का बुखार था। मैंने सिर्फ़ एक पैरासिटामोल ली थी।"),
    (RELATIVE_TE, "ఈ రోజు ఉదయం జ్వరం లేదు."),
    (DOCTOR, "Okay. We will do an ultrasound of the abdomen and a blood test."),
    (PATIENT_HI, "ठीक है डॉक्टर, रिपोर्ट कब तक आएगी?"),
    (RELATIVE_TE, "మేము మధ్యాహ్నం మళ్ళీ వస్తాము."),
    (DOCTOR, "The reports will be ready by evening. Please avoid oily food until then."),
]

TRANSLATIONS = {
    "नमस्ते डॉक्टर साहब। मुझे तीन दिन से पेट में दर्द हो रहा है।": "Hello doctor. I have had stomach pain for three days.",
    "ఆమెకు రాత్రి వాంతులు కూడా అయ్యాయి డాక్టర్ గారు.": "She also vomited at night, doctor.",
    "दाहिनी तरफ़ ज़्यादा है, और खाना खाने के बाद बढ़ जाता है।": "It is more on the right side, and it gets worse after eating.",
    "ఆమె రెండు రోజులుగా సరిగ్గా తినడం లేదు.": "She has not been eating properly for two days.",
    "हल्का बुखार था। मैंने सिर्फ़ एक पैरासिटामोल ली थी।": "There was a mild fever. I took only one paracetamol.",
    "ఈ రోజు ఉదయం జ్వరం లేదు.": "There was no fever this morning.",
    "ठीक है डॉक्टर, रिपोर्ट कब तक आएगी?": "Okay doctor, by when will the report come?",
    "మేము మధ్యాహ్నం మళ్ళీ వస్తాము.": "We will come again in the afternoon.",
}


async def tts(text: str, voice: str) -> bytes:
    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


def decode(mp3: bytes) -> np.ndarray:
    """MP3 -> mono float32 at 48 kHz."""
    out = []
    with av.open(io.BytesIO(mp3)) as c:
        res = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        for frame in c.decode(audio=0):
            for f in res.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in res.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    x = np.concatenate(out).astype(np.float32)
    nz = np.nonzero(np.abs(x) > 1e-3)[0]  # trim TTS lead/tail silence
    x = x[max(0, nz[0] - 480): nz[-1] + 480] if len(nz) else x
    return x / (np.max(np.abs(x)) + 1e-9) * 0.5  # same level for every voice


def room(seconds: float) -> np.ndarray:
    return (np.random.randn(int(seconds * RATE)) * 0.0015).astype(np.float32)


async def build(name: str, script: list, title: str) -> dict:
    parts = [room(1.0)]
    t = 1.0
    truth = []
    for item in script:
        if item[0] == "pause":
            parts.append(room(item[1]))
            t += item[1]
            continue
        (speaker, voice, lang), text = item
        speech = decode(await tts(text, voice))
        speech = speech + (np.random.randn(len(speech)) * 0.0015).astype(np.float32)
        dur = len(speech) / RATE
        truth.append({"start": round(t, 2), "end": round(t + dur, 2), "speaker": speaker,
                      "language": lang, "voice": voice, "text": text,
                      **({"english": TRANSLATIONS[text]} if text in TRANSLATIONS else {})})
        parts.append(speech)
        t += dur
        gap = random.uniform(0.45, 0.9)
        parts.append(room(gap))
        t += gap
    parts.append(room(1.0))
    audio = np.clip(np.concatenate(parts), -1, 1)
    pcm = (audio * 32767).astype("<i2").tobytes()
    OUT.mkdir(exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
    duration = len(audio) / RATE
    meta = {"file": f"{name}.wav", "title": title, "durationSec": round(duration, 1),
            "speakers": sorted({x["speaker"] for x in truth}), "turns": truth}
    (OUT / f"{name}.truth.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# {title}", "", f"Synthetic test audio, {duration:.0f} s, 48 kHz mono WAV. "
             "Voices: Microsoft neural TTS. No real people.", "",
             "| Time | Speaker | Language | Line |", "|---|---|---|---|"]
    for x in truth:
        mm, ss = divmod(int(x["start"]), 60)
        line = x["text"] + (f" *({x['english']})*" if x.get("english") else "")
        lines.append(f"| {mm:02d}:{ss:02d} | {x['speaker']} | {x['language']} | {line} |")
    (OUT / f"{name}.script.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return meta


async def main():
    a = await build("three-speakers-english", ENGLISH, "Three speakers – English")
    b = await build("three-speakers-multilingual", MULTILINGUAL,
                    "Three speakers – English / Hindi / Telugu")
    for m in (a, b):
        print(f"{m['file']}: {m['durationSec']} s, {len(m['turns'])} turns, speakers {m['speakers']}")


if __name__ == "__main__":
    asyncio.run(main())
