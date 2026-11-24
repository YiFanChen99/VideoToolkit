"""Merge a downloaded video (.mp4) and audio (.m4a) pair of the same name in the Downloads folder into .mkv.

Usage: python -m download_processing.merge_video <keyword> [--input <file.mp4 or .m4a>] [--overwrite]
  keyword      picks the pair whose name contains every space-separated word of it
               (ignoring case and simplified/traditional Chinese)
               and names the output: Downloads\\<keyword>.mkv
  --input      merge this file's pair instead of searching Downloads
  --overwrite  replace an existing output file
Each file is checked for an interrupted download first. A cut file is reported and deleted, and the run stops.
After mkvmerge succeeds and the .mkv holds every packet of both files, the .mp4 and .m4a are deleted.
Exit codes: 0 done, 1 a problem (reported), 2 several pairs match the keyword (listed), 3 the output already exists.
"""
import argparse
import os
import struct
import subprocess
import sys

from common.downloads import DOWNLOADS, name_matches
from common.measure import format_duration, probe

MKVMERGE = r'E:\Programs\MKVToolNix_102.0\mkvmerge.exe'
# A download cut on a fragment boundary keeps its header's full duration but its data stops early;
# fragments run seconds long, while a complete file's data ends within a frame of the header.
CUT_TOLERANCE = 0.5
# A complete pair's video and audio lengths differ by a fraction of a second.
PAIR_TOLERANCE = 1.0
DOWNLOADING_SUFFIXES = ('.crdownload', '.part')


def box_problem(path):
    """An MP4's top-level boxes exactly fill the file; a cut download ends inside one."""
    length = os.path.getsize(path)
    if length == 0:
        return 'empty file'
    position = 0
    with open(path, 'rb') as file:
        while position < length:
            file.seek(position)
            header = file.read(16)
            if len(header) < 8:
                return f'cut inside a box header at byte {position}'
            size = struct.unpack('>I', header[:4])[0]
            if size == 1 and len(header) == 16:
                size = struct.unpack('>Q', header[8:16])[0]
            elif size == 0:
                return None  # the last box runs to the end of the file
            if size < 8:
                return f'broken box at byte {position}'
            position += size
    if position > length:
        return f'ends inside a box: the file has {length:,} bytes, its last box needs {position:,}'
    return None


def check(path):
    """Return (problem, None) for an incomplete file, else (None, (data end in seconds, packet count))."""
    problem = box_problem(path)
    if problem:
        return problem, None
    try:
        declared = float(probe(['-show_entries', 'format=duration', path])['format']['duration'])
    except (KeyError, ValueError):
        return 'ffprobe cannot read it', None
    packets = probe(['-show_entries', 'packet=pts_time,duration_time', path]).get('packets', [])
    ends = [float(packet['pts_time']) + float(packet.get('duration_time') or 0)
            for packet in packets if 'pts_time' in packet]
    if not ends:
        return 'no media data', None
    end = max(ends)
    if declared - end > CUT_TOLERANCE:
        return f'data stops at {format_duration(end)} of {format_duration(declared)}', None
    return None, (end, len(packets))


def packet_counts(path):
    streams = probe(['-count_packets', '-show_entries', 'stream=codec_type,nb_read_packets', path])
    return {stream['codec_type']: int(stream['nb_read_packets']) for stream in streams.get('streams', [])}


def find_pair(keyword):
    """Return the path without extension of the one .mp4 + .m4a pair matching the keyword."""
    stems = sorted(name[:-4] for name in os.listdir(DOWNLOADS)
                   if name.lower().endswith('.m4a') and name_matches(name, keyword)
                   and os.path.isfile(os.path.join(DOWNLOADS, name[:-4] + '.mp4')))
    if not stems:
        sys.exit(f'no .mp4 + .m4a pair in {DOWNLOADS} contains "{keyword}"')
    if len(stems) > 1:
        print(f'several .mp4 + .m4a pairs in {DOWNLOADS} contain "{keyword}":')
        for stem in stems:
            print(f'  {stem}')
        sys.exit(2)
    return os.path.join(DOWNLOADS, stems[0])


def merge(stem, keyword, overwrite):
    """Check, merge and clean up one pair; exit on any problem."""
    video, audio = stem + '.mp4', stem + '.m4a'
    output = os.path.join(DOWNLOADS, keyword + '.mkv')
    print(f'{os.path.basename(stem)} -> {os.path.basename(output)}')
    for path in (video, audio):
        if not os.path.isfile(path):
            sys.exit(f'missing: {path}')
    if any(os.path.exists(path + suffix) for path in (video, audio) for suffix in DOWNLOADING_SUFFIXES):
        sys.exit('still downloading (a .crdownload/.part file is next to it); run again when it finishes')

    results = {path: check(path) for path in (video, audio)}
    cut = [(path, problem) for path, (problem, _) in results.items() if problem]
    if cut:
        for path, problem in cut:
            os.remove(path)
            print(f'incomplete, deleted: {os.path.basename(path)} ({problem})')
        sys.exit('download it again, then rerun')
    (video_end, video_packets), (audio_end, audio_packets) = results[video][1], results[audio][1]
    print(f'video {format_duration(video_end)} ({video_end:.2f}s), audio {format_duration(audio_end)} ({audio_end:.2f}s)')
    if abs(video_end - audio_end) > PAIR_TOLERANCE:
        sys.exit('video and audio lengths differ, though each file looks complete: '
                 'check they belong together (nothing deleted)')

    if os.path.exists(output) and not overwrite:
        print(f'output already exists: {output}')
        sys.exit(3)
    # Merge under a temporary name, so a failed run never leaves a half-made file under the real name.
    partial = os.path.join(DOWNLOADS, keyword + '.merging.mkv')
    result = subprocess.run([MKVMERGE, '--output-charset', 'UTF-8', '--output', partial, video, audio],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode == 2 or not os.path.isfile(partial):  # mkvmerge: 0 ok, 1 warnings, 2 error
        print(result.stdout.strip())
        if os.path.exists(partial):
            os.remove(partial)
        sys.exit('mkvmerge failed (nothing deleted)')
    if result.returncode == 1:
        print('\n'.join(line for line in result.stdout.splitlines() if 'Warning' in line))

    merged = packet_counts(partial)
    if merged.get('video') != video_packets or merged.get('audio') != audio_packets:
        os.remove(partial)
        sys.exit(f'merged file lost packets: video {merged.get("video")} of {video_packets}, '
                 f'audio {merged.get("audio")} of {audio_packets} (nothing deleted)')
    os.replace(partial, output)
    os.remove(video)
    os.remove(audio)
    print(f'merged: {os.path.basename(output)} ({round(os.path.getsize(output) / 1e6)} MB); deleted the .mp4 and .m4a')


def main():
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument('keyword')
    parser.add_argument('--input')
    parser.add_argument('--overwrite', action='store_true')
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')  # sys.exit messages go to stderr
    stem = os.path.splitext(arguments.input)[0] if arguments.input else find_pair(arguments.keyword)
    merge(stem, arguments.keyword, arguments.overwrite)


if __name__ == '__main__':
    main()
