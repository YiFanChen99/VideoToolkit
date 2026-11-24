"""Convert a merged download (.mkv in the Downloads folder) to AV1 at a given average bitrate.

Usage: python -m download_processing.convert_video <name> [<condition> ...] <bitrate> [--input <file.mkv>]
                                                  [--trim-start <time>] [--trim-end <time>] [--overwrite]
  name          the video's name: names the output, Downloads\\<name>.mp4, or <name> (2).mp4, <name> (3).mp4 ...
                when that already exists
  condition     extra words that, with the name, pick the one .mkv to convert
                (every word must be in the file name, ignoring case and simplified/traditional Chinese)
  bitrate       a shorthand listed in bitrates.txt, e.g. 2 for 2048 kbps
  --input       convert this file instead of searching Downloads
  --trim-start  cut this much off the beginning, e.g. 2, 2s, 1.5s or 1:05 (minutes:seconds)
  --trim-end    cut this much off the end, in the same format
  --overwrite   replace an existing Downloads\\<name>.mp4 instead of writing a numbered one
Before encoding, every other .mkv in Downloads that matches the name but not all conditions is deleted
(the trial cuts of the same video). Nothing is deleted when the source is ambiguous.
Every other setting comes from the HandBrake GUI preset BASE_PRESET.
Exit codes: 0 done, 1 error, 2 several .mkv files match (listed).
"""
import argparse
import os
import subprocess
import sys

from common.downloads import DOWNLOADS, name_matches
from common.measure import HEADER, describe, format_seconds, probe

HANDBRAKE_CLI = r'C:\Program Files\HandBrake\HandBrakeCLI.exe'
# A preset in the HandBrake GUI's Custom Presets: SVT-AV1 10-bit, average bitrate with multi-pass, MP4.
# Only its bitrate is replaced, by --vb.
BASE_PRESET = 'AV1 2560'
BITRATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bitrates.txt')
# The preset selects no audio tracks; keep every track, copied when the preset's copy mask allows.
KEEP_AUDIO = ['--all-audio', '--aencoder', 'copy', '--audio-fallback', 'av_aac']
# For a bitrate marked fast in bitrates.txt, a quick trial encode: one pass instead of the preset's multi-pass,
# and SVT-AV1's fastest speed preset (10 is the fastest HandBrake 1.11 offers; the base preset uses 6).
FAST_ENCODE = ['--no-multi-pass', '--encoder-preset', '10']


def bitrate_for(shorthand):
    """Look up (kbps, fast) for a shorthand in bitrates.txt: lines of `shorthand = kbps`, optionally followed by
    `fast` for a quick trial encode (see FAST_ENCODE); # starts a comment."""
    table = {}
    with open(BITRATES, encoding='utf-8') as file:
        for line in file:
            line = line.split('#')[0].strip()
            if line:
                name, setting = (part.strip() for part in line.split('=', 1))
                kbps, *flags = setting.split()
                if flags not in ([], ['fast']):
                    sys.exit(f'bitrates.txt: only "fast" may follow the kbps, in "{line}"')
                table[name] = (int(kbps), flags == ['fast'])
    if shorthand not in table:
        choices = ', '.join(f'{name} = {kbps}{" fast" if fast else ""}' for name, (kbps, fast) in table.items())
        sys.exit(f'bitrate "{shorthand}" is not in bitrates.txt ({choices})')
    return table[shorthand]


def seconds_in(text):
    """Parse a trim length: seconds with an optional trailing s (2, 2s, 1.5s) or minutes:seconds (1:05)."""
    value = text.strip().lower().removesuffix('s')
    try:
        if ':' in value:
            minutes, seconds = value.split(':', 1)
            result = int(minutes) * 60 + float(seconds)
        else:
            result = float(value)
    except ValueError:
        sys.exit(f'not a length of time: "{text}" (use e.g. 2, 2s, 1.5s or 1:05)')
    if result < 0:
        sys.exit(f'a trim cannot be negative: "{text}"')
    return result


