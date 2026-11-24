# VideoToolkit

整理和處理本機影片的小工具：把影片庫列成一份 Google 試算表，以及把下載的影片合併、裁剪、轉成 AV1。

| 功能 | 用法 | 說明 |
|---|---|---|
| 影片清單 | `python -m video_list.list_video_info [資料夾 ...]` | [video_list](video_list/README.md) |
| 合併下載 | `python -m download_processing.merge_video <關鍵字>` | [合併](download_processing/README.md#合併merge_video) |
| 比對片段 | `python -m download_processing.match_clip <關鍵字>` | [比對片段](download_processing/README.md#比對片段match_clip) |
| 轉檔 | `python -m download_processing.convert_video <名稱> [條件 ...] <位元率>` | [轉檔](download_processing/README.md#轉檔convert_video) |
| 轉檔進度 | `python -m download_processing.watch_progress <轉檔輸出檔>` | [轉檔進度](download_processing/README.md#轉檔進度watch_progress) |

全部從專案根目錄執行。下載後的典型流程：下載 `.mp4` + `.m4a` → 合併成 `.mkv` →（有舊剪輯時先比對）→ 轉 AV1。

## 安裝

- Python 3.9+，`pip install -r requirements.txt`
- [FFmpeg](https://ffmpeg.org/)：`ffprobe`、`ffmpeg` 要在 PATH 上，例如 `winget install Gyan.FFmpeg`
- 下載後處理另外需要 [MKVToolNix](https://mkvtoolnix.download/) 和 [HandBrakeCLI](https://handbrake.fr/downloads2.php)，路徑見 [download_processing](download_processing/README.md)
- 影片清單另外需要 Google service account，見 [video_list](video_list/README.md#service-account)

## 在 Claude Code 裡

[.claude/skills](.claude/skills/) 讓你直接用口語下指令，例如「掃影片清單」「合併 黑名單」「比對 姜杉」「轉 黑名單 16 12 到 25」。

## 結構

```
common/                共用：ffprobe 量測（measure.py）、下載資料夾與關鍵字比對（downloads.py）
video_list/            影片清單，設定在 folders.txt（從 folders.example.txt 複製，不進 repo）
download_processing/   合併、比對、轉檔、轉檔進度，設定在 bitrates.txt
```
