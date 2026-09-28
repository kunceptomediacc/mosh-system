import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import {TransitionSeries, linearTiming} from "@remotion/transitions";
import {fade} from "@remotion/transitions/fade";

export type Scene = {title: string; body: string; durationInFrames: number};
export type MoshVideoProps = {title: string; accent: string; scenes: Scene[]};

const TRANSITION_FRAMES = 15;

export const totalFrames = (scenes: Scene[]) =>
  Math.max(1, scenes.reduce((sum, scene) => sum + scene.durationInFrames, 0) - Math.max(0, scenes.length - 1) * TRANSITION_FRAMES);

const SceneCard = ({scene, accent, sequence}: {scene: Scene; accent: string; sequence: number}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const reveal = spring({frame, fps, config: {damping: 200}, durationInFrames: 30});
  const opacity = interpolate(frame, [0, 12], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  return (
    <AbsoluteFill style={{background: "#0b0b0c", color: "#f4efe7", padding: 120, justifyContent: "center", fontFamily: "Arial, sans-serif"}}>
      <div style={{color: accent, fontSize: 28, letterSpacing: 8}}>MOSH / {String(sequence).padStart(2, "0")}</div>
      <h1 style={{fontSize: 112, lineHeight: 0.95, maxWidth: 1350, margin: "34px 0", opacity, transform: `translateY(${(1 - reveal) * 44}px)`}}>{scene.title}</h1>
      <p style={{fontSize: 46, maxWidth: 1050, lineHeight: 1.25, opacity: reveal}}>{scene.body}</p>
      <div style={{position: "absolute", left: 120, right: 120, bottom: 92, height: 4, background: "#29292d"}}>
        <div style={{height: "100%", width: `${Math.min(100, frame / Math.max(1, scene.durationInFrames - 1) * 100)}%`, background: accent}} />
      </div>
    </AbsoluteFill>
  );
};

export const MoshVideo = ({title, accent, scenes}: MoshVideoProps) => {
  if (!scenes.length) return <AbsoluteFill style={{background: "#0b0b0c", color: "white", justifyContent: "center", alignItems: "center"}}>{title}</AbsoluteFill>;
  return (
    <TransitionSeries>
      {scenes.flatMap((scene, index) => {
        const sequence = (
          <TransitionSeries.Sequence key={`scene-${index}`} durationInFrames={scene.durationInFrames} premountFor={30}>
            <SceneCard scene={scene} accent={accent} sequence={index + 1} />
          </TransitionSeries.Sequence>
        );
        if (index === scenes.length - 1) return [sequence];
        return [sequence, <TransitionSeries.Transition key={`transition-${index}`} presentation={fade()} timing={linearTiming({durationInFrames: TRANSITION_FRAMES})} />];
      })}
    </TransitionSeries>
  );
};
