import React from 'react';
import {
  AbsoluteFill, Audio, Easing, Img, Sequence, interpolate, spring, staticFile,
  useCurrentFrame, useVideoConfig,
} from 'remotion';
import SCRIPT from './script.json';
import DUR from './durations.json';

export const FPS = 30;
const PAD = 0.9; // seconds of breathing room after each voice-over line
const DELAY = 8; // frames: the scene starts moving before the voice begins
const C = {bg: '#070b18', cyan: '#3ee6ff', violet: '#8b5cf6', green: '#34d399', red: '#fb7185',
  amber: '#fbbf24', text: '#e8eefc', mute: '#8a96b8'};
const FONT = '-apple-system, "SF Pro Display", "Helvetica Neue", Helvetica, Arial, sans-serif';
const MONO = '"SF Mono", Menlo, Monaco, monospace';

const text = Object.fromEntries(SCRIPT.map((s) => [s.id, s.text]));
const dur = (id) => DUR.find((d) => d.id === id);
const frames = (id) => Math.ceil((dur(id).seconds + PAD) * FPS) + DELAY;
// frame (within the scene) where sentence i starts / ends
const sStart = (id, i) => DELAY + Math.round(dur(id).sentences[i].start * FPS);
const sEnd = (id, i) => DELAY + Math.round(dur(id).sentences[Math.min(i, dur(id).sentences.length - 1)].end * FPS);

// ---------- shared pieces ----------

const ease = Easing.bezier(0.22, 1, 0.36, 1);
const lerpKeys = (t, keys, k) => {
  // keys: [{at, x, y, z}] sorted by `at` (0..1 of the shot); smooth between neighbours
  if (t <= keys[0].at) return keys[0][k];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t <= b.at) return interpolate(t, [a.at, b.at], [a[k], b[k]], {easing: ease});
  }
  return keys[keys.length - 1][k];
};

const Background = () => {
  const f = useCurrentFrame();
  const g1 = 30 + 20 * Math.sin(f / 90), g2 = 70 + 15 * Math.cos(f / 110);
  return (
    <AbsoluteFill style={{background: C.bg, overflow: 'hidden'}}>
      <AbsoluteFill style={{background:
        `radial-gradient(900px 600px at ${g1}% 25%, rgba(139,92,246,0.28), transparent 70%),` +
        `radial-gradient(800px 600px at ${g2}% 80%, rgba(62,230,255,0.20), transparent 70%)`}} />
      {/* 3D grid floor */}
      <div style={{position: 'absolute', left: '-50%', width: '200%', height: '120%', top: '45%',
        transform: 'perspective(900px) rotateX(72deg)', transformOrigin: 'top',
        backgroundImage:
          'linear-gradient(rgba(62,230,255,0.13) 1px, transparent 1px),' +
          'linear-gradient(90deg, rgba(62,230,255,0.13) 1px, transparent 1px)',
        backgroundSize: '80px 80px', backgroundPosition: `0 ${(f * 1.6) % 80}px`,
        maskImage: 'linear-gradient(to bottom, black, transparent 75%)'}} />
      {Array.from({length: 40}).map((_, i) => {
        const x = (i * 137.5) % 100, sp = 0.15 + (i % 7) * 0.05;
        const y = (100 - ((f * sp + i * 23) % 120)) ;
        return <div key={i} style={{position: 'absolute', left: `${x}%`, top: `${y}%`, width: 3, height: 3,
          borderRadius: 3, background: i % 3 ? C.cyan : C.violet, opacity: 0.35,
          boxShadow: `0 0 8px ${i % 3 ? C.cyan : C.violet}`}} />;
      })}
    </AbsoluteFill>
  );
};

// captions: exactly the sentence being spoken, word by word highlighted
const Captions = ({id}) => {
  const f = useCurrentFrame();
  const S = dur(id).sentences;
  let i = 0;
  for (let k = 0; k < S.length; k++) if (f >= sStart(id, k) - 2) i = k;
  const st = sStart(id, i), en = sEnd(id, i);
  const words = S[i].text.split(' ');
  const spoken = interpolate(f, [st, en], [0, words.length], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const o = interpolate(f, [st - 2, st + 6], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', bottom: 46, left: 0, right: 0, display: 'flex', justifyContent: 'center'}}>
      <div style={{maxWidth: 1500, padding: '14px 28px', borderRadius: 14, background: 'rgba(7,11,24,0.8)',
        border: '1px solid rgba(62,230,255,0.25)', fontFamily: FONT, fontSize: 34, lineHeight: 1.3,
        textAlign: 'center', opacity: o, transform: `translateY(${(1 - o) * 12}px)`}}>
        {words.map((w, k) => <span key={k} style={{color: k < spoken ? C.text : 'rgba(232,238,252,0.45)',
          textShadow: k < spoken && k > spoken - 1.5 ? `0 0 12px ${C.cyan}` : 'none'}}>{w} </span>)}
      </div>
    </div>
  );
};

const Kicker = ({children, color = C.cyan, delay = 0}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f - delay, fps, config: {damping: 18}});
  return <div style={{display: 'inline-block', padding: '8px 18px', borderRadius: 999, border: `1px solid ${color}`,
    color, fontFamily: MONO, fontSize: 24, letterSpacing: 2, textTransform: 'uppercase',
    opacity: s, transform: `translateY(${(1 - s) * 20}px)`, background: 'rgba(7,11,24,0.6)'}}>{children}</div>;
};

