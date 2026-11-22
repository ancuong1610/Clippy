import React from "react";
import { Audio, Easing, Img, interpolate, OffthreadVideo, staticFile, useCurrentFrame } from "remotion";
import { Bg, C, FPS, Headline, Step, useIn } from "./theme";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const Vo: React.FC<{ id: string }> = ({ id }) => <Audio src={staticFile(`vo/${id}.wav`)} />;
const pop = Easing.spring({ damping: 14 });

// ------------------------------------------------------------------ 1. hook: the crash, with the promise on top
export const SceneHook: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Bg>
      <Vo id="hook" />
      <OffthreadVideo
        src={staticFile("cow.mp4")}
        startFrom={Math.round(9.2 * FPS)}
        volume={0.45}
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(180deg, rgba(11,11,16,0.92) 0%, rgba(11,11,16,0.55) 30%, rgba(11,11,16,0) 50%)",
        }}
      />
      <Headline text={"My AI clips *GTA 6* streamers while I sleep."} size={96} top={150} />
      <div
        style={{
          position: "absolute",
          bottom: 170,
          left: 80,
          fontWeight: 700,
          fontSize: 44,
          color: C.text,
          background: C.bad,
          padding: "10px 22px",
          borderRadius: 14,
          opacity: interpolate(f, [40, 48], [0, 1], clamp),
          scale: interpolate(f, [40, 52], [0.6, 1], { ...clamp, easing: pop }),
        }}
      >
        cut & edited by AI
      </div>
    </Bg>
  );
};

// ------------------------------------------------------------------ 2. chat spike (real xQc chat, msgs per second)
const CHAT = [8, 7, 7, 6, 7, 5, 3, 5, 4, 7, 7, 3, 2, 12, 8, 4, 3, 2, 4, 5, 8, 6, 9, 7, 13, 5, 10, 7, 13, 11, 13, 34, 30, 35, 38];
const MSGS: [string, number, number][] = [
  ["THE COW D:", 90, 430],
  ["MOOOO", 560, 430],
  ["LMFAOOOOO", 220, 530],
  ["MR COW LO", 640, 530],
  ["D: THE COWS", 90, 630],
  ["OMEGALUL", 560, 630],
];

export const SceneChat: React.FC = () => {
  const f = useCurrentFrame();
  const shown = interpolate(f, [10, 60], [0, CHAT.length], clamp);
  const max = 40;
  return (
    <Bg>
      <Vo id="chat" />
      <Step n={1} />
      <Headline text={"It watches the *chat.*"} />
      <Headline text={"When chat explodes, it clips."} at={8} size={52} top={290} />
      <div style={{ position: "absolute", left: 80, right: 80, top: 760, height: 520, display: "flex", alignItems: "flex-end", gap: 8 }}>
        {CHAT.map((v, i) => {
          const spike = i >= 31;
          const h = interpolate(shown, [i, i + 1], [0, (v / max) * 520], clamp);
          return (
            <div key={i} style={{ flex: 1, height: h, borderRadius: 6, background: spike ? C.accent : C.line }} />
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 80, top: 1300, fontSize: 34, color: C.dim, fontWeight: 500 }}>
        real xQc chat · messages per second
      </div>
      {MSGS.map(([m, left, top], i) => {
        const at = 58 + i * 4;
        return (
          <div
            key={m}
            style={{
              position: "absolute",
              left,
              top,
              fontSize: 44,
              fontWeight: 900,
              color: C.text,
              background: C.card,
              border: `3px solid ${C.accent}`,
              padding: "8px 20px",
              borderRadius: 40,
              opacity: interpolate(f, [at, at + 4], [0, 1], clamp),
              scale: interpolate(f, [at, at + 8], [0.4, 1], { ...clamp, easing: pop }),
            }}
          >
            {m}
          </div>
        );
      })}
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 1420,
          fontSize: 60,
          fontWeight: 900,
          color: C.accent,
          opacity: interpolate(f, [70, 80], [0, 1], clamp),
        }}
      >
        ✂ CLIP SAVED
      </div>
    </Bg>
  );
};

