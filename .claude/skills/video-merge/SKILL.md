---
name: video-merge
description: Merge a downloaded .mp4 video + .m4a audio pair of the same name in Downloads into .mkv via merge_video.py, after checking neither download was cut short. Use when the user gives a keyword to merge a download.
argument-hint: "<keyword>"
---

Target: $ARGUMENTS

1. Run `python -m download_processing.merge_video "<keyword>"` from the project root. It takes seconds.
   The keyword picks the pair and names the output: `Downloads\<keyword>.mkv`.
2. Read the outcome and tell the user:
   - Exit code 2 (several pairs match): ask the user to pick one of the listed pairs, then rerun with the same
     keyword and `--input "<Downloads>\<picked name>.mp4"`.
   - Exit code 3 (output exists): ask whether to replace it; rerun with `--overwrite` only on yes.
   - `incomplete, deleted: <file> (<reason>)`: that download was cut short and is already deleted;
     the user must download it again.
   - lengths differ / still downloading / mkvmerge failed / lost packets: nothing was deleted; report the message.
3. Done when it exits 0 with a `merged:` line. Report the .mkv name and size.

To test the script, copy files to throwaway names (e.g. `claude-test-pair.mp4` / `.m4a`) and pass that keyword:
the script deletes the files it merges or finds incomplete.