// ---------- the 3D screenshot shot with camera keyframes and glowing highlights ----------

const W = 1560, H = Math.round(1560 * 1301 / 2000);

const resolveAt = (sceneId, offset, len, k) => {
  if (k.s === undefined) return k.at;
  const a = sStart(sceneId, k.s), b = sEnd(sceneId, k.s);
  return (a + (b - a) * (k.off || 0) - offset) / len;
};

const Shot = ({src, keys: rawKeys, boxes: rawBoxes = [], label, dur, sceneId, offset = 0}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = f / dur;
  const keys = rawKeys.map((k) => ({...k, at: resolveAt(sceneId, offset, dur, k)}));
  const boxes = rawBoxes.map((b) => b.s === undefined ? b : {...b,
    from: resolveAt(sceneId, offset, dur, {s: b.s, off: b.off || 0}),
    to: b.sEnd === undefined ? 1.05 : resolveAt(sceneId, offset, dur, {s: b.sEnd, off: 1})});
  const enter = spring({frame: f, fps, config: {damping: 20, mass: 0.9}});
  const exit = interpolate(f, [dur - 12, dur], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const rotY = interpolate(enter, [0, 1], [-28, 0]) + Math.sin(f / 70) * 2.2 + exit * 18;
  const rotX = interpolate(enter, [0, 1], [22, 0]) + Math.cos(f / 85) * 1.4;
  const tz = interpolate(enter, [0, 1], [-900, 0]) - exit * 500;
  const z = lerpKeys(t, keys, 'z') * (1 + 0.012 * Math.sin(f / 45));
  // never pan past the screenshot's edges (terminal text starts at the left margin)
  const clampTo = (v) => Math.min(1 - 0.5 / z, Math.max(0.5 / z, v));
  const x = clampTo(lerpKeys(t, keys, 'x')), y = clampTo(lerpKeys(t, keys, 'y'));
  return (
    <AbsoluteFill style={{perspective: 2200, alignItems: 'center', justifyContent: 'center', opacity: 1 - exit}}>
      <div style={{width: W, height: H, borderRadius: 22, overflow: 'hidden', position: 'relative',
        transform: `translateY(-30px) translateZ(${tz}px) rotateX(${rotX}deg) rotateY(${rotY}deg)`,
        boxShadow: '0 40px 120px rgba(0,0,0,0.7), 0 0 0 1px rgba(62,230,255,0.35), 0 0 60px rgba(62,230,255,0.18)',
        background: '#000'}}>
        <div style={{position: 'absolute', width: W, height: H, transformOrigin: 'center',
          transform: `translate(${(0.5 - x) * W * z}px, ${(0.5 - y) * H * z}px) scale(${z})`}}>
          <Img src={staticFile(`shots/${src}.png`)} style={{width: W, height: H, display: 'block'}} />
          {boxes.map((b, i) => {
            const span = Math.max(0.004, b.to - b.from), fade = Math.min(0.04, span / 3);
            const on = interpolate(t, [b.from, b.from + fade, b.from + span - fade, b.from + span + 1e-6], [0, 1, 1, 0],
              {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
            const pulse = 0.6 + 0.4 * Math.sin(f / 6);
            const col = b.color || C.cyan;
            const since = (t - b.from) * dur; // frames since the box appeared
            return <React.Fragment key={i}>
              <div style={{position: 'absolute', left: b.x * W, top: b.y * H, width: b.w * W,
                height: b.h * H, borderRadius: 6 / z, border: `${3 / z}px solid ${col}`, opacity: on,
                boxShadow: `0 0 ${24 * pulse / z}px ${col}, inset 0 0 ${18 / z}px ${col}55`,
                background: `${col}14`, transform: `scale(${1 + 0.08 * Math.max(0, 1 - since / 8)})`}} />
              {since >= 0 && since < 22 && Array.from({length: 14}).map((_, j) => {
                const ang = (j / 14) * Math.PI * 2, r = (since / 22) * 90 / z;
                return <div key={j} style={{position: 'absolute', width: 6 / z, height: 6 / z, borderRadius: 9,
                  left: (b.x + b.w / 2) * W + Math.cos(ang) * (b.w * W / 2 + r),
                  top: (b.y + b.h / 2) * H + Math.sin(ang) * (b.h * H / 2 + r),
                  background: col, boxShadow: `0 0 ${10 / z}px ${col}`, opacity: 1 - since / 22}} />;
              })}
            </React.Fragment>;
          })}
        </div>
        {/* glass sheen sweeping across the card */}
        <div style={{position: 'absolute', inset: 0, pointerEvents: 'none',
          background: `linear-gradient(115deg, transparent ${interpolate(f, [0, 60], [-30, 130], {extrapolateRight: 'clamp'})}%, rgba(255,255,255,0.10) ${interpolate(f, [0, 60], [-20, 140], {extrapolateRight: 'clamp'})}%, transparent ${interpolate(f, [0, 60], [-10, 150], {extrapolateRight: 'clamp'})}%)`}} />
      </div>
      <div style={{position: 'absolute', bottom: 70, width: W * 0.8, height: 60, borderRadius: '50%',
        background: 'radial-gradient(closest-side, rgba(62,230,255,0.35), transparent)', filter: 'blur(10px)',
        opacity: enter * (1 - exit)}} />
      {label && <div style={{position: 'absolute', top: 34, left: 60}}><Kicker>{label}</Kicker></div>}
    </AbsoluteFill>
  );
};

// a scene made of one or more shots that share the voice-over; a shot can start at a sentence
const ShotsScene = ({id, shots}) => {
  const total = frames(id);
  const starts = shots.map((sh, i) => (i === 0 ? 0 : sStart(id, sh.startS) - 4));
  return (
    <AbsoluteFill>
      {shots.map((sh, i) => {
        const len = (i + 1 < shots.length ? starts[i + 1] : total) - starts[i];
        return <Sequence key={i} from={starts[i]} durationInFrames={len}>
          <Shot {...sh} dur={len} sceneId={id} offset={starts[i]} />
        </Sequence>;
      })}
    </AbsoluteFill>
  );
};

// ---------- typographic / diagram scenes ----------

const Title = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f, fps, config: {damping: 14}});
  const letters = 'cdebt'.split('');
  const g0 = sStart('title', 2);
  const glitch = (f < 20 || (f > g0 && f < g0 + 10)) ? (Math.sin(f * 2.7) * 9) : Math.sin(f / 9) * 1.5;
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', perspective: 1400}}>
      <div style={{display: 'flex', transform: `rotateX(${(1 - s) * 50}deg) rotateY(${Math.sin(f / 40) * 8}deg)`}}>
        {letters.map((l, i) => {
          const ls = spring({frame: f - i * 4, fps, config: {damping: 12}});
          return <span key={i} style={{fontFamily: FONT, fontWeight: 800, fontSize: 260, color: C.text,
            display: 'inline-block', transform: `translateZ(${(1 - ls) * -600}px) translateY(${(1 - ls) * 120}px)`,
            opacity: ls, textShadow: `${glitch}px 0 0 rgba(255,60,90,0.8), ${-glitch}px 0 0 rgba(62,230,255,0.8), 0 0 40px ${C.cyan}88, 0 0 90px ${C.violet}66`}}>{l}</span>;
        })}
      </div>
      <div style={{marginTop: 10, opacity: interpolate(f, [25, 45], [0, 1], {extrapolateRight: 'clamp'}),
        fontFamily: FONT, fontSize: 46, color: C.mute}}>
        who actually <span style={{color: C.cyan}}>understood</span> the code your AI wrote?
      </div>
    </AbsoluteFill>
  );
};

