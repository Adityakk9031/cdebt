import {AbsoluteFill, Composition, Still} from 'remotion';
import {Thumbnail} from './Thumbnail';
import {Video, totalFrames, FPS} from './Video';

// 720p version for the README (GitHub video uploads are limited to 10 MB): the same 1080p design,
// scaled down exactly
const Video720 = () => (
  <AbsoluteFill style={{overflow: 'hidden'}}>
    <div style={{position: 'relative', width: 1920, height: 1080, transform: 'scale(0.6666667)', transformOrigin: 'top left'}}>
      <Video />
    </div>
  </AbsoluteFill>
);

export const Root = () => (
  <>
    <Composition id="CdebtLaunch" component={Video} durationInFrames={totalFrames} fps={FPS}
      width={1920} height={1080} />
    <Composition id="CdebtLaunch720" component={Video720} durationInFrames={totalFrames} fps={FPS}
      width={1280} height={720} />
    <Still id="Thumbnail" component={Thumbnail} width={1920} height={1080} />
  </>
);