def trim_options(source, trim_start, trim_end):
    """HandBrakeCLI options that keep the source minus trim_start at the start and trim_end at the end,
    on HandBrake's 90 kHz clock (--stop-at is a length counted from --start-at, not an end time),
    plus a line describing the kept range."""
    duration = float(probe(['-show_entries', 'format=duration', source])['format']['duration'])
    kept = duration - trim_start - trim_end
    if kept <= 0:
        sys.exit(f'trimming {trim_start:g}s + {trim_end:g}s leaves nothing of a {duration:g}s video')
    options = []
    if trim_start:
        options += ['--start-at', f'pts:{round(trim_start * 90000)}']
    if trim_end:
        options += ['--stop-at', f'pts:{round(kept * 90000)}']
    description = (f'trim: {format_seconds(trim_start)} off the start, {format_seconds(trim_end)} off the end, '
                   f'keeping {format_seconds(trim_start)}-{format_seconds(trim_start + kept)} '
                   f'of {format_seconds(duration)}')
    return options, description


def downloaded_mkvs():
    return sorted(name for name in os.listdir(DOWNLOADS) if name.lower().endswith('.mkv'))


def find_input(keyword):
    matches = [name for name in downloaded_mkvs() if name_matches(name, keyword)]
    if not matches:
        sys.exit(f'no .mkv in {DOWNLOADS} contains "{keyword}"')
    if len(matches) > 1:
        print(f'several .mkv files in {DOWNLOADS} contain "{keyword}":')
        for name in matches:
            print(f'  {name}')
        sys.exit(2)
    return os.path.join(DOWNLOADS, matches[0])


def unused_output_path(name):
    """Downloads\\<name>.mp4, or the first of <name> (2).mp4, <name> (3).mp4 ... that doesn't exist yet."""
    path, number = os.path.join(DOWNLOADS, name + '.mp4'), 2
    while os.path.exists(path):
        path, number = os.path.join(DOWNLOADS, f'{name} ({number}).mp4'), number + 1
    return path


def audio_packets(path):
    """Per audio track, (time in seconds, MD5) of every packet."""
    packets = probe(['-select_streams', 'a', '-show_data_hash', 'MD5',
                     '-show_entries', 'packet=stream_index,pts_time,data_hash', path]).get('packets', [])
    tracks = {}
    for packet in packets:
        tracks.setdefault(packet['stream_index'], []).append(
            (float(packet.get('pts_time', 0)), packet['data_hash']))
    return [tracks[index] for index in sorted(tracks)]


# With --start-at, HandBrake's audio sync sometimes drops a copied packet (seen: one 21 ms packet near the end
# of a 3-minute video). A trimmed output may skip up to this many source packets; more fails the check.
MOST_SKIPPED_PACKETS = 10


def skipped_packets(copied, source):
    """Indexes of the source packets skipped inside the stretch the output was copied from, choosing the stretch
    that skips the fewest, or None when the output is not the source's packets in order.
    Every stretch starting at a copy of the output's first packet is tried, since identical packets
    (e.g. silence) can make an earlier start look possible but skip many more."""
    best = None
    for start in (index for index, md5 in enumerate(source) if copied and md5 == copied[0]):
        skipped, position = [], start
        for md5 in copied:
            while position < len(source) and source[position] != md5:
                skipped.append(position)
                position += 1
            if position == len(source):
                break
            position += 1
        else:
            if best is None or len(skipped) < len(best):
                best = skipped
            if not best:
                break
    return best