const Problem = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items = [
    ['Code review', 'skims', C.amber],
    ['Tests', 'check behaviour, not understanding', C.amber],
    ['Comprehension debt', 'code nobody has verifiably understood', C.red],
    ['cdebt', 'measures code, never people', C.green],
  ];
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', perspective: 1600}}>
      <div style={{display: 'flex', flexDirection: 'column', gap: 26, transform: `rotateY(${Math.sin(f / 60) * 6}deg)`}}>
        {items.map(([a, b, col], i) => {
          const s = spring({frame: f - sStart('problem', i) + 4, fps, config: {damping: 16}});
          return <div key={i} style={{display: 'flex', alignItems: 'baseline', gap: 26, opacity: s,
            transform: `translateX(${(1 - s) * -220}px) translateZ(${(1 - s) * -400}px) rotateY(${(1 - s) * 40}deg)`,
            padding: '22px 40px', borderRadius: 18, background: 'rgba(255,255,255,0.04)',
            border: `1px solid ${col}66`, boxShadow: `0 0 40px ${col}22`}}>
            <span style={{fontFamily: FONT, fontWeight: 800, fontSize: 56, color: col, minWidth: 560}}>{a}</span>
            <span style={{fontFamily: FONT, fontSize: 40, color: C.text}}>{b}</span>
          </div>;
        })}
      </div>
    </AbsoluteFill>
  );
};

