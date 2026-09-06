# Video Cropper

`Video Cropper` is a disposable, local utility for turning exactly one landscape 16:9 gameplay video into several clean vertical social clips. It uses Python's standard library plus locally installed `ffmpeg` and `ffprobe`; it has no database, history, archive, cloud service, or dependency on any other project.

## Folder contract

Only these project files and the active workspace are used:

```text
Video Cropper/
├── video_cropper.py
├── README.md
└── current_video/
    ├── clips.json
    ├── your_gameplay_video.mp4   # exactly one top-level source video
    └── outputs/                  # rendered clips for that source only
```

Put one `.mp4`, `.mov`, `.m4v`, or `.mkv` directly in `current_video/`. The tool ignores hidden files, non-video files, and everything in `outputs/`. It stops with a clear error if there are zero or multiple top-level source videos—there is never automatic source selection.

When switching videos, run `python3 video_cropper.py clear --yes`, then put the next video and its new `clips.json` in `current_video/`. `clear` removes only supported top-level source video files, `clips.json`, and `outputs/`; it recreates an empty `outputs/` folder and leaves unrelated files alone.

## Clip configuration

Edit [`current_video/clips.json`](current_video/clips.json):

```json
{
  "clips": [
    { "name": "01_intro", "start": "00:00:10.000", "end": "00:00:20.000" },
    { "name": "02_jump", "start": "00:01:20.000", "end": "00:01:40.000" }
  ]
}
```

Each clip needs a unique `name`, a `start`, and exactly one of `end` or `duration`. Timestamps support `HH:MM:SS`, `MM:SS`, and optional milliseconds. Every range is validated before any render: its end must follow its start and fall within the source duration. Names are converted to safe filenames; collisions after sanitization are rejected. The rendered filename is `<safe-name>.mp4`.

When working through Codex, each new request that supplies clip timings replaces the entire `clips` list for the active video. Old entries are never appended or carried into a new request, so stale instructions from a video you already deleted cannot be rendered accidentally.

## How to use the tool

1. Put exactly one supported landscape video directly in `current_video/`.
2. Replace the entire contents of `current_video/clips.json` with one of the examples below, changing its times and text as needed.
3. From the `Video Cropper` folder, run these commands in order:

```bash
python3 video_cropper.py validate
```

```bash
python3 video_cropper.py dry-run
```

```bash
python3 video_cropper.py render
```

`validate` checks the source and configuration. `dry-run` prints the FFmpeg plan without creating files. `render` writes the requested MP4 files to `current_video/outputs/`. The default render is sequential: one clip at a time. Use `python3 video_cropper.py render --jobs 2` or `--jobs 3` only when you want parallel encodes. If an output with the same filename already exists, review it first; use `python3 video_cropper.py render --overwrite` only when you intend to replace it.

The commands are identical for every example; the JSON in `clips.json` is what chooses the clip timings, text, and colors.

### Optional on-screen text

Text is optional: omit `texts` to render gameplay only. When present, each item has a required `text`; all other text timing fields are optional. Text is top-centred, uses the fixed display treatment, respects the 120-pixel left/right safe area, wraps automatically to at most four lines, and appears/disappears without animation.

- With only `text`, the caption starts at the clip's first frame and remains visible through its last frame.
- To customize timing, use a text-relative `start` with either `end` or `duration`. Text timestamps are relative to the start of that clip, not the source video.
- Use one-based `word_index` values in `word_colors` to color selected words independently, including repeated words.

### Color reference

`color` defaults to `white`. These are the only accepted named colors:

| Value to put in JSON | Rendered color | Exact hex value |
| --- | --- | --- |
| `white` | White | `#FFFFFF` |
| `red` | Red | `#FF3B30` |
| `green` | Green | `#34C759` |
| `yellow` | Yellow | `#FFD60A` |

For any other exact color, use a six-digit hex value in `#RRGGBB` form. For example, the orange used in example 5 is explicitly `#FF8A00`; the word `orange` is **not** accepted as a color name. Invalid names or malformed hex values fail validation before rendering.

`random` is an optional special value requested for automatic color selection. It calculates one bright hex color from the clip/text/word data and keeps that same result on re-renders of the same configuration. It does **not** choose a new color for every frame or every render. Use a named preset or a specific hex value whenever you want an exact predictable color.

## Configuration examples

### 1. Render a clip with no text

```json
{
  "clips": [
    { "name": "01_intro", "start": "00:00:10", "end": "00:00:20" }
  ]
}
```

