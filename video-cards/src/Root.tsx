import "./index.css";
import { Composition } from "remotion";
import { Card, calculateMetadata } from "./Card";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Card"
      component={Card}
      durationInFrames={84}  // default 3.5s @ 24fps; overridden by calculateMetadata
      fps={24}
      width={1280}
      height={720}
      defaultProps={{
        points: [
          "Key insight about the topic at hand",
          "Supporting point that reinforces the narrative",
          "Third critical takeaway viewers should remember",
        ],
        title: "KEY POINTS",
        duration: 3.5,
      }}
      calculateMetadata={calculateMetadata}
    />
  );
};
