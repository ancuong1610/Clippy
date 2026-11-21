import { Composition, Folder } from "remotion";
import { Promo } from "./promo/Promo";
import { SceneChat, SceneCta, SceneCut, SceneHook, SceneJudge, SceneLearn, ScenePost } from "./promo/scenes";

const S = { fps: 30, width: 1080, height: 1920 };

export const RemotionRoot: React.FC = () => (
  <>
    <Folder name="Promo-Scenes">
      <Composition id="Hook" component={SceneHook} durationInFrames={115} {...S} />
      <Composition id="Chat" component={SceneChat} durationInFrames={140} {...S} />
      <Composition id="Judge" component={SceneJudge} durationInFrames={165} {...S} />
      <Composition id="Cut" component={SceneCut} durationInFrames={155} {...S} />
      <Composition id="Post" component={ScenePost} durationInFrames={190} {...S} />
      <Composition id="Learn" component={SceneLearn} durationInFrames={150} {...S} />
      <Composition id="CTA" component={SceneCta} durationInFrames={140} {...S} />
    </Folder>
    <Composition id="Promo" component={Promo} durationInFrames={1055 - 6 * 8} {...S} />
  </>
);
