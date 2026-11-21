# Clippy demo video

Remotion project for the 34 s vertical demo "My AI clips GTA 6 streamers while I sleep"
([watch it](https://www.instagram.com/reel/DgBSkCRjIR9/)). Seven scenes in `src/promo/scenes.tsx`, built from the
repo's own data: real xQc chat rates, real judge scores, real view counts.

```bash
npm i
npx remotion studio                                   # preview / edit
npx remotion render Promo out/demo.mp4 --codec=h264   # render
```

- `public/cow.mp4`, `cow_hit.jpg`, `cutscene.jpg` are frames/clips of streamer footage and are git-ignored: copy a
  rendered Short from `queue/posted/<id>/short.mp4` to `public/cow.mp4` and grab the stills with ffmpeg.
- `public/vo/*.wav` is the voice-over, one line per scene, made locally with
  [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0, voice `af_heart`, speed 1.1) via
  `mlx-audio`. Scene lengths in `src/Root.tsx` / `src/promo/Promo.tsx` are set to fit each line.
- `remotion.config.ts` uses `chrome-for-testing`: the default headless shell crashed on this Mac.
- Loudness for social: `ffmpeg -i out/demo.mp4 -c:v copy -af loudnorm=I=-14:TP=-1.5 out/final.mp4`.