// ------------------------------------------------------------------ 3. AI judge: real verdicts
const Card: React.FC<{ img: string; score: number; ok: boolean; label: string; at: number; top: number }> = ({
  img,
  score,
  ok,
  label,
  at,
  top,
}) => {
  const f = useCurrentFrame();
  const style = useIn(at);
  const n = Math.round(interpolate(f, [at + 6, at + 30], [0, score], clamp));
  return (
    <div
      style={{
        position: "absolute",
        left: 80,
        right: 80,
        top,
        height: 400,
        display: "flex",
        gap: 36,
        background: C.card,
        borderRadius: 32,
        padding: 28,
        border: `4px solid ${ok ? C.good : C.bad}`,
        ...style,
      }}
    >
      <Img src={staticFile(img)} style={{ width: 300, height: 344, objectFit: "cover", borderRadius: 20 }} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
        <div style={{ fontSize: 150, fontWeight: 900, color: ok ? C.good : C.bad, lineHeight: 1 }}>{n}</div>
        <div style={{ fontSize: 40, fontWeight: 700, color: C.text, marginTop: 14 }}>{label}</div>
        <div style={{ fontSize: 44, fontWeight: 900, color: ok ? C.good : C.bad, marginTop: 14 }}>
          {ok ? "✓ POST IT" : "✗ SKIP"}
        </div>
      </div>
    </div>
  );
};

export const SceneJudge: React.FC = () => (
  <Bg>
      <Vo id="judge" />
    <Step n={2} />
    <Headline text={"An AI judge scores *every* moment."} />
    <Headline text={"Only 90+ survives."} at={8} size={52} top={400} />
    <Card img="cutscene.jpg" score={35} ok={false} label="story cutscene, nobody reacts" at={18} top={540} />
    <Card img="cow_hit.jpg" score={100} ok label="quad bike vs. a herd of cows" at={46} top={1000} />
  </Bg>
);

// ------------------------------------------------------------------ 4. the hook: stop right before impact
export const SceneCut: React.FC = () => {
  const f = useCurrentFrame();
  // clip plays from 8.0 s of the short; impact at ~10.6 s = scene frame 78 (scene is 155 frames)
  const impact = 78;
  const p = interpolate(f, [0, 155], [0, 1], clamp);
  return (
    <Bg>
      <Vo id="cut" />
      <Step n={3} />
      <Headline text={"The hook stops *one beat* before the crash."} size={84} />
      <div style={{ position: "absolute", left: 140, right: 140, top: 560, height: 900, borderRadius: 28, overflow: "hidden" }}>
        <OffthreadVideo
          src={staticFile("cow.mp4")}
          startFrom={Math.round(8.0 * FPS)}
          volume={0.3}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </div>
      {/* timeline */}
      <div style={{ position: "absolute", left: 140, right: 140, top: 1520, height: 14, background: C.line, borderRadius: 7 }}>
        <div style={{ width: `${p * 100}%`, height: "100%", background: C.text, borderRadius: 7 }} />
        <div
          style={{
            position: "absolute",
            left: `${(impact / 155) * 100 - 15}%`,
            width: "15%",
            top: -10,
            height: 34,
            borderRadius: 8,
            background: C.accent,
            opacity: 0.85,
          }}
        />
        <div style={{ position: "absolute", left: `${(impact / 155) * 100}%`, top: -30, width: 6, height: 74, background: C.bad }} />
      </div>
      <div style={{ position: "absolute", left: 140, top: 1580, fontSize: 40, fontWeight: 700, color: C.accent }}>■ hook (shown first)</div>
      <div style={{ position: "absolute", right: 140, top: 1580, fontSize: 40, fontWeight: 700, color: C.bad }}>| impact</div>
      <div
        style={{
          position: "absolute",
          left: 80,
          right: 80,
          top: 1680,
          fontSize: 44,
          fontWeight: 700,
          color: C.text,
          opacity: interpolate(f, [impact + 6, impact + 14], [0, 1], clamp),
        }}
      >
        so you <span style={{ color: C.accent }}>have</span> to keep watching.
      </div>
    </Bg>
  );
};

