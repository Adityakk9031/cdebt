# cdebt launch video (Remotion)

The animated launch video, built with [Remotion](https://www.remotion.dev/) from real screenshots
of a hand-answered rehearsal on cdebt 1.1.0rc10/rc11 (see `docs/VERIFICATION.md`).

- `src/script.json`: the voice-over, one entry per scene.
- `public/vo/*.wav`: the voice-over, made sentence by sentence with the macOS `say` voice
  "Samantha", so every caption, zoom and highlight is timed to the sentence being spoken
  (`src/durations.json` holds the measured start and end of each sentence).
- `public/shots/*.png`: the real screenshots (terminal and GitHub PR).
- `src/Video.jsx`: the scenes: 3D screenshot cards with camera zooms and highlight boxes keyed to
  sentences, animated diagrams, transitions, captions.

```bash
python3 make_voiceover.py                                           # the voice-over (macOS)
npm install
npx remotion studio src/index.jsx                                   # preview
npx remotion render src/index.jsx CdebtLaunch out/cdebt-launch.mp4  # render (about 3 minutes of video)
```
