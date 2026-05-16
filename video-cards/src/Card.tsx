import React from "react";
import {
  AbsoluteFill,
  CalculateMetadataFunction,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface CardProps {
  points: string[];
  title?: string;
  duration: number; // seconds — drives composition length via calculateMetadata
}

export const calculateMetadata: CalculateMetadataFunction<CardProps> = async ({
  props,
}) => ({
  durationInFrames: Math.max(24, Math.round(props.duration * 24)),
});

export const Card: React.FC<CardProps> = ({
  points = [],
  title = "KEY POINTS",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Label fade in
  const labelOpacity = interpolate(frame, [0, fps * 0.25], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  // Divider line grows right
  const lineScale = interpolate(frame, [fps * 0.15, fps * 0.55], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  return (
    <AbsoluteFill
      style={{
        background: "linear-gradient(135deg, #0d1117 0%, #0f1420 100%)",
        fontFamily:
          "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        overflow: "hidden",
      }}
    >
      {/* Ambient blue glow — top-left */}
      <div
        style={{
          position: "absolute",
          top: -160,
          left: -160,
          width: 520,
          height: 520,
          borderRadius: "50%",
          background: "rgba(59,130,246,0.09)",
          filter: "blur(90px)",
          pointerEvents: "none",
        }}
      />
      {/* Ambient purple glow — bottom-right */}
      <div
        style={{
          position: "absolute",
          bottom: -120,
          right: -80,
          width: 380,
          height: 380,
          borderRadius: "50%",
          background: "rgba(139,92,246,0.07)",
          filter: "blur(80px)",
          pointerEvents: "none",
        }}
      />

      {/* Section label */}
      <div
        style={{
          position: "absolute",
          top: 52,
          left: 80,
          fontSize: 18,
          fontWeight: 700,
          letterSpacing: "1.2px",
          textTransform: "uppercase",
          color: "#63707d",
          opacity: labelOpacity,
        }}
      >
        {title}
      </div>

      {/* Animated divider */}
      <div
        style={{
          position: "absolute",
          top: 92,
          left: 80,
          right: 80,
          height: 1,
          background: "rgba(255,255,255,0.08)",
          transformOrigin: "left center",
          transform: `scaleX(${lineScale})`,
        }}
      />

      {/* Bullet points — staggered spring reveals */}
      {points.slice(0, 4).map((point, i) => {
        const delay = fps * (0.35 + i * 0.22);
        const progress = spring({
          frame: frame - delay,
          fps,
          config: { damping: 18, stiffness: 90, mass: 0.9 },
          durationInFrames: Math.round(fps * 0.9),
        });

        const opacity = interpolate(progress, [0, 0.25, 1], [0, 0.5, 1]);
        const tx = interpolate(progress, [0, 1], [-36, 0]);

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              top: 118 + i * 128,
              left: 80,
              right: 80,
              display: "flex",
              alignItems: "flex-start",
              gap: 16,
              opacity,
              transform: `translateX(${tx}px)`,
            }}
          >
            {/* Bullet dot */}
            <div
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "#3b82f6",
                marginTop: 13,
                flexShrink: 0,
                boxShadow: "0 0 10px rgba(59,130,246,0.65)",
              }}
            />
            {/* Point text */}
            <div
              style={{
                fontSize: 27,
                fontWeight: 500,
                color: "#e2e4ec",
                lineHeight: 1.55,
              }}
            >
              {point}
            </div>
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