// ------------------------------------------------------------------ 5. edit + post
const PLATFORMS = ["YouTube Shorts", "Instagram Reels", "TikTok"];

export const ScenePost: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Bg>
      <Vo id="post" />
      <Step n={4} />
      <Headline text={"Facecam, captions, title."} size={84} />
      <Headline text={"Then it *posts* on a schedule."} at={8} size={56} top={370} />
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 520,
          width: 520,
          height: 924,
          borderRadius: 48,
          border: `10px solid ${C.line}`,
          overflow: "hidden",
          ...useIn(10),
        }}
      >
        <OffthreadVideo src={staticFile("cow.mp4")} startFrom={Math.round(4.0 * FPS)} volume={0.25} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </div>
      {PLATFORMS.map((p, i) => {
        const at = 30 + i * 12;
        return (
          <div
            key={p}
            style={{
              position: "absolute",
              left: 640,
              right: 60,
              top: 640 + i * 190,
              padding: "26px 24px",
              borderRadius: 24,
              background: C.card,
              border: `3px solid ${C.good}`,
              fontSize: 40,
              fontWeight: 700,
              color: C.text,
              opacity: interpolate(f, [at, at + 6], [0, 1], clamp),
              translate: interpolate(f, [at, at + 10], ["60px 0px", "0px 0px"], { ...clamp, easing: pop }),
            }}
          >
            ✓ {p}
          </div>
        );
      })}
      <div style={{ position: "absolute", left: 640, right: 60, top: 1240, fontSize: 36, color: C.dim, fontWeight: 500, opacity: interpolate(f, [70, 80], [0, 1], clamp) }}>
        19:00 · 22:00 · 01:00
        <br />
        best clips first
      </div>
    </Bg>
  );
};

// ------------------------------------------------------------------ 6. analytics -> learning (real numbers, first 4 days)
const STATS: [string, number][] = [
  ["Instagram", 2122],
  ["YouTube", 1349],
  ["TikTok", 986],
];

export const SceneLearn: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Bg>
      <Vo id="learn" />
      <Step n={5} />
      <Headline text={"Then it reads the *analytics*"} size={84} />
      <Headline text={"and learns what goes viral."} at={8} size={56} top={370} />
      {STATS.map(([name, v], i) => {
        const at = 20 + i * 8;
        const w = interpolate(f, [at, at + 24], [0, (v / 2122) * 100], { ...clamp, easing: Easing.bezier(0.16, 1, 0.3, 1) });
        return (
          <div key={name} style={{ position: "absolute", left: 80, right: 80, top: 560 + i * 170 }}>
            <div style={{ fontSize: 40, fontWeight: 700, color: C.text, marginBottom: 12 }}>
              {name} <span style={{ color: C.accent }}>{Math.round((w / 100) * 2122).toLocaleString("en-US")}</span>
            </div>
            <div style={{ height: 40, width: `${w}%`, background: i === 0 ? C.accent : C.line, borderRadius: 10 }} />
          </div>
        );
      })}
      <div style={{ position: "absolute", left: 80, top: 1110, fontSize: 34, color: C.dim, fontWeight: 500 }}>views · our first 4 days</div>
      <div
        style={{
          position: "absolute",
          left: 80,
          right: 80,
          top: 1220,
          background: C.card,
          borderRadius: 24,
          padding: 36,
          fontSize: 38,
          lineHeight: 1.4,
          color: C.text,
          fontWeight: 500,
          ...useIn(60),
        }}
      >
        <div style={{ color: C.dim, fontSize: 30, letterSpacing: 3, marginBottom: 12 }}>LEARNINGS.MD</div>
        Titles that name a <b style={{ color: C.accent }}>real object</b> win.
        <br />
        Story cutscenes get skipped.
      </div>
    </Bg>
  );
};

