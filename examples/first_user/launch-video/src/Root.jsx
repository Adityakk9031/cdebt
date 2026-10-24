import {Composition} from 'remotion';
import {Video, totalFrames, FPS} from './Video';

export const Root = () => (
  <Composition
    id="CdebtLaunch"
    component={Video}
    durationInFrames={totalFrames}
    fps={FPS}
    width={1920}
    height={1080}
  />
);
