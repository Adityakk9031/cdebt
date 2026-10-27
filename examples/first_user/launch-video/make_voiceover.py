"""Make public/vo/<scene>.wav sentence by sentence with the macOS `say` voice "Samantha", and write
src/durations.json with each sentence's start and end, so captions and zooms can be timed exactly.
Run from this folder: python3 make_voiceover.py (macOS only: needs `say` and `afconvert`)."""

import json
import os
import re
import subprocess
import tempfile
import wave

GAP = 0.18  # seconds of silence between sentences
VOICE, RATE = "Samantha", "172"

with open("src/script.json") as fh:
    script = json.load(fh)
os.makedirs("public/vo", exist_ok=True)
tmp = tempfile.mkdtemp()
out = []
for scene in script:
    sents = [x.strip() for x in re.findall(r"[^.!?]+[.!?]+", scene["text"])] or [scene["text"]]
    frames, timings, t, params = [], [], 0.0, None
    for i, text in enumerate(sents):
        a, w = f"{tmp}/{scene['id']}_{i}.aiff", f"{tmp}/{scene['id']}_{i}.wav"
        subprocess.run(["say", "-v", VOICE, "-r", RATE, "-o", a, text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", a, w], check=True)
        with wave.open(w) as wf:
            frames.append(wf.readframes(wf.getnframes()))
            d = wf.getnframes() / wf.getframerate()
            params = wf.getparams()
        timings.append({"text": text, "start": round(t, 3), "end": round(t + d, 3)})
        t += d + GAP
    silence = b"\x00\x00" * int(GAP * 44100) * params.nchannels
    with wave.open(f"public/vo/{scene['id']}.wav", "wb") as o:
        o.setparams(params)
        o.writeframes(silence.join(frames))
    out.append({"id": scene["id"], "seconds": round(t - GAP, 3), "sentences": timings})
with open("src/durations.json", "w") as fh:
    json.dump(out, fh, indent=1)
print(f"{len(out)} scenes, {sum(o['seconds'] for o in out):.1f} s of voice-over")
