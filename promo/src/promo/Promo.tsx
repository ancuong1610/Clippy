import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import React from "react";
import { SceneChat, SceneCta, SceneCut, SceneHook, SceneJudge, SceneLearn, ScenePost } from "./scenes";

const T = 8; // transition frames

export const Promo: React.FC = () => (
  <TransitionSeries>
    <TransitionSeries.Sequence durationInFrames={115} name="Hook">
      <SceneHook />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={slide({ direction: "from-bottom" })} timing={linearTiming({ durationInFrames: T })} />
    <TransitionSeries.Sequence durationInFrames={140} name="Chat">
      <SceneChat />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={slide({ direction: "from-right" })} timing={linearTiming({ durationInFrames: T })} />
    <TransitionSeries.Sequence durationInFrames={165} name="Judge">
      <SceneJudge />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={slide({ direction: "from-right" })} timing={linearTiming({ durationInFrames: T })} />
    <TransitionSeries.Sequence durationInFrames={155} name="Cut">
      <SceneCut />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={slide({ direction: "from-right" })} timing={linearTiming({ durationInFrames: T })} />
    <TransitionSeries.Sequence durationInFrames={190} name="Post">
      <ScenePost />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={slide({ direction: "from-right" })} timing={linearTiming({ durationInFrames: T })} />
    <TransitionSeries.Sequence durationInFrames={150} name="Learn">
      <SceneLearn />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={fade()} timing={linearTiming({ durationInFrames: T })} />
    <TransitionSeries.Sequence durationInFrames={140} name="CTA">
      <SceneCta />
    </TransitionSeries.Sequence>
  </TransitionSeries>
);