const Node = ({x, y, title, sub, col, at}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f - at, fps, config: {damping: 13}});
  return <div style={{position: 'absolute', left: x, top: y, width: 340, padding: '26px 24px', borderRadius: 22,
    background: 'rgba(12,18,38,0.92)', border: `2px solid ${col}`, boxShadow: `0 0 50px ${col}55`,
    transform: `translate(-50%,-50%) scale(${s}) rotateY(${(1 - s) * 90}deg)`, opacity: s, textAlign: 'center'}}>
    <div style={{fontFamily: FONT, fontWeight: 800, fontSize: 38, color: col}}>{title}</div>
    <div style={{fontFamily: FONT, fontSize: 25, color: C.text, marginTop: 10, lineHeight: 1.3}}>{sub}</div>
  </div>;
};

const How = () => {
  const f = useCurrentFrame();
  const pts = [[300, 470], [740, 470], [1180, 470], [1620, 470]];
  const at = [1, 2, 3, 4].map((i) => sStart('how', i) - 4);
  const lineP = (i) => interpolate(f, [at[i] + 10, at[i + 1]], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{perspective: 1800}}>
      <AbsoluteFill style={{transform: `rotateX(${10 + Math.sin(f / 50) * 3}deg)`}}>
        <svg width={1920} height={1080} style={{position: 'absolute'}}>
          {[0, 1, 2].map((i) => {
            const [x1, y1] = pts[i], [x2] = pts[i + 1];
            const p = lineP(i);
            const dot = x1 + 170 + (x2 - 170 - x1 - 170) * ((f / 40) % 1);
            return <g key={i}>
              <line x1={x1 + 170} y1={y1} x2={x1 + 170 + (x2 - x1 - 340) * p} y2={y1}
                stroke={C.cyan} strokeWidth={4} strokeDasharray="10 8" />
              {p >= 1 && <circle cx={dot} cy={y1} r={8} fill={C.cyan} style={{filter: `drop-shadow(0 0 8px ${C.cyan})`}} />}
            </g>;
          })}
        </svg>
        <Node x={pts[0][0]} y={pts[0][1]} at={at[0]} col={C.violet} title="git push" sub="the pre-push hook finds AI-written functions" />
        <Node x={pts[1][0]} y={pts[1][1]} at={at[1]} col={C.cyan} title="Your agent" sub="Claude Code proposes questions, never sees answers" />
        <Node x={pts[2][0]} y={pts[2][1]} at={at[2]} col={C.green} title="Sandbox" sub="cdebt runs each question to get the true answer" />
        <Node x={pts[3][0]} y={pts[3][1]} at={at[3]} col={C.amber} title="git note + PR" sub="proof travels with the push; CI re-checks it" />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const Rules = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const at = [1, 2, 3, 4].map((i) => sStart('rules', i));
  const cards = [
    ['≤ 6', 'questions per push', '3 per function, biggest first'],
    ['2 / 3', 'right to pass', 'one wrong answer is fine'],
    ['HARD ✓', 'a hard one must be right', 'easy questions alone never pass'],
    ['✗', 'failing score', 'no follow-up: simply not understood'],
  ];
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', perspective: 1600}}>
      <div style={{display: 'flex', gap: 34}}>
        {cards.map(([big, a, b], i) => {
          const s = spring({frame: f - at[i], fps, config: {damping: 14}});
          return <div key={i} style={{width: 380, height: 420, borderRadius: 26, padding: 34,
            background: 'linear-gradient(160deg, rgba(62,230,255,0.12), rgba(139,92,246,0.12))',
            border: '1px solid rgba(62,230,255,0.4)', boxShadow: '0 30px 80px rgba(0,0,0,0.5)',
            transform: `rotateY(${(1 - s) * 180}deg) translateY(${Math.sin((f + i * 20) / 30) * 8}px)`,
            backfaceVisibility: 'hidden', opacity: s > 0.5 ? 1 : 0, display: 'flex', flexDirection: 'column',
            justifyContent: 'center', textAlign: 'center'}}>
            <div style={{fontFamily: FONT, fontWeight: 800, fontSize: big.length > 4 ? 80 : 110, color: C.cyan, textShadow: `0 0 30px ${C.cyan}`}}>{big}</div>
            <div style={{fontFamily: FONT, fontWeight: 700, fontSize: 36, color: C.text, marginTop: 14}}>{a}</div>
            <div style={{fontFamily: FONT, fontSize: 26, color: C.mute, marginTop: 12}}>{b}</div>
          </div>;
        })}
      </div>
    </AbsoluteFill>
  );
};