Run example 1 with the three commands in [How to use the tool](#how-to-use-the-tool). It creates `current_video/outputs/01_intro.mp4` with gameplay only.

### 2. Use a clip duration instead of an end time

```json
{
  "clips": [
    { "name": "02_jump", "start": "01:20", "duration": "00:20" }
  ]
}
```

Run example 2 with the three commands in [How to use the tool](#how-to-use-the-tool). It creates a 20-second `02_jump.mp4`.

### 3. Add default white text for the full clip

```json
{
  "clips": [
    {
      "name": "03_full_caption",
      "start": "00:02:00",
      "duration": "00:08",
      "texts": [
        { "text": "Clean racing hits different in racing games" }
      ]
    }
  ]
}
```

Run example 3 with the three commands in [How to use the tool](#how-to-use-the-tool). The white caption is visible from the first to the final frame of `03_full_caption.mp4`.

### 4. Set one color for the full caption

```json
{
  "clips": [
    {
      "name": "04_green_caption",
      "start": "02:10",
      "end": "02:20",
      "texts": [
        { "text": "This whole caption is green", "color": "green" }
      ]
    }
  ]
}
```

Run example 4 with the three commands in [How to use the tool](#how-to-use-the-tool). The full caption in `04_green_caption.mp4` uses the `green` preset.

### 5. Color particular words with presets or hex values

```json
{
  "clips": [
    {
      "name": "05_colored_words",
      "start": "00:30:00",
      "end": "00:30:10",
      "texts": [
        {
          "text": "Clean racing hits different in racing games",
          "word_colors": [
            { "word_index": 2, "color": "green" },
            { "word_index": 6, "color": "yellow" },
            { "word_index": 4, "color": "#FF8A00" }
          ]
        }
      ]
    }
  ]
}
```

Run example 5 with the three commands in [How to use the tool](#how-to-use-the-tool). The word indexes are counted from left to right, starting at 1.

### 6. Show text for only part of the clip

```json
{
  "clips": [
    {
      "name": "06_timed_caption",
      "start": "00:30:00",
      "duration": "00:12",
      "texts": [
        {
          "text": "The caption appears at two seconds for three seconds",
          "start": "00:00:02",
          "duration": "00:00:03",
          "color": "#00C2FF"
        }
      ]
    }
  ]
}
```

Run example 6 with the three commands in [How to use the tool](#how-to-use-the-tool). Its caption appears at clip time two seconds and disappears after three seconds.

### 7. Use a stable automatic color (`random`)

```json
{
  "clips": [
    {
      "name": "07_random_color",
      "start": "03:00",
      "end": "03:08",
      "texts": [
        { "text": "This uses a stable generated color", "color": "random" }
      ]
    }
  ]
}
```

Run example 7 with the three commands in [How to use the tool](#how-to-use-the-tool). `random` calculates one stable bright color for this exact caption; use a named preset or hex value instead when you need an exact color.

## Commands

From this folder:

```bash
python3 video_cropper.py validate
python3 video_cropper.py dry-run
python3 video_cropper.py render
python3 video_cropper.py render --jobs 2
python3 video_cropper.py render --jobs 3
python3 video_cropper.py render --overwrite
python3 video_cropper.py clear --yes
```

`validate` checks FFmpeg, source discovery, video/audio streams, source duration, and all clipping instructions. `dry-run` performs the same validation and prints the exact safe FFmpeg argument commands without writing files. `render` runs all clips (one job by default); it refuses to replace an existing output unless `--overwrite` is explicit. Each clip renders first to a temporary file and is moved into place only after FFmpeg succeeds.

For a clip whose requested start is more than 10 seconds into the source, rendering uses FFmpeg's accurate input seek to ten seconds before that timestamp before it decodes or encodes. Clips starting at 10 seconds or earlier simply begin from the source start. The final filter trims away the ten-second pre-roll and preserves frame-accurate requested boundaries without decoding unrelated earlier footage.

Run the small built-in automated verification with:

```bash
python3 video_cropper.py self-test
```

`self-test` is safe to run without an active source video. It checks that both `ffmpeg` and `ffprobe` are installed and runnable, validates timestamp/name/source-discovery rules, checks start/end and start/duration configuration, text defaults and custom timings, preset/hex/random word colors, four-line safe-area limits, and the ten-second seek rule. It also creates a tiny temporary video, renders a captioned clip, and verifies its H.264/AAC streams, dimensions, frame rate, BT.709 tags, zero timestamps, duration, and MP4 faststart layout with `ffprobe`. No files in `current_video/` are changed.

## Render specification

Every output is rendered (never stream-copied) with frame-accurate video and audio trimming starting at timestamp zero. It is exactly 1440×2560 at 60 fps, H.264 High / CRF 18 / 8-bit `yuv420p`, AAC 48 kHz stereo at 192 kbps, MP4 faststart, and SDR BT.709 metadata. Source gameplay audio is only trimmed and re-encoded as required—no narration, music, effects, muting, gain changes, or mixing.

The layout is a 1440×2560 scale-cover, center-cropped background with Gaussian blur (`sigma=40`) and brightness `-0.18`, plus a sharp 1440×1920 scale-cover, center-cropped gameplay frame at `x=0, y=320`. There are no overlays other than the optional user-configured text described above. HDR-tagged sources are rejected because this tool does not tone-map HDR.
