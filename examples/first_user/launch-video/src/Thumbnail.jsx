import {AbsoluteFill, Img, staticFile} from 'remotion';

// the README / social thumbnail: a real PR screenshot behind the title, with a play button
export const Thumbnail = () => (
  <AbsoluteFill style={{background: '#070b18', fontFamily: '-apple-system, "Helvetica Neue", Arial, sans-serif'}}>
    <AbsoluteFill style={{background:
      'radial-gradient(900px 600px at 25% 25%, rgba(139,92,246,0.35), transparent 70%),' +
      'radial-gradient(800px 600px at 80% 80%, rgba(62,230,255,0.28), transparent 70%)'}} />
    <Img src={staticFile('shots/pr-comment.png')} style={{position: 'absolute', right: -80, top: 250, width: 1150,
      borderRadius: 22, transform: 'perspective(1800px) rotateY(-22deg) rotateX(8deg)', opacity: 0.9,
      boxShadow: '0 40px 120px rgba(0,0,0,0.7), 0 0 0 1px rgba(62,230,255,0.4), 0 0 70px rgba(62,230,255,0.25)'}} />
    <div style={{position: 'absolute', left: 110, top: 200, width: 900}}>
      <div style={{fontSize: 210, fontWeight: 800, color: '#e8eefc', lineHeight: 1,
        textShadow: '0 0 40px rgba(62,230,255,0.55), 0 0 90px rgba(139,92,246,0.45)'}}>cdebt</div>
      <div style={{fontSize: 64, fontWeight: 700, color: '#e8eefc', marginTop: 30, lineHeight: 1.15}}>
        Who actually <span style={{color: '#3ee6ff'}}>understood</span> the code your AI wrote?</div>
      <div style={{fontSize: 36, color: '#8a96b8', marginTop: 28}}>
        A quiz before you push · answers checked by running the code · shown on the PR</div>
    </div>
    <div style={{position: 'absolute', left: 110, bottom: 120, display: 'flex', alignItems: 'center', gap: 28}}>
      <div style={{width: 150, height: 150, borderRadius: '50%', background: '#ff3b3b', display: 'flex',
        alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 60px rgba(255,59,59,0.7)'}}>
        <div style={{width: 0, height: 0, marginLeft: 14, borderTop: '34px solid transparent',
          borderBottom: '34px solid transparent', borderLeft: '58px solid white'}} />
      </div>
      <div style={{fontSize: 46, fontWeight: 700, color: '#e8eefc'}}>Watch the demo · 3 min</div>
    </div>
  </AbsoluteFill>
);