const Bars = ({x, title, times, col, appear, tag, tagAt}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f - appear, fps, config: {damping: 16}});
  const labels = ['easy', 'medium', 'hard'];
  return <div style={{position: 'absolute', left: x, top: 175, width: 720, opacity: s,
    transform: `translateY(${(1 - s) * 40}px) rotateY(${(1 - s) * 30}deg)`}}>
    <div style={{fontFamily: FONT, fontWeight: 800, fontSize: 40, color: col, marginBottom: 26}}>{title}</div>
    {times.map((sec, i) => {
      const grow = interpolate(f - appear - i * 10, [0, 30], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease});
      return <div key={i} style={{display: 'flex', alignItems: 'center', gap: 18, marginBottom: 20}}>
        <div style={{width: 110, fontFamily: MONO, fontSize: 26, color: C.mute}}>{labels[i]}</div>
        <div style={{height: 40, width: sec * 16 * grow, borderRadius: 10,
          background: `linear-gradient(90deg, ${col}, ${col}88)`, boxShadow: `0 0 24px ${col}66`}} />
        <div style={{fontFamily: MONO, fontSize: 26, color: C.text, opacity: grow}}>{sec} s</div>
      </div>;
    })}
    {tag && <div style={{marginTop: 16, display: 'inline-block', padding: '10px 20px', borderRadius: 999,
      fontFamily: MONO, fontSize: 26, color: '#0b0f1f', background: col,
      opacity: interpolate(f, [tagAt, tagAt + 6], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
      transform: `scale(${1 + 0.06 * Math.sin(f / 4)})`, boxShadow: `0 0 30px ${col}`}}>{tag}</div>}
  </div>;
};

const Pace = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const S = (i) => sStart('pace', i);
  const card = spring({frame: f - S(3) - 20, fps, config: {damping: 13}});
  const limit = 7; // the countdown in the example: about 75% of a 9-10 s pace
  const tick = interpolate(f, [S(3) + 40, S(4)], [limit, 2], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const ring = 2 * Math.PI * 70;
  const result = f >= S(4) ? (f < S(5) - 6 ? 'pass' : 'fail') : null;
  const foot = interpolate(f, [S(6), S(6) + 15], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{perspective: 1600}}>
      <div style={{position: 'absolute', top: 50, width: '100%', textAlign: 'center',
        opacity: interpolate(f, [0, 12], [0, 1], {extrapolateRight: 'clamp'})}}><Kicker color={C.amber}>the timing check</Kicker></div>
      <Bars x={110} title="pasted into an AI" times={[9, 9, 9.5]} col={C.red} appear={S(1)} tag="suspiciously even" tagAt={S(3)} />
      <Bars x={1050} title="really thinking" times={[6, 11, 24]} col={C.green} appear={S(2)} tag="hard is slower: no follow-up" tagAt={S(2) + 40} />
      {/* the follow-up card with its countdown */}
      <div style={{position: 'absolute', left: 560, top: 560, width: 800, padding: 30, borderRadius: 24,
        background: 'rgba(12,18,38,0.95)', border: `2px solid ${C.amber}`, boxShadow: `0 0 60px ${C.amber}55`,
        opacity: card, transform: `translateZ(${(1 - card) * -700}px) rotateX(${(1 - card) * 40}deg)`,
        display: 'flex', alignItems: 'center', gap: 30}}>
        <svg width={170} height={170}>
          <circle cx={85} cy={85} r={70} stroke="rgba(255,255,255,0.12)" strokeWidth={12} fill="none" />
          <circle cx={85} cy={85} r={70} stroke={C.amber} strokeWidth={12} fill="none" strokeLinecap="round"
            strokeDasharray={ring} strokeDashoffset={ring * (1 - tick / limit)} transform="rotate(-90 85 85)" />
          <text x={85} y={100} textAnchor="middle" fontFamily={MONO} fontSize={46} fill={C.text}>{Math.ceil(tick)}s</text>
        </svg>
        <div style={{flex: 1}}>
          <div style={{fontFamily: MONO, fontSize: 24, color: C.amber}}>Follow-up (countdown from your own pace)</div>
          <div style={{fontFamily: MONO, fontSize: 30, color: C.text, marginTop: 10}}>What does late_fee(22, 14) evaluate to?</div>
          <div style={{fontFamily: MONO, fontSize: 26, color: C.mute, marginTop: 8}}>A) 0   B) -1   C) 1</div>
          {result && <div style={{marginTop: 14, fontFamily: FONT, fontWeight: 800, fontSize: 34,
            color: result === 'pass' ? C.green : C.red}}>
            {result === 'pass' ? '✓ right and in time → understood' : '✗ wrong or too slow → NOT understood'}</div>}
        </div>
      </div>
      <div style={{position: 'absolute', top: 112, width: '100%', textAlign: 'center', opacity: foot,
        fontFamily: FONT, fontSize: 34, color: C.text}}>
        raises the effort of pasting into an AI · <span style={{color: C.amber}}>proves nothing</span> · timing never stored
      </div>
    </AbsoluteFill>
  );
};

