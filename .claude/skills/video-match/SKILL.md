---
name: video-match
description: Find where one video's cut (e.g. an old clip) lies in another copy of the same video (e.g. a new, higher-quality download) via match_clip.py, giving the trims that repeat the cut on the new one. Use when the user asks to compare two videos, find the matching part, or apply an old cut to a new download, e.g. "比對 姜杉", "姜杉 舊的剪裁套到新的".
argument-hint: "<keyword> | <reference file> <target file>"
---

Target: $ARGUMENTS

1. Run `python -m download_processing.match_clip "<keyword>"` from the project root; with two files, run
   `python -m download_processing.match_clip --reference "<the cut>" --target "<the video to cut>"` instead.
   The keyword must pick exactly two videos in Downloads (any extension); the shorter is the reference (the old
   cut), the longer the target. It decodes both videos, so it takes about as long as decoding the target
   (4 minutes of 1080p: about 12 seconds).
2. Exit code 2 (not exactly two videos match): ask the user which two, then rerun with `--reference` / `--target`.
   Exit code 1: report the message (e.g. not found: the videos may be cropped, mirrored or a different take).
3. Report each `match:` line (the reference's range = the target's range) and any `not found:` line, which means
   part of the reference is not in the target, so trimming the target will not fully repeat the cut.
4. With a `trim:` line, offer to convert the target with those trims through the video-convert skill:
   `轉 <name> 去頭<trim-start> 去尾<trim-end> <bitrate>`, where name is a word from the target's file name
   (convert_video.py only picks .mkv files). Ask for the bitrate if the user didn't give one; don't start the
   conversion without it.
