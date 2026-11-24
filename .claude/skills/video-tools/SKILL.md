---
name: video-tools
description: Paths and quirks of this machine's video tools (HandBrakeCLI, mkvmerge, mkvpropedit, mkvextract, ffprobe, ffmpeg). Use before running any of them, or when writing a script that transcodes, merges or inspects videos.
---

## Paths

| Tool | Path | On PATH |
|---|---|---|
| HandBrakeCLI 1.11.2 | `C:\Program Files\HandBrake\HandBrakeCLI.exe` | no |
| HandBrake GUI | `C:\Program Files\HandBrake\HandBrake.exe` | no |
| MKVToolNix 102.0 (portable) | `E:\Programs\MKVToolNix_102.0\` — `mkvmerge.exe`, `mkvpropedit.exe`, `mkvextract.exe`, `mkvinfo.exe` | no |
| FFmpeg 9.0.2 (winget `Gyan.FFmpeg`) | `ffprobe`, `ffmpeg` | yes |

Call HandBrakeCLI and MKVToolNix by full path; scripts keep each path in one variable at the top.
Ignore the old `ffmpeg.exe` copies under `C:\Program Files (x86)\` (Moo0, MP4Tools): bundled with other apps, not the one on PATH.

## Hardware

CPU Intel i3-12100 (4 cores), GPU GeForce GTX 1650 + Intel UHD 730.
Neither GPU encodes AV1, so AV1 always means software SVT-AV1: `--encoder svt_av1_10bit` (or `svt_av1`).
NVENC and QSV are available for H.264/H.265 only.

## HandBrake GUI presets

The GUI saves presets to `%APPDATA%\HandBrake\presets.json`; HandBrakeCLI reads them with `--preset-import-gui -Z "<name>"` (list them with `--preset-import-gui -z`), so a script uses the user's preset with no export.
The user's own presets are in the `Custom Presets` group: `AV1 2560` and `AV1 3072`, identical except the bitrate:
SVT-AV1 10-bit, preset 6, tune vq, average bitrate 2560 / 3072 kbps with multi-pass, MP4, no scaling or filters, chapters kept.
In this JSON `VideoQualityType` 1 means average bitrate (2 is constant quality), so their `VideoQualitySlider` (RF 35) is unused.
Options after `--preset` override single settings and keep the rest: `--vb <kbps>` replaces only the bitrate (multi-pass and tune stay; tested), so one preset serves every bitrate — convert_video.py uses `AV1 2560` this way.

Their audio and subtitle selection behavior is `none`: through HandBrakeCLI the output has video only (tested).
To keep tracks, add options after `-Z`, e.g. `--all-audio --aencoder copy --audio-fallback av_aac` and `--all-subtitles`.

## Quirks

- HandBrakeCLI writes `--help`, `--version` and its progress log to stderr: capture with `2>&1` in Bash.
  In Windows PowerShell 5.1, `2>&1` on a native exe turns each stderr line into an error record and sets `$?` to false; check `$LASTEXITCODE` instead.
- SVT-AV1 on 4 cores is slow (preset 4–6 runs well below real time); run long encodes in the background.