const Limits = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const rows = ['The score is not validated', 'A quiz cannot stop pasting questions into an AI',
    'The sandbox has had no independent review'];
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <Kicker color={C.amber}>honest limits</Kicker>
      <div style={{marginTop: 40, display: 'flex', flexDirection: 'column', gap: 22}}>
        {rows.map((r, i) => {
          const a = sStart('limits', 1), b = sEnd('limits', 1);
          const s = spring({frame: f - (a + (b - a) * i / 3), fps, config: {damping: 16}});
          return <div key={i} style={{fontFamily: FONT, fontSize: 50, color: C.text, opacity: s,
            transform: `translateY(${(1 - s) * 30}px)`}}><span style={{color: C.amber}}>— </span>{r}</div>;
        })}
      </div>
      <div style={{marginTop: 50, fontFamily: FONT, fontSize: 60, fontWeight: 800, color: C.cyan,
        opacity: interpolate(f, [sStart('limits', 2), sStart('limits', 2) + 15], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}}>
        visibility, not proof
      </div>
    </AbsoluteFill>
  );
};

const End = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f, fps, config: {damping: 14}});
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', perspective: 1400}}>
      <div style={{fontFamily: FONT, fontWeight: 800, fontSize: 180, color: C.text,
        transform: `scale(${s}) rotateY(${(1 - s) * -60}deg)`, textShadow: `0 0 50px ${C.cyan}88`}}>cdebt</div>
      <div style={{marginTop: 20, fontFamily: MONO, fontSize: 44, color: C.green, padding: '16px 30px',
        borderRadius: 14, background: 'rgba(0,0,0,0.5)', border: `1px solid ${C.green}66`,
        opacity: interpolate(f, [15, 30], [0, 1], {extrapolateRight: 'clamp'})}}>
        pip install --pre "cdebt[agent]"
      </div>
      <div style={{marginTop: 24, fontFamily: MONO, fontSize: 34, color: C.mute,
        opacity: interpolate(f, [25, 40], [0, 1], {extrapolateRight: 'clamp'})}}>github.com/Adityakk9031/cdebt</div>
    </AbsoluteFill>
  );
};

// ---------- the storyboard (keys and boxes are tied to the sentence that talks about them) ----------

