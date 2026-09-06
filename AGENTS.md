# Video Cropper — agent operating instructions

This is a standalone, disposable local utility. Work only in this `Video Cropper` folder. Do not inspect, import, modify, or depend on the user's separate Forza project or any other project.

## Authoritative workspace contract

The active job lives entirely in `current_video/`:

```text
current_video/
├── <exactly one source video: .mp4, .mov, .m4v, or .mkv>
├── clips.json
└── outputs/
```

- The source must be directly inside `current_video/`; never look recursively for source videos.
- `current_video/outputs/` contains only clips for that one active source.
- `current_video/clips.json` contains only clipping instructions for that one active source.
- Never create a history system, database, archive, global manifest, prior-video directory, or output directory outside `current_video/`.
- Ignore hidden files, non-video files, and `outputs/` when considering source videos. Never guess between multiple source files.
- Do not add logos, watermarks, music, narration, sound effects, or audio gain/mixing. Add on-screen text only when the user requests it through a clip's `texts` configuration.

## Normal workflow for a clip request

When the user supplies clip names and time ranges, or says they edited `clips.json`:

1. Inspect `current_video/clips.json` and confirm there is exactly one supported top-level source video. Do not move or rename their source video unless they explicitly ask.
2. If the user supplied a new clip list, replace the **entire** `clips` array in `current_video/clips.json` with exactly that new list. Never append to, merge with, or preserve older clip entries: `clips.json` belongs only to the current source video and may be stale after the user clears or replaces it. Use this schema:

   ```json
   {
     "clips": [
       { "name": "01_intro", "start": "00:00:10.000", "end": "00:00:20.000" }
     ]
   }
   ```

   Each clip must use `start` plus exactly one of `end` or `duration`. Timestamps may be `HH:MM:SS`, `MM:SS`, with optional milliseconds. Use clear names; output names become safe `<name>.mp4` filenames.
3. Run `python3 video_cropper.py validate`. Resolve every validation error before rendering. In particular, do not silently adjust requested timings or choose a source video for the user.
4. Run `python3 video_cropper.py dry-run` whenever reviewing the plan is useful, when diagnosing a configuration change, or before the first render of a new workflow. It writes no files.
5. If the user requested clips to be produced, run `python3 video_cropper.py render`. Use `--jobs N` only when parallel encoding is appropriate for the available machine capacity.
6. If an output already exists, stop and report the exact conflicting file(s). Only run `render --overwrite` when the user explicitly authorizes replacing those outputs.
7. Report the rendered filenames and any failures succinctly. Do not claim a render succeeded without checking the command result.

For clips starting after `00:00:10`, the utility performs an accurate FFmpeg input seek to exactly ten seconds before the requested start before decoding; do not replace this with a command that decodes all earlier source footage. Starts at or before ten seconds intentionally begin from the source start. The utility trims away that pre-roll and preserves frame-accurate clip boundaries.

The user asking for specific clips is authorization to replace `clips.json` with that requested list and render those requested clips after validation. It is not authorization to overwrite clips from a prior configuration.

## On-screen text

Text is optional; omit `texts` for a gameplay-only clip. When the user requests text, add a `texts` array inside the relevant clip. Text timings are relative to that clip, not the original source. Each text item needs only `text` by default: it appears at clip time zero and remains through the end of that clip. For custom timing, accept a text-relative `start` plus exactly one of `end` or `duration`. The default color is `white` (`#FFFFFF`). The only named colors are `white` (`#FFFFFF`), `red` (`#FF3B30`), `green` (`#34C759`), and `yellow` (`#FFD60A`). For an exact custom color, require `#RRGGBB`; never infer a named color such as `orange`. `random` is an optional special value that deterministically chooses a stable bright color for the same clip/text/word; use a named preset or hex when an exact color is required. For selected words, use `word_colors` entries with one-based `word_index`; this is required to distinguish repeated words. Wrap long text automatically within the fixed 120-pixel left/right safe area, up to four lines; reject text exceeding that safe area instead of shrinking or clipping it. Do not alter the fixed text font, top placement, safe margins, line spacing, outline, or shadow unless the user explicitly asks to change the visual treatment.

## Output specification — do not alter

`video_cropper.py` owns the rendering pipeline. Do not replace it with ad-hoc FFmpeg commands or change its locked render behavior unless the user explicitly asks to change the utility itself.

- 1440×2560 (9:16), 60 fps, SDR BT.709, no HDR/PQ/HLG/tone-mapping.
- H.264 High, 8-bit `yuv420p`, CRF 18; AAC 48 kHz stereo at 192 kbps; MP4 faststart.
- Preserve gameplay audio content only, with clip video and audio beginning at timestamp zero.
- Background: source scale-cover/crop to 1440×2560, Gaussian blur about sigma 40, brightness about -0.18.
- Foreground: sharp 1440×1920 source scale-cover/center-crop at `x=0, y=320`.

## Testing and cleanup

- For a code/configuration check without a source video, run `python3 video_cropper.py self-test`. It verifies FFmpeg/FFprobe availability and runs a temporary end-to-end render; it never changes `current_video/`.
- `python3 video_cropper.py validate` is the required pre-render check once a source is present.
- `python3 video_cropper.py clear --yes` is destructive: it removes supported top-level source videos, `clips.json`, and `outputs/`, then recreates `outputs/`. Run it only when the user explicitly asks to reset/clear the active video workspace.
- Do not delete a user's source, clips, or outputs by any other means.

## Communication

State which source video and clip ranges you are about to use. If the source is absent, multiple, invalid, HDR-tagged, or a requested range is invalid, give the user the exact actionable issue. Prefer preserving the user's files and asking a concise question over making a timing or overwrite decision on their behalf.