// ------------------------------------------------------------------ 7. call to action (built to drive comments)
export const SceneCta: React.FC = () => {
  const f = useCurrentFrame();
  const typed = "CLIPPY".slice(0, Math.max(0, Math.floor((f - 30) / 4)));
  return (
    <Bg>
      <Vo id="cta" />
      <Headline text={"Want the *setup?*"} size={110} top={420} />
      <Headline text={"Comment the word below and I'll send it to you."} at={10} size={56} top={580} />
      <div
        style={{
          position: "absolute",
          left: 80,
          right: 80,
          top: 900,
          height: 160,
          borderRadius: 80,
          background: C.card,
          border: `4px solid ${C.accent}`,
          display: "flex",
          alignItems: "center",
          padding: "0 50px",
          fontSize: 84,
          fontWeight: 900,
          color: C.text,
          letterSpacing: 4,
          ...useIn(20),
        }}
      >
        {typed}
        <span style={{ opacity: Math.floor(f / 8) % 2 ? 0 : 1, color: C.accent }}>|</span>
      </div>
      <div
        style={{
          position: "absolute",
          left: 80,
          top: 1140,
          fontSize: 120,
          opacity: interpolate(f, [60, 66], [0, 1], clamp),
          translate: interpolate(f % 30, [0, 15, 30], ["0px 0px", "0px 20px", "0px 0px"]),
        }}
      >
        👇
      </div>
    </Bg>
  );
};

// ------------------------------------------------------------------ 1b. snapback: the real problem (12 h -> 30 s)
export const SceneSnap: React.FC = () => {
  const f = useCurrentFrame();
  const at = 0.63; // where the moment sits in the stream bar
  const zoom = interpolate(f, [95, 120], [0, 1], { ...clamp, easing: Easing.bezier(0.16, 1, 0.3, 1) });
  return (
    <Bg>
      <Vo id="snap" />
      <Headline text={"But *clipping* isn't the hard part."} size={88} />
      <Headline text={"Finding the *30 seconds* that go viral is."} at={62} size={64} top={400} />
      {/* the whole stream */}
      <div style={{ position: "absolute", left: 80, right: 80, top: 720, ...useIn(20) }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 36, fontWeight: 700, color: C.dim, marginBottom: 16 }}>
          <span>0:00:00</span>
          <span>one stream</span>
          <span>12:00:00</span>
        </div>
        <div style={{ position: "relative", height: 90, borderRadius: 14, background: C.line, overflow: "hidden" }}>
          {Array.from({ length: 48 }).map((_, i) => (
            <div key={i} style={{ position: "absolute", left: `${(i / 48) * 100}%`, top: 0, bottom: 0, width: 2, background: C.bg, opacity: 0.6 }} />
          ))}
          <div
            style={{
              position: "absolute",
              left: `${at * 100}%`,
              top: 0,
              bottom: 0,
              width: 10,
              background: C.accent,
              opacity: interpolate(f, [70, 76], [0, 1], clamp),
              boxShadow: `0 0 ${interpolate(f % 20, [0, 10, 20], [10, 30, 10])}px ${C.accent}`,
            }}
          />
        </div>
      </div>
      {/* zoom lines from the sliver to the moment */}
      <svg style={{ position: "absolute", left: 0, top: 0, width: 1080, height: 1920, opacity: zoom }}>
        <line x1={80 + at * 920} y1={860} x2={190} y2={1040} stroke={C.accent} strokeWidth={4} strokeDasharray="10 10" />
        <line x1={90 + at * 920} y1={860} x2={890} y2={1040} stroke={C.accent} strokeWidth={4} strokeDasharray="10 10" />
      </svg>
      <div
        style={{
          position: "absolute",
          left: 190,
          right: 190,
          top: 1040,
          height: 560,
          borderRadius: 28,
          overflow: "hidden",
          border: `5px solid ${C.accent}`,
          opacity: zoom,
          scale: interpolate(zoom, [0, 1], [0.7, 1]),
        }}
      >
        <Img src={staticFile("cow_hit.jpg")} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        <div style={{ position: "absolute", left: 24, bottom: 20, fontSize: 52, fontWeight: 900, color: C.text, background: C.bg, padding: "6px 18px", borderRadius: 12 }}>
          0:30 <span style={{ color: C.accent }}>that matters</span>
        </div>
      </div>
    </Bg>
  );
};