const SCENES = [
  {id: 'title', el: <Title />},
  {id: 'problem', el: <Problem />},
  {id: 'how', el: <How />},
  {id: 'setup', el: <ShotsScene id="setup" shots={[{src: 'setup', label: 'setup from PyPI',
    keys: [{at: 0, x: 0.5, y: 0.5, z: 1}, {s: 1, x: 0, y: 0.07, z: 2.1}, {s: 2, x: 0, y: 0.42, z: 1.9},
      {s: 3, x: 0, y: 0.55, z: 1.9}, {s: 4, x: 0, y: 0.74, z: 2.3}, {at: 1, x: 0, y: 0.74, z: 2.4}],
    boxes: [{s: 1, sEnd: 1, x: 0.0, y: 0.005, w: 0.8, h: 0.05}, {s: 2, sEnd: 2, x: 0.0, y: 0.365, w: 0.75, h: 0.11},
      {s: 3, sEnd: 3, x: 0.0, y: 0.49, w: 0.75, h: 0.085}, {s: 4, x: 0.003, y: 0.69, w: 0.59, h: 0.085, color: C.red}]}]} />},
  {id: 'quiz', el: <ShotsScene id="quiz" shots={[{src: 'push-quiz', label: 'git push → the quiz',
    keys: [{at: 0, x: 0.5, y: 0.5, z: 1}, {s: 0, off: 0.6, x: 0, y: 0.02, z: 2.3}, {s: 1, x: 0, y: 0.15, z: 2.1},
      {s: 2, x: 0, y: 0.25, z: 2.1}, {s: 2, off: 0.7, x: 0, y: 0.5, z: 2.0}, {at: 1, x: 0, y: 0.52, z: 2.0}],
    boxes: [{s: 0, sEnd: 0, x: 0.0, y: 0.01, w: 0.45, h: 0.02}, {s: 1, sEnd: 1, x: 0.0, y: 0.09, w: 0.5, h: 0.03},
      {s: 2, x: 0.0, y: 0.055, w: 0.42, h: 0.035}, {s: 2, off: 0.6, x: 0.0, y: 0.45, w: 0.4, h: 0.035}]}]} />},
  {id: 'rules', el: <Rules />},
  {id: 'pace', el: <Pace />},
  {id: 'verdict', el: <ShotsScene id="verdict" shots={[{src: 'push-quiz', label: 'the verdicts',
    keys: [{at: 0, x: 0, y: 0.7, z: 1.4}, {s: 0, x: 0, y: 0.715, z: 2.5}, {at: 1, x: 0, y: 0.72, z: 2.6}],
    boxes: [{s: 0, x: 0.0, y: 0.6965, w: 0.4, h: 0.0125, color: C.green},
      {s: 1, x: 0.0, y: 0.7085, w: 0.4, h: 0.0125, color: C.red},
      {s: 2, x: 0.0, y: 0.7445, w: 0.86, h: 0.0125, color: C.amber}]}]} />},
  {id: 'pr', el: <ShotsScene id="pr" shots={[
    {src: 'pr-comment', label: 'the pull request',
      keys: [{at: 0, x: 0.5, y: 0.5, z: 1}, {s: 1, x: 0.4, y: 0.52, z: 1.9}, {s: 2, x: 0.38, y: 0.57, z: 2.3}, {at: 1, x: 0.38, y: 0.57, z: 2.35}],
      boxes: [{s: 1, sEnd: 1, x: 0.13, y: 0.475, w: 0.36, h: 0.04}, {s: 2, x: 0.14, y: 0.545, w: 0.43, h: 0.065}]},
    {src: 'pr-checks', label: 'advisory check', startS: 3,
      keys: [{at: 0, x: 0.45, y: 0.6, z: 1.3}, {at: 0.3, x: 0.35, y: 0.66, z: 2.2}, {at: 1, x: 0.35, y: 0.67, z: 2.3}],
      boxes: [{at: 0, from: 0.25, to: 1.05, x: 0.13, y: 0.64, w: 0.42, h: 0.07, color: C.mute}]}]} />},
  {id: 'retry', el: <ShotsScene id="retry" shots={[{src: 'quiz-pr', label: 'cdebt quiz --pr',
    keys: [{at: 0, x: 0.5, y: 0.5, z: 1}, {s: 0, off: 0.12, x: 0, y: 0.27, z: 2.2}, {s: 0, off: 0.45, x: 0, y: 0.32, z: 2.2},
      {s: 0, off: 0.8, x: 0, y: 0.95, z: 2.3}, {at: 1, x: 0, y: 0.95, z: 2.3}],
    boxes: [{s: 0, off: 0.1, x: 0.0, y: 0.245, w: 0.4, h: 0.025}, {s: 0, off: 0.45, x: 0.0, y: 0.3, w: 0.33, h: 0.025},
      {s: 0, off: 0.78, x: 0.0, y: 0.935, w: 0.2, h: 0.045, color: C.green}]}]} />},
  {id: 'recheck', el: <ShotsScene id="recheck" shots={[
    {src: 'recheck', label: '/cdebt recheck',
      keys: [{at: 0, x: 0.5, y: 0.5, z: 1}, {at: 0.35, x: 0.35, y: 0.42, z: 2.1}, {at: 1, x: 0.33, y: 0.42, z: 2.3}],
      boxes: [{at: 0, from: 0.3, to: 1.05, x: 0.14, y: 0.395, w: 0.36, h: 0.055}]},
    {src: 'recheck-diff', label: 'edit history', startS: 1,
      keys: [{at: 0, x: 0.5, y: 0.45, z: 1.1}, {s: 1, off: 0.25, x: 0.47, y: 0.38, z: 2.4}, {at: 1, x: 0.47, y: 0.38, z: 2.6}],
      boxes: [{s: 1, off: 0.55, x: 0.31, y: 0.382, w: 0.35, h: 0.019, color: C.red},
        {s: 1, off: 0.8, x: 0.31, y: 0.364, w: 0.35, h: 0.019, color: C.green}]}]} />},
  {id: 'sandbox', el: <ShotsScene id="sandbox" shots={[{src: 'sandbox-a', label: 'behind the scenes: the sandbox',
    keys: [{at: 0, x: 0.5, y: 0.6, z: 1}, {s: 0, off: 0.2, x: 0, y: 0.47, z: 2.0}, {s: 1, x: 0, y: 0.77, z: 2.0},
      {s: 1, off: 0.8, x: 0, y: 0.88, z: 2.2}, {at: 1, x: 0, y: 0.88, z: 2.2}],
    boxes: [{s: 0, off: 0.2, sEnd: 0, x: 0.0, y: 0.435, w: 0.3, h: 0.07}, {s: 1, x: 0.0, y: 0.725, w: 0.43, h: 0.095, color: C.green},
      {s: 1, off: 0.8, x: 0.0, y: 0.84, w: 0.2, h: 0.085, color: C.green}]}]} />},
  {id: 'expire', el: <ShotsScene id="expire" shots={[{src: 'gate', label: 'understanding expires',
    keys: [{at: 0, x: 0.5, y: 0.7, z: 1.1}, {s: 1, x: 0, y: 0.765, z: 2.3}, {s: 2, x: 0, y: 0.86, z: 2.3}, {at: 1, x: 0, y: 0.87, z: 2.4}],
    boxes: [{s: 1, off: 0.3, x: 0.0, y: 0.757, w: 0.4, h: 0.018, color: C.red},
      {s: 2, off: 0.4, x: 0.0, y: 0.875, w: 0.08, h: 0.018, color: C.amber}]}]} />},
  {id: 'limits', el: <Limits />},
  {id: 'end', el: <End />},
];

