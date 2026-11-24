---
name: video-convert
description: Re-encode a merged download (.mkv in Downloads) to AV1 at a bitrate shorthand via convert_video.py, optionally trimming the start and end, deleting the other trial cuts of the same video. Use when the user asks to convert a video, e.g. "轉 黑名單 16 12 到 25" or "轉 公演 去頭2s 去尾3s 25".
argument-hint: "<name> [<condition> ...] [去頭<length>] [去尾<length>] [到] <bitrate shorthand>"
---

Target: $ARGUMENTS

1. Split the request: the first word is the name, the last number (after `到`, if present) is the bitrate
   shorthand from download_processing/bitrates.txt, and the words between are conditions. "轉 黑名單 16 12 到 25" → name `黑名單`,
   conditions `16 12`, shorthand `25`; "轉 幻 cut 2" → name `幻`, condition `cut`, shorthand `2`.
   Settle the shorthand first: when the last word is a shorthand in bitrates.txt, it is the bitrate, with or
   without `到`, even if it also appears in a file name. Do not ask; only then read the words before it.
   `去頭<length>` and `去尾<length>` are trims, not conditions: they become `--trim-start <length>` and
   `--trim-end <length>` (lengths like `2s`, `1.5s`, `1:05`). "轉 公演 去頭2s 去尾3s 25" → name `公演`,
   no conditions, `--trim-start 2s --trim-end 3s`, shorthand `25`.
   `覆蓋` is not a condition either: it becomes `--overwrite`, replacing `Downloads\<name>.mp4`. Without it, an
   existing output is kept and the new one is written as `<name> (2).mp4`, `<name> (3).mp4` ...
2. Run `python -m download_processing.convert_video "<name>" <condition> ... <shorthand> [--trim-start <length>] [--trim-end <length>]`
   from the project root, in the background
   (a few minutes of video takes several minutes to encode on this machine).
   Before encoding it deletes every other .mkv matching the name but not all conditions, and prints each one.
   Right after it starts, start a Monitor (`timeout_ms` 1800000, description `<name> 轉檔進度`) running
   `python -m download_processing.watch_progress "<the background run's output file>"` from the project root. Each line it prints
   (e.g. `第 1/2 趟 35%，全部約剩 6 分`) is a progress report: pass it to the user as one short line. It exits by
   itself when the run ends; if the Monitor expires while the run is still going, start it again.
   The run's own completion notification, not the Monitor, drives step 3.
3. Exit code 2 (several .mkv match): nothing was deleted. Ask the user to pick one of the listed files, then rerun
   with `--input "<Downloads>\<picked file>"`.
   Exit code 1: report the message.
4. Done when it exits 0. Report the deleted files, the output path (say so when it got a number), the `trim:` line if any (the kept range),
   and the source/output rows it prints (video kbps, codec, MB, duration; duration is rounded to whole seconds).

To test the script, use a few-second clip made with ffmpeg and pass it with `--input` and a throwaway name;
a real name can start a full encode and delete the user's other cuts.
