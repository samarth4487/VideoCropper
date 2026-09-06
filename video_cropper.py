#!/usr/bin/env python3
"""Render short 9:16 gameplay clips from one disposable source video.

Only Python's standard library is used.  FFmpeg and FFprobe must be available
on PATH.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CURRENT_VIDEO = ROOT / "current_video"
CONFIG_FILE = CURRENT_VIDEO / "clips.json"
OUTPUTS = CURRENT_VIDEO / "outputs"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv"}
NAME_MAX_LENGTH = 120
COLOR_PRESETS = {"white": "#FFFFFF", "red": "#FF3B30", "green": "#34C759", "yellow": "#FFD60A"}
TEXT_FONT = "Impact"
TEXT_FONT_SIZE = 132
TEXT_SCALE_X = 83
TEXT_TOP_Y = 365
TEXT_LINE_STEP = 150
TEXT_SAFE_MARGIN = 120
TEXT_MAX_LINES = 4
INPUT_SEEK_THRESHOLD = Decimal("10")


class CropperError(Exception):
    """An expected, user-actionable error."""


@dataclass(frozen=True)
class SourceInfo:
    path: Path
    duration: Decimal
    width: int
    height: int
    color_transfer: str | None


@dataclass(frozen=True)
class Clip:
    name: str
    start: Decimal
    end: Decimal
    texts: tuple["TextOverlay", ...] = ()

    @property
    def filename(self) -> str:
        return f"{self.name}.mp4"

    @property
    def duration(self) -> Decimal:
        return self.end - self.start


@dataclass(frozen=True)
class TextOverlay:
    text: str
    start: Decimal
    end: Decimal
    color: str = "#FFFFFF"
    word_colors: tuple[tuple[int, str], ...] = ()


def require_binaries() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise CropperError(
            "Required program(s) not found on PATH: " + ", ".join(missing) + ". "
            "Install FFmpeg (which provides both ffmpeg and ffprobe), then retry."
        )


def discover_source(directory: Path = CURRENT_VIDEO) -> Path:
    """Find exactly one supported, non-hidden video file at directory's top level."""
    if not directory.is_dir():
        raise CropperError(f"Missing required directory: {directory}")
    videos = sorted(
        (
            item
            for item in directory.iterdir()
            if item.is_file()
            and not item.name.startswith(".")
            and item.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda item: item.name.casefold(),
    )
    if not videos:
        raise CropperError(
            f"No source video found in {directory}. Place exactly one MP4, MOV, M4V, "
            "or MKV file directly in current_video/. Files inside outputs/ are ignored."
        )
    if len(videos) > 1:
        names = ", ".join(item.name for item in videos)
        raise CropperError(
            f"Found {len(videos)} top-level source videos in {directory}: {names}. "
            "Keep exactly one; the tool will never guess which to use."
        )
    return videos[0]


def run_ffprobe(source: Path) -> SourceInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,color_transfer,channels,sample_rate",
        "-of",
        "json",
        str(source),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or "ffprobe returned no diagnostic output."
        raise CropperError(f"Could not inspect {source.name}: {detail}")
    try:
        payload = json.loads(result.stdout)
        duration = Decimal(str(payload["format"]["duration"]))
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        raise CropperError(f"Could not read a usable duration from {source.name}.") from exc
    if not duration.is_finite() or duration <= 0:
        raise CropperError(f"{source.name} has no positive, readable duration.")

    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video is None:
        raise CropperError(f"{source.name} has no readable video stream.")
    if audio is None:
        raise CropperError(f"{source.name} has no audio stream; gameplay audio is required.")
    try:
        width, height = int(video["width"]), int(video["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CropperError(f"Could not read the video dimensions from {source.name}.") from exc
    if width <= height:
        raise CropperError(
            f"{source.name} is {width}x{height}, not a landscape source video. "
            "This utility expects a landscape 16:9 gameplay video."
        )
    # Allow small encoder rounding differences while rejecting substantially non-16:9 video.
    if abs((width / height) - (16 / 9)) > 0.02:
        raise CropperError(
            f"{source.name} is {width}x{height}, which is not approximately 16:9. "
            "This utility expects a landscape 16:9 gameplay video."
        )
    transfer = video.get("color_transfer")
    if transfer in {"smpte2084", "arib-std-b67"}:
        raise CropperError(
            f"{source.name} is tagged as HDR ({transfer}). This renderer deliberately "
            "accepts SDR sources only and does not tone-map HDR."
        )
    return SourceInfo(source, duration, width, height, transfer)


def parse_timestamp(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise CropperError("Timestamps must be strings, such as '00:01:20.000'.")
    text = value.strip()
    pieces = text.split(":")
    if len(pieces) not in (2, 3):
        raise CropperError(
            f"Invalid timestamp {value!r}. Use HH:MM:SS, MM:SS, with optional milliseconds."
        )
    if any(not piece for piece in pieces):
        raise CropperError(f"Invalid timestamp {value!r}.")
    seconds_text = pieces[-1]
    if not re.fullmatch(r"\d{1,2}(?:\.\d{1,3})?", seconds_text):
        raise CropperError(f"Invalid seconds in timestamp {value!r}.")
    try:
        seconds = Decimal(seconds_text)
    except InvalidOperation as exc:
        raise CropperError(f"Invalid timestamp {value!r}.") from exc
    if not Decimal("0") <= seconds < Decimal("60"):
        raise CropperError(f"Seconds must be between 00 and 59.999 in {value!r}.")

    integer_parts = pieces[:-1]
    if any(not re.fullmatch(r"\d+", part) for part in integer_parts):
        raise CropperError(f"Invalid timestamp {value!r}.")
    if len(pieces) == 3:
        hours, minutes = map(int, integer_parts)
        if minutes >= 60:
            raise CropperError(f"Minutes must be between 00 and 59 in {value!r}.")
    else:
        hours, minutes = 0, int(integer_parts[0])
    return Decimal(hours * 3600 + minutes * 60) + seconds


def sanitize_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CropperError("Each clip name must be a non-empty string.")
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("._-")
    if not safe:
        raise CropperError(f"Clip name {value!r} has no usable filename characters.")
    if len(safe) > NAME_MAX_LENGTH:
        raise CropperError(
            f"Clip name {value!r} becomes more than {NAME_MAX_LENGTH} filename characters."
        )
    return safe


def parse_color(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CropperError(f"{field} must be a preset color or a #RRGGBB value.")
    value = value.strip().lower()
    if value in COLOR_PRESETS:
        return COLOR_PRESETS[value]
    if value == "random":
        return value
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value.upper()
    raise CropperError(
        f"{field} must be one of {', '.join(COLOR_PRESETS)}, random, or a #RRGGBB value."
    )


def parse_interval(
    entry: dict[str, Any],
    prefix: str,
    maximum: Decimal,
    *,
    default_start: Decimal | None = None,
    default_end: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Parse start/end or start/duration, with explicit defaults where allowed."""
    if "start" in entry:
        start = parse_timestamp(entry["start"])
    elif default_start is not None:
        start = default_start
    else:
        raise CropperError(f"{prefix} must include a start timestamp.")

    has_end = "end" in entry
    has_duration = "duration" in entry
    if has_end and has_duration:
        raise CropperError(f"{prefix} may use end or duration, not both.")
    if has_end:
        end = parse_timestamp(entry["end"])
    elif has_duration:
        end = start + parse_timestamp(entry["duration"])
    elif default_end is not None:
        end = default_end
    else:
        raise CropperError(f"{prefix} must include an end timestamp or duration.")

    if end <= start:
        raise CropperError(f"{prefix} end must be after start.")
    if start < 0 or end > maximum:
        raise CropperError(
            f"{prefix} range {format_seconds(start)}–{format_seconds(end)} is outside "
            f"its allowed duration {format_seconds(maximum)}."
        )
    return start, end


def load_texts(value: Any, clip_duration: Decimal, clip_index: int) -> tuple[TextOverlay, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CropperError(f"clips[{clip_index}].texts must be an array.")
    overlays: list[TextOverlay] = []
    for index, entry in enumerate(value, start=1):
        prefix = f"clips[{clip_index}].texts[{index}]"
        if not isinstance(entry, dict):
            raise CropperError(f"{prefix} must be an object.")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
            raise CropperError(f"{prefix}.text must be one non-empty line of text.")
        if len(text) > 240:
            raise CropperError(f"{prefix}.text is limited to 240 characters.")
        words = text.split()
        if len(wrap_words(words)) > TEXT_MAX_LINES:
            raise CropperError(
                f"{prefix}.text exceeds the {TEXT_MAX_LINES}-line safe text area; shorten it."
            )
        start, end = parse_interval(
            entry, prefix, clip_duration, default_start=Decimal(0), default_end=clip_duration
        )
        color = parse_color(entry.get("color", "white"), f"{prefix}.color")
        overrides = entry.get("word_colors", [])
        if not isinstance(overrides, list):
            raise CropperError(f"{prefix}.word_colors must be an array.")
        seen_indices: set[int] = set()
        parsed_overrides: list[tuple[int, str]] = []
        for override_index, override in enumerate(overrides, start=1):
            if not isinstance(override, dict) or not isinstance(override.get("word_index"), int):
                raise CropperError(f"{prefix}.word_colors[{override_index}] needs an integer word_index.")
            word_index = override["word_index"]
            if word_index < 1 or word_index > len(words) or word_index in seen_indices:
                raise CropperError(f"{prefix}.word_colors[{override_index}].word_index is invalid or repeated.")
            seen_indices.add(word_index)
            parsed_overrides.append((word_index, parse_color(override.get("color"), f"{prefix}.word_colors[{override_index}].color")))
        overlays.append(TextOverlay(text.strip(), start, end, color, tuple(parsed_overrides)))
    return tuple(overlays)


def load_clips(config_path: Path, source_duration: Decimal) -> list[Clip]:
    if not config_path.is_file():
        raise CropperError(f"Missing clipping configuration: {config_path}")
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CropperError(f"Could not read valid JSON from {config_path}: {exc}") from exc
    entries = document.get("clips") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise CropperError(f"{config_path} must contain a non-empty 'clips' array.")

    clips: list[Clip] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CropperError(f"clips[{index}] must be an object.")
        name = sanitize_name(entry.get("name"))
        if name.casefold() in seen:
            raise CropperError(
                f"clips[{index}] name {entry.get('name')!r} collides after filename "
                f"sanitization (output: {name}.mp4)."
            )
        seen.add(name.casefold())
        start, end = parse_interval(entry, f"clips[{index}]", source_duration)
        clips.append(Clip(name, start, end, load_texts(entry.get("texts"), end - start, index)))
    return clips


def format_seconds(value: Decimal) -> str:
    milliseconds = int((value * 1000).to_integral_value())
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def ass_color(color: str) -> str:
    return f"&H00{color[5:7]}{color[3:5]}{color[1:3]}&"


def resolve_color(color: str, seed: str) -> str:
    """Keep requested random colors stable across dry runs and re-renders."""
    if color != "random":
        return color
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return f"#{80 + digest[0] % 176:02X}{80 + digest[1] % 176:02X}{80 + digest[2] % 176:02X}"


def ass_time(value: Decimal) -> str:
    centiseconds = int((value * 100).to_integral_value())
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def wrap_words(words: list[str]) -> list[list[tuple[int, str]]]:
    """Wrap at a stable character budget; color markup does not affect wrapping."""
    lines: list[list[tuple[int, str]]] = [[]]
    length = 0
    for index, word in enumerate(words, start=1):
        added = len(word) + (1 if lines[-1] else 0)
        if lines[-1] and length + added > 27:
            lines.append([])
            length = 0
        lines[-1].append((index, word))
        length += len(word) + (1 if length else 0)
    return lines


def make_ass(clip: Clip) -> str:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1440
PlayResY: 2560
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Overlay,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,{scale_x},100,0,0,1,10,8,8,{margin},{margin},0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
""".format(
        font=TEXT_FONT,
        size=TEXT_FONT_SIZE,
        scale_x=TEXT_SCALE_X,
        margin=TEXT_SAFE_MARGIN,
    )
    events: list[str] = []
    for overlay in clip.texts:
        colors = dict(overlay.word_colors)
        base_color = resolve_color(overlay.color, f"{clip.name}:{overlay.text}:base")
        for line_number, line in enumerate(wrap_words(overlay.text.split())):
            pieces: list[str] = []
            for word_index, word in line:
                color = resolve_color(colors.get(word_index, base_color), f"{clip.name}:{overlay.text}:{word_index}")
                escaped = word.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                pieces.append(f"{{\\c{ass_color(color)}}}{escaped}{{\\c{ass_color(base_color)}}}")
            y = TEXT_TOP_Y + line_number * TEXT_LINE_STEP
            events.append(
                f"Dialogue: 0,{ass_time(overlay.start)},{ass_time(overlay.end)},Overlay,,0,0,0,,"
                f"{{\\an8\\pos(720,{y})}}" + " ".join(pieces)
            )
    return header + "\n".join(events) + "\n"


def should_seek_input(clip: Clip) -> bool:
    """Avoid decoding irrelevant leading footage while keeping short clips simple."""
    return clip.start > INPUT_SEEK_THRESHOLD


def input_seek_start(clip: Clip) -> Decimal:
    """Leave a ten-second decode pre-roll before later clip boundaries."""
    return clip.start - INPUT_SEEK_THRESHOLD if should_seek_input(clip) else Decimal(0)


def ffmpeg_filter(
    clip: Clip, subtitle_path: Path | None = None, *, input_seek: bool | None = None
) -> str:
    # FFmpeg's trim/atrim filters take seconds as numbers.  The human-facing
    # HH:MM:SS formatter is intentionally not used here because ':' is parsed
    # as an option separator in a filter graph.
    if input_seek is None:
        input_seek = should_seek_input(clip)
    seek_start = input_seek_start(clip) if input_seek else Decimal(0)
    trim_start = clip.start - seek_start
    trim_end = clip.end - seek_start
    start, end = format(trim_start, "f"), format(trim_end, "f")
    graph = (
        f"[0:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,split=2[bgsrc][fgsrc];"
        "[bgsrc]scale=1440:2560:force_original_aspect_ratio=increase,"
        "crop=1440:2560,gblur=sigma=40:steps=6,eq=brightness=-0.18[bg];"
        "[fgsrc]scale=1440:1920:force_original_aspect_ratio=increase,"
        "crop=1440:1920[fg];"
        "[bg][fg]overlay=x=0:y=320:shortest=1,fps=60,setsar=1[composed];"
        f"[0:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a]"
    )
    if subtitle_path is None:
        return graph.replace("setsar=1[composed];", "setsar=1,format=yuv420p[v];")
    escaped = str(subtitle_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return graph.replace(
        "setsar=1[composed];", f"setsar=1,subtitles=filename='{escaped}',format=yuv420p[v];"
    )


def build_ffmpeg_command(source: SourceInfo, clip: Clip, destination: Path, subtitle_path: Path | None = None) -> list[str]:
    input_seek = should_seek_input(clip)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if input_seek:
        # Input seeking is accurate for re-encoding: FFmpeg seeks to a nearby
        # keyframe then decodes/discards frames until the pre-roll timestamp.
        command.extend(["-ss", format(input_seek_start(clip), "f"), "-accurate_seek"])
    command.extend([
        "-i", str(source.path), "-filter_complex", ffmpeg_filter(clip, subtitle_path, input_seek=input_seek),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-profile:v", "high", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", "60", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv", "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
        "-movflags", "+faststart", "-map_metadata", "-1", str(destination),
    ])
    return command


def validate_project() -> tuple[SourceInfo, list[Clip]]:
    require_binaries()
    source = run_ffprobe(discover_source())
    clips = load_clips(CONFIG_FILE, source.duration)
    return source, clips


def print_plan(source: SourceInfo, clips: list[Clip]) -> None:
    print(f"Source: {source.path.name} ({source.width}x{source.height}, {format_seconds(source.duration)})")
    print(f"Valid clips: {len(clips)}")
    for clip in clips:
        print(f"  {clip.filename}: {format_seconds(clip.start)}–{format_seconds(clip.end)} ({format_seconds(clip.duration)}), {len(clip.texts)} text overlay(s)")


def render_one(source: SourceInfo, clip: Clip, overwrite: bool) -> str:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    destination = OUTPUTS / clip.filename
    temporary = OUTPUTS / f".{clip.name}.partial.mp4"
    subtitles = CURRENT_VIDEO / f".{clip.name}.text.ass"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    if clip.texts:
        subtitles.write_text(make_ass(clip), encoding="utf-8")
    command = build_ffmpeg_command(source, clip, temporary, subtitles if clip.texts else None)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    subtitles.unlink(missing_ok=True)
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        detail = result.stderr.strip() or "ffmpeg returned no diagnostic output."
        raise CropperError(f"{clip.filename} failed: {detail}")
    if destination.exists() and not overwrite:
        temporary.unlink(missing_ok=True)
        raise CropperError(f"Refusing to overwrite existing output: {destination}. Use --overwrite.")
    os.replace(temporary, destination)
    return clip.filename


def command_validate(_: argparse.Namespace) -> int:
    source, clips = validate_project()
    print_plan(source, clips)
    print("Validation passed.")
    return 0


def command_dry_run(_: argparse.Namespace) -> int:
    source, clips = validate_project()
    print_plan(source, clips)
    print("\nFFmpeg commands (no files will be created):")
    for clip in clips:
        temporary = OUTPUTS / f".{clip.name}.partial.mp4"
        subtitles = CURRENT_VIDEO / f".{clip.name}.text.ass"
        print("  " + shlex_join(build_ffmpeg_command(source, clip, temporary, subtitles if clip.texts else None)))
    return 0


def command_render(args: argparse.Namespace) -> int:
    source, clips = validate_project()
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    conflicts = [clip.filename for clip in clips if (OUTPUTS / clip.filename).exists()]
    if conflicts and not args.overwrite:
        raise CropperError(
            "Refusing to overwrite existing output(s): " + ", ".join(conflicts) + ". Use --overwrite."
        )
    print_plan(source, clips)
    print(f"Rendering {len(clips)} clip(s) with {args.jobs} job(s)...")
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(render_one, source, clip, args.overwrite): clip for clip in clips}
        for future in concurrent.futures.as_completed(futures):
            clip = futures[future]
            try:
                print(f"Rendered: {future.result()}")
            except CropperError as exc:
                failures.append(str(exc))
            except Exception as exc:  # pragma: no cover - defensive reporting around subprocess threads
                failures.append(f"{clip.filename} failed unexpectedly: {exc}")
    if failures:
        raise CropperError("Rendering finished with failures:\n  " + "\n  ".join(failures))
    print("Rendering complete.")
    return 0


def command_clear(args: argparse.Namespace) -> int:
    if not args.yes:
        raise CropperError(
            "Refusing to clear current_video/ without --yes. This removes source video(s), clips.json, and outputs/."
        )
    if not CURRENT_VIDEO.exists():
        print("current_video/ is already absent; nothing to clear.")
        return 0
    removed: list[str] = []
    for item in CURRENT_VIDEO.iterdir():
        if item == OUTPUTS:
            if item.is_symlink():
                item.unlink()
            else:
                shutil.rmtree(item)
            removed.append("outputs/")
        elif item == CONFIG_FILE or (
            item.is_file() and not item.name.startswith(".") and item.suffix.lower() in VIDEO_EXTENSIONS
        ):
            item.unlink()
            removed.append(item.name)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    print("Cleared: " + (", ".join(removed) if removed else "nothing") + ".")
    return 0


def shlex_join(command: list[str]) -> str:
    # shlex.join is present in every supported modern Python, kept isolated for testability.
    import shlex
    return shlex.join(command)


class CropperUnitTests(unittest.TestCase):
    def test_timestamp_formats(self) -> None:
        self.assertEqual(parse_timestamp("00:01:02.500"), Decimal("62.500"))
        self.assertEqual(parse_timestamp("01:02.5"), Decimal("62.5"))
        self.assertEqual(parse_timestamp("01:02:03"), Decimal("3723"))

    def test_sanitization_and_collision(self) -> None:
        self.assertEqual(sanitize_name("  jump / replay! "), "jump_replay")
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "clips.json"
            config.write_text(
                '{"clips":[{"name":"jump replay","start":"00:00","end":"00:01"},'
                '{"name":"jump_replay","start":"00:01","end":"00:02"}]}', encoding="utf-8"
            )
            with self.assertRaises(CropperError):
                load_clips(config, Decimal("3"))

    def test_duration_and_default_text_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "clips.json"
            config.write_text(
                json.dumps({"clips": [{
                    "name": "duration_clip",
                    "start": "01:00",
                    "duration": "00:15.500",
                    "texts": [{"text": "This stays for the whole clip by default"}],
                }]}),
                encoding="utf-8",
            )
            clip = load_clips(config, Decimal("100"))[0]
        self.assertEqual((clip.start, clip.end), (Decimal("60"), Decimal("75.5")))
        self.assertEqual((clip.texts[0].start, clip.texts[0].end), (Decimal(0), Decimal("15.5")))

    def test_rejects_end_and_duration_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "clips.json"
            config.write_text(
                '{"clips":[{"name":"bad","start":"00:00","end":"00:01","duration":"00:01"}]}',
                encoding="utf-8",
            )
            with self.assertRaises(CropperError):
                load_clips(config, Decimal("3"))

    def test_discovery_is_top_level_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "outputs").mkdir()
            (folder / "outputs" / "old.mp4").touch()
            (folder / ".hidden.mp4").touch()
            (folder / "notes.txt").touch()
            (folder / "gameplay.MOV").touch()
            self.assertEqual(discover_source(folder).name, "gameplay.MOV")

    def test_filter_has_locked_layout(self) -> None:
        filter_graph = ffmpeg_filter(Clip("example", Decimal("10"), Decimal("20")))
        self.assertIn("scale=1440:2560", filter_graph)
        self.assertIn("gblur=sigma=40", filter_graph)
        self.assertIn("eq=brightness=-0.18", filter_graph)
        self.assertIn("scale=1440:1920", filter_graph)
        self.assertIn("overlay=x=0:y=320", filter_graph)
        self.assertIn("fps=60", filter_graph)
        self.assertIn("setsar=1", filter_graph)

    def test_long_clips_use_accurate_input_seek(self) -> None:
        source = SourceInfo(Path("gameplay.mov"), Decimal("3600"), 2560, 1440, "bt709")
        short = Clip("short", Decimal("10"), Decimal("12"))
        long = Clip("long", Decimal("780"), Decimal("782"))
        self.assertNotIn("-ss", build_ffmpeg_command(source, short, Path("short.mp4")))
        command = build_ffmpeg_command(source, long, Path("long.mp4"))
        self.assertEqual(command[5:8], ["-ss", "770", "-accurate_seek"])
        self.assertIn("trim=start=10:end=12", ffmpeg_filter(long))

    def test_text_colors_and_word_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "clips.json"
            config.write_text(
                json.dumps({"clips": [{
                    "name": "clean_racing",
                    "start": "00:00:10",
                    "end": "00:00:20",
                    "texts": [{
                        "text": "Clean racing hits different in racing games",
                        "start": "00:00:01",
                        "end": "00:00:05",
                        "word_colors": [
                            {"word_index": 2, "color": "green"},
                            {"word_index": 6, "color": "yellow"},
                        ],
                    }],
                }]}),
                encoding="utf-8",
            )
            clip = load_clips(config, Decimal("30"))[0]
        self.assertEqual(clip.texts[0].color, "#FFFFFF")
        self.assertEqual(clip.texts[0].word_colors, ((2, "#34C759"), (6, "#FFD60A")))
        ass = make_ass(clip)
        self.assertIn("&H0059C734&", ass)  # #34C759 in ASS BGR order.
        self.assertIn("&H000AD6FF&", ass)  # #FFD60A in ASS BGR order.
        self.assertEqual(resolve_color("random", "same-seed"), resolve_color("random", "same-seed"))


def command_self_test(_: argparse.Namespace) -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CropperUnitTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Make vertical, blurred-band gameplay clips from one current_video source."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("validate", command_validate), ("dry-run", command_dry_run), ("self-test", command_self_test)):
        child = subparsers.add_parser(name)
        child.set_defaults(handler=handler)
    render = subparsers.add_parser("render")
    render.add_argument("--jobs", type=int, default=1, choices=range(1, 65), metavar="1-64")
    render.add_argument("--overwrite", action="store_true", help="replace an existing output clip")
    render.set_defaults(handler=command_render)
    clear = subparsers.add_parser("clear")
    clear.add_argument("--yes", action="store_true", help="confirm removal of disposable current-video files")
    clear.set_defaults(handler=command_clear)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except CropperError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