export const totalFrames = SCENES.reduce((a, sc) => a + frames(sc.id), 0);

// grain + vignette over everything
const Finish = () => {
  const f = useCurrentFrame();
  return <AbsoluteFill style={{pointerEvents: 'none'}}>
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.55) 100%)'}} />
    <svg width="1920" height="1080" style={{position: 'absolute', opacity: 0.06, mixBlendMode: 'overlay'}}>
      <filter id="g"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed={f % 30} /></filter>
      <rect width="100%" height="100%" filter="url(#g)" />
    </svg>
  </AbsoluteFill>;
};

export const Video = () => {
  let from = 0;
  return (
    <AbsoluteFill style={{fontFamily: FONT}}>
      <Background />
      {SCENES.map((sc) => {
        const d = frames(sc.id);
        const el = (
          <Sequence key={sc.id} from={from} durationInFrames={d}>
            <Transition dur={d}>{sc.el}</Transition>
            <Sequence from={DELAY}><Audio src={staticFile(`vo/${sc.id}.wav`)} /></Sequence>
            {sc.id !== 'title' && sc.id !== 'end' && <Captions id={sc.id} />}
          </Sequence>
        );
        from += d;
        return el;
      })}
      <Finish />
    </AbsoluteFill>
  );
};

// blur-zoom in and out with a light sweep across the frame
const Transition = ({children, dur}) => {
  const f = useCurrentFrame();
  const inP = interpolate(f, [0, 12], [0, 1], {extrapolateRight: 'clamp', easing: ease});
  const outP = interpolate(f, [dur - 12, dur], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const blur = (1 - inP) * 14 + outP * 14;
  const sweep = interpolate(f, [0, 16], [-40, 140], {extrapolateRight: 'clamp'});
  return <AbsoluteFill>
    <AbsoluteFill style={{opacity: inP * (1 - outP), filter: `blur(${blur}px)`,
      transform: `scale(${1.06 - 0.06 * inP + 0.05 * outP})`}}>{children}</AbsoluteFill>
    {f < 18 && <AbsoluteFill style={{pointerEvents: 'none', mixBlendMode: 'screen',
      background: `linear-gradient(100deg, transparent ${sweep - 12}%, rgba(62,230,255,0.35) ${sweep}%, rgba(139,92,246,0.25) ${sweep + 6}%, transparent ${sweep + 18}%)`}} />}
  </AbsoluteFill>;
};
