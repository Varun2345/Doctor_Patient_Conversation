"""Generate two ~1-minute SYNTHETIC consultations for testing speaker labels.

  test-audio/two-speakers-english.wav        2 speakers (male Doctor, female Patient), all English
  test-audio/two-speakers-multilingual.wav   2 speakers (male Doctor in English, female Patient in Hindi)

Each comes with <name>.truth.json (who spoke when, exact timings) and <name>.script.md.
Voices: Microsoft neural TTS via the `edge-tts` package.
"""
from __future__ import annotations

import asyncio
import io
import json
import random
import wave
from pathlib import Path

import av
import edge_tts
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "test-audio"
RATE = 48000
random.seed(11)
np.random.seed(11)

DOCTOR_EN = ("Doctor", "en-IN-PrabhatNeural", "English")
PATIENT_EN = ("Patient", "en-IN-NeerjaNeural", "English")
PATIENT_HI = ("Patient", "hi-IN-SwaraNeural", "Hindi")

ENGLISH_2SPK = [
    (DOCTOR_EN, "Good morning. Please come in and take a seat. How can I help you today?"),
    (PATIENT_EN, "Good morning, doctor. I have been having severe headaches and dizziness for the past four days."),
    (DOCTOR_EN, "I see. Does the headache stay on one side of your head, or does it feel like pressure all over?"),
    (PATIENT_EN, "It feels like constant pressure around my forehead and temples, especially in the late afternoon."),
    (DOCTOR_EN, "Have you noticed any nausea, sensitivity to bright lights, or blurring in your vision?"),
    (PATIENT_EN, "Yes, bright screen lights at work make it worse, but thankfully I have not vomited."),
    (DOCTOR_EN, "Understood. How has your sleep and water intake been over this past week?"),
    (PATIENT_EN, "My sleep has been quite irregular because of project deadlines, maybe only five hours a night, and I drink too much coffee."),
    (DOCTOR_EN, "Alright. Let me check your blood pressure and examine your eyes first. Please relax your arm."),
    ("pause", 3.0),
    (DOCTOR_EN, "Your blood pressure is slightly elevated at one hundred and thirty-five over eighty-eight."),
    (PATIENT_EN, "Is that dangerous, doctor? Should I be worried?"),
    (DOCTOR_EN, "It is mildly elevated, likely triggered by sleep deprivation, stress, and caffeine. I will prescribe a mild pain reliever and advise you to rest."),
    (PATIENT_EN, "Do I need to undergo an MRI or any blood investigations right away?"),
    (DOCTOR_EN, "Not immediately. Let us monitor your symptoms for one week. Please drink plenty of water, reduce screen time, and get at least seven hours of sleep."),
    (PATIENT_EN, "Thank you very much, doctor. I will follow your advice and see you next Monday."),
    (DOCTOR_EN, "You are welcome. Take care, and feel free to return sooner if the headache worsens."),
]

MULTILINGUAL_2SPK = [
    (DOCTOR_EN, "Good morning. Please come inside and have a seat. What brings you to the clinic today?"),
    (PATIENT_HI, "नमस्ते डॉक्टर साहब। मुझे पिछले चार-पांच दिनों से बहुत तेज़ खांसी और सीने में जकड़न महसूस हो रही है।"),
    (DOCTOR_EN, "Do you have any fever or chills, especially during the evening hours?"),
    (PATIENT_HI, "हाँ डॉक्टर, दो दिन पहले रात में हल्का बुखार था, लगभग सौ डिग्री, और पूरे बदन में दर्द भी था।"),
    (DOCTOR_EN, "Are you coughing up any phlegm, or is it mostly a dry cough?"),
    (PATIENT_HI, "शुरुआत में सूखी खांसी थी, लेकिन अब पीला कफ निकल रहा है और सांस लेने में भी थोड़ी तकलीफ होती है।"),
    (DOCTOR_EN, "Alright. Please sit upright and take a few deep breaths while I listen to your chest with the stethoscope."),
    ("pause", 3.0),
    (DOCTOR_EN, "There is some mild wheezing in the lower lobes of your lungs. Have you taken any medications yet?"),
    (PATIENT_HI, "मैंने घर पर सिर्फ़ अदरक की चाय पी थी और कल रात एक पैरासिटामोल की गोली ली थी।"),
    (DOCTOR_EN, "Okay. It looks like an acute bronchial infection. I am prescribing an antibiotic along with a cough syrup for five days."),
    (PATIENT_HI, "क्या मुझे कोई खून की जांच या छाती का एक्स-रे भी करवाना पड़ेगा?"),
    (DOCTOR_EN, "Let us get a chest X-ray done today just to rule out pneumonia, and then start the course of medication immediately."),
    (PATIENT_HI, "ठीक है डॉक्टर साहब, एक्स-रे की रिपोर्ट कब तक मिल जाएगी?"),
    (DOCTOR_EN, "The X-ray will be ready in thirty minutes. Avoid cold drinks, take steam twice a day, and rest properly."),
    (PATIENT_HI, "बहुत-बहुत धन्यवाद डॉक्टर साहब, मैं अभी एक्स-रे करवा कर आती हूँ।"),
    (DOCTOR_EN, "You are welcome. Please show me the film as soon as it is done."),
]

TRANSLATIONS = {
    "नमस्ते डॉक्टर साहब। मुझे पिछले चार-पांच दिनों से बहुत तेज़ खांसी और सीने में जकड़न महसूस हो रही है।": "Hello doctor. I have had a severe cough and chest tightness for the last four to five days.",
    "हाँ डॉक्टर, दो दिन पहले रात में हल्का बुखार था, लगभग सौ डिग्री, और पूरे बदन में दर्द भी था।": "Yes doctor, two days ago at night I had a mild fever, around one hundred degrees, and body ache too.",
    "शुरुआत में सूखी खांसी थी, लेकिन अब पीला कफ निकल रहा है और सांस लेने में भी थोड़ी तकलीफ होती है।": "In the beginning it was a dry cough, but now there is yellow phlegm and some difficulty in breathing.",
    "मैंने घर पर सिर्फ़ अदरक की चाय पी थी और कल रात एक पैरासिटामोल की गोली ली थी।": "At home I only had ginger tea and took one paracetamol tablet last night.",
    "क्या मुझे कोई खून की जांच या छाती का एक्स-रे भी करवाना पड़ेगा?": "Will I also need a blood test or a chest X-ray done?",
    "ठीक है डॉक्टर साहब, एक्स-रे की रिपोर्ट कब तक मिल जाएगी?": "Okay doctor, by when will the X-ray report be ready?",
    "बहुत-बहुत धन्यवाद डॉक्टर साहब, मैं अभी एक्स-रे करवा कर आती हूँ।": "Thank you very much doctor, I will get the X-ray done right now and return.",
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
        gap = random.uniform(0.55, 1.0)
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
    a = await build("two-speakers-english", ENGLISH_2SPK, "Two speakers – English (Male Doctor, Female Patient)")
    b = await build("two-speakers-multilingual", MULTILINGUAL_2SPK,
                    "Two speakers – English / Hindi (Male Doctor English, Female Patient Hindi)")
    for m in (a, b):
        print(f"{m['file']}: {m['durationSec']} s, {len(m['turns'])} turns, speakers {m['speakers']}")


if __name__ == "__main__":
    asyncio.run(main())
