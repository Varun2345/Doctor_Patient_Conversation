"""Join the synthetic TTS lines into one 48 kHz mono WAV with natural pauses,
one long silent pause (examination) and faint room noise. Test data only."""
import random, struct, sys, wave
from pathlib import Path

out_dir = Path(sys.argv[1])
rate = 48000
random.seed(7)
def noise(seconds, amp=60):
    return [int(random.gauss(0, amp)) for _ in range(int(seconds * rate))]
samples = noise(1.5)
for i in range(9):
    with wave.open(str(out_dir / f"line{i:02d}.wav")) as w:
        assert w.getframerate() == rate and w.getnchannels() == 1
        data = w.readframes(w.getnframes())
    speech = struct.unpack("<%dh" % (len(data) // 2), data)
    samples += [max(-32768, min(32767, s + int(random.gauss(0, 60)))) for s in speech]
    samples += noise(14.0 if i == 4 else 1.3)   # long pause during "examination"
samples += noise(3.0)
with wave.open(str(out_dir / "dialogue.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(struct.pack("<%dh" % len(samples), *samples))
print(f"dialogue.wav: {len(samples) / rate:.1f} s")
