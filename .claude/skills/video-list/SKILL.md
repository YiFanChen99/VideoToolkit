---
name: video-list
description: Update the video list (the 影片 worksheet of a Google spreadsheet) by scanning the folders in folders.txt with list_video_info.py; only new or changed videos are probed. Use when the user asks to scan or update the video list, e.g. "掃影片清單", "重掃全部", "掃 舞蹈 1.表演".
argument-hint: "[<folder name> ...]"
---

Target: $ARGUMENTS

1. Run `python -m video_list.list_video_info [<folder name> ...]` from the project root, in the background. Folder names are
   the ones in video_list/folders.txt; "全部" or none → no arguments (every folder). Scan only the folders the user named.
   The spreadsheet may stay open in the browser; no need to ask the user to close anything.
2. Report what it prints: the summary line (it ends with the spreadsheet's URL) and each group it lists (新增,
   已不存在, ...), plus any rows it says it couldn't find to write (moved or renamed by hand during the run).
   If it stops because it can't find the key file or open the spreadsheet, point the user at the service account section of
   video_list/README.md.