def check_audio(source, output, trimmed):
    """Return (whether every audio track was copied untouched, notes on skipped packets).
    Untrimmed, the output must have exactly the source's packets. Packets before time 0 (encoder priming,
    never played) may be dropped: HandBrake drops them from an MP4 source, but keeps them, moved to time 0,
    from an MKV one.
    Trimmed, the output must be the source's packets in order, with at most MOST_SKIPPED_PACKETS skipped:
    a re-encoded packet never matches a source packet's MD5, so this still proves the audio was copied."""
    source_tracks, output_tracks = audio_packets(source), audio_packets(output)
    if len(source_tracks) != len(output_tracks):
        return False, []
    notes = []
    for track, (source_track, output_track) in enumerate(zip(source_tracks, output_tracks), 1):
        every = [md5 for _, md5 in source_track]
        copied = [md5 for _, md5 in output_track]
        if not trimmed:
            if copied not in (every, [md5 for time, md5 in source_track if time >= 0]):
                return False, []
            continue
        skipped = skipped_packets(copied, every)
        if skipped is None or len(skipped) > MOST_SKIPPED_PACKETS:
            return False, []
        if skipped:
            times = ', '.join(format_seconds(source_track[index][0]) for index in skipped)
            notes.append(f'track {track}: {len(skipped)} packet(s) skipped by HandBrake at {times} in the source')
    return True, notes


def main():
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument('name')
    parser.add_argument('conditions', nargs='*')
    parser.add_argument('bitrate')
    parser.add_argument('--input')
    parser.add_argument('--trim-start', default='0')
    parser.add_argument('--trim-end', default='0')
    parser.add_argument('--overwrite', action='store_true')
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')  # sys.exit messages go to stderr
    kbps, fast = bitrate_for(arguments.bitrate)
    trim_start, trim_end = seconds_in(arguments.trim_start), seconds_in(arguments.trim_end)
    trimmed = bool(trim_start or trim_end)
    keyword = ' '.join([arguments.name] + arguments.conditions)

    source = arguments.input or find_input(keyword)
    if not os.path.isfile(source):
        sys.exit(f'not a file: {source}')
    output = (os.path.join(DOWNLOADS, arguments.name + '.mp4') if arguments.overwrite
              else unused_output_path(arguments.name))
    # Checked before deleting anything, so a trim longer than the video deletes nothing.
    trim, trim_description = trim_options(source, trim_start, trim_end) if trimmed else ([], None)

    for name in downloaded_mkvs():
        if (name_matches(name, arguments.name) and not name_matches(name, keyword)
                and name != os.path.basename(source)):
            os.remove(os.path.join(DOWNLOADS, name))
            print(f'deleted (does not match "{keyword}"): {name}')

    print(f'{os.path.basename(source)} -> {os.path.basename(output)} ({kbps} kbps'
          f'{", fast: one pass, speed preset 10" if fast else ""})', flush=True)
    if trim_description:
        print(trim_description, flush=True)
    # Encode under a temporary name, so an interrupted run never leaves a half-made file under the real name.
    partial = os.path.splitext(output)[0] + '.converting.mp4'
    command = [HANDBRAKE_CLI, '--preset-import-gui', '--preset', BASE_PRESET, '--vb', str(kbps),
               *(FAST_ENCODE if fast else []), *KEEP_AUDIO, *trim, '--input', source, '--output', partial]
    # HandBrakeCLI prints progress on stdout (shown as is) and its log on stderr (kept for errors).
    result = subprocess.run(command, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0 or not os.path.isfile(partial):
        print('\n'.join(result.stderr.splitlines()[-30:]))
        if os.path.exists(partial):
            os.remove(partial)
        sys.exit(f'HandBrakeCLI failed (exit code {result.returncode})')
    os.replace(partial, output)

    print()
    print('\t'.join(HEADER))
    for path in (source, output):
        print('\t'.join([os.path.basename(path)] + (describe(path) or ['讀取失敗'])))
    copied, notes = check_audio(source, output, trimmed)
    if not copied:
        sys.exit('audio differs from the source (re-encoded or tracks lost)')
    print('audio: identical to the source' if not notes else 'audio: copied from the source; ' + '; '.join(notes))


if __name__ == '__main__':
    main()
