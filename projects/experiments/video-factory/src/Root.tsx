import type {CalculateMetadataFunction} from "remotion";
import {Composition, Folder} from "remotion";
import {MoshVideo, type MoshVideoProps, totalFrames} from "./Video";

const defaultProps = {
  title: "Everything moving. Nothing lost.",
  accent: "#b8ff5a",
  scenes: [
    {title: "Throw it in", body: "Ideas become durable work.", durationInFrames: 90},
    {title: "Route with evidence", body: "Agents change. Tasks survive.", durationInFrames: 105},
    {title: "Stay in control", body: "Production and publishing remain separate.", durationInFrames: 90}
  ]
} satisfies MoshVideoProps;

const calculateMetadata: CalculateMetadataFunction<MoshVideoProps> = async ({props}) => ({
  durationInFrames: totalFrames(props.scenes),
  defaultOutName: "mosh-video-preview.mp4"
});

export const RemotionRoot = () => (
  <Folder name="MOSH-Video-Factory">
    <Composition
      id="MoshVideo"
      component={MoshVideo}
      durationInFrames={totalFrames(defaultProps.scenes)}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaultProps}
      calculateMetadata={calculateMetadata}
    />
  </Folder>
);
