import { loadFont } from "@remotion/google-fonts/Inter";
import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";

export const { fontFamily } = loadFont("normal", { weights: ["500", "700", "900"], subsets: ["latin"] });

export const C = {
  bg: "#0B0B10",
  card: "#16161F",
  line: "#2A2A38",
  text: "#F5F5F7",
  dim: "#9A9AB0",
  accent: "#FFD400",
  good: "#3DDC84",
  bad: "#FF4D5E",
};

export const FPS = 30;

const ease = Easing.bezier(0.16, 1, 0.3, 1);

/** Fade + slide up, starting at `at` frames. */
export const useIn = (at = 0, dur = 12) => {
  const f = useCurrentFrame();
  return {
    opacity: interpolate(f, [at, at + dur], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
    translate: interpolate(f, [at, at + dur], ["0px 40px", "0px 0px"], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: ease,
    }),
  };
};

/** Big headline at the top of the frame, one highlighted word in the accent colour (wrap it in *stars*). */
export const Headline: React.FC<{ text: string; at?: number; size?: number; top?: number }> = ({
  text,
  at = 0,
  size = 92,
  top = 170,
}) => {
  const style = useIn(at);
  const parts = text.split("*");
  return (
    <div
      style={{
        position: "absolute",
        top,
        left: 80,
        right: 80,
        fontFamily,
        fontWeight: 900,
        fontSize: size,
        lineHeight: 1.05,
        letterSpacing: -2,
        color: C.text,
        ...style,
      }}
    >
      {parts.map((p, i) => (
        <span key={i} style={{ color: i % 2 ? C.accent : C.text }}>
          {p}
        </span>
      ))}
    </div>
  );
};

export const Bg: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ position: "absolute", inset: 0, background: C.bg, fontFamily, overflow: "hidden" }}>{children}</div>
);

export const Step: React.FC<{ n: number }> = ({ n }) => (
  <div
    style={{
      position: "absolute",
      top: 90,
      left: 80,
      fontFamily,
      fontWeight: 700,
      fontSize: 34,
      color: C.dim,
      letterSpacing: 4,
    }}
  >
    STEP {n} / 5
  </div>
);
