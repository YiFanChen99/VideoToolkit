"""Find where a cut of a video (the reference) lies in another copy of the same video (the target),
e.g. an old cut and a new, higher-quality download, so the old cut can be repeated on the new one.

Usage: python -m download_processing.match_clip <keyword>
       python -m download_processing.match_clip --reference <file> --target <file>
  keyword      words that pick exactly two videos in Downloads (every word must be in the file name, ignoring case
               and simplified/traditional Chinese); the shorter one is the reference, the longer one the target
  --reference  the cut to look for
  --target     the video to look in
Prints each stretch of the reference found in the target and, when the reference is one stretch of the target,
the --trim-start / --trim-end that cut the target down to it with convert_video.py.
Frames are compared as tiny grayscale thumbnails, so a different resolution, bitrate or codec doesn't matter,
but a different crop, mirror or overlay does.
Exit codes: 0 done (the reference matches), 1 error or no match, 2 the keyword does not pick exactly two videos.
"""
import argparse
import os
import subprocess
import sys

import numpy

from common.downloads import DOWNLOADS, name_matches
from common.measure import VIDEO_EXTENSIONS, format_seconds, probe

# Frames are shrunk to THUMBNAIL x THUMBNAIL gray pixels (aspect ratio ignored): 256 numbers per frame keeps an
# hour of 30 fps video at about 110 MB, and still tells frames apart.
THUMBNAIL = 16
# Both videos are sampled at the higher of their frame rates (at most this), so a frame index is the same time in
# both and the match is accurate to one frame.
HIGHEST_RATE = 60
# Every this many seconds of the reference, its frame votes for the offset of its best match in the target.
VOTE_INTERVAL = 0.25
# A vote counts only when its best match is at least this similar (1 = identical thumbnails, after removing
# brightness and contrast differences).
VOTE_SIMILARITY = 0.8
# An offset needs this many seconds' worth of votes to be checked frame by frame.
LEAST_VOTE_SECONDS = 1
# Frame by frame, two frames match when their similarity is at least this.
MATCH_SIMILARITY = 0.9
# Inside a stretch, up to this many seconds of frames below MATCH_SIMILARITY (e.g. a flash) are tolerated.
LONGEST_GAP = 0.5
# Stretches shorter than this are not reported.
SHORTEST_STRETCH = 1


def frame_rate(path):
    stream = probe(['-select_streams', 'v:0', '-show_entries', 'stream=avg_frame_rate', path])['streams'][0]
    numerator, denominator = (int(part) for part in stream['avg_frame_rate'].split('/'))
    return numerator / denominator if denominator else 0


def duration(path):
    return float(probe(['-show_entries', 'format=duration', path])['format']['duration'])


def thumbnails(path, rate):
    """One row per frame sampled at rate: the THUMBNAIL x THUMBNAIL gray pixels minus their mean, scaled to length 1,
    so the dot product of two rows is their correlation. A flat frame (e.g. all black) becomes all zeros and
    matches nothing."""
    command = ['ffmpeg', '-v', 'error', '-i', path, '-map', '0:v:0',
               '-vf', f'fps={rate},scale={THUMBNAIL}:{THUMBNAIL}:flags=area,format=gray', '-f', 'rawvideo', '-']
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        sys.exit(f'ffmpeg could not read {path}: {result.stderr.decode("utf-8", "replace").strip()}')
    frames = numpy.frombuffer(result.stdout, numpy.uint8).reshape(-1, THUMBNAIL * THUMBNAIL).astype(numpy.float32)
    frames -= frames.mean(axis=1, keepdims=True)
    lengths = numpy.linalg.norm(frames, axis=1, keepdims=True)
    return frames / numpy.maximum(lengths, 1e-3)


def offset_votes(reference, target, rate):
    """Target frame index minus reference frame index of each sampled reference frame's best match."""
    step = max(1, round(rate * VOTE_INTERVAL))
    indexes = numpy.arange(0, len(reference), step)
    votes = []
    for start in range(0, len(indexes), 256):  # 256 rows at a time keeps the similarity matrix small
        chunk = indexes[start:start + 256]
        similarities = reference[chunk] @ target.T
        best = similarities.argmax(axis=1)
        good = similarities[numpy.arange(len(chunk)), best] >= VOTE_SIMILARITY
        votes += list(best[good] - chunk[good])
    return votes, step


def candidate_offsets(votes, least_votes):
    """Offsets with at least least_votes votes within 2 frames of them, most voted first."""
    remaining = numpy.array(votes)
    offsets = []
    while len(remaining):
        values, counts = numpy.unique(remaining, return_counts=True)
        near = [counts[numpy.abs(values - value) <= 2].sum() for value in values]
        center = values[int(numpy.argmax(near))]
        if max(near) < least_votes:
            break
        offsets.append(int(center))
        remaining = remaining[numpy.abs(remaining - center) > 2]
    return offsets


def similarities_at(reference, target, offset):
    """(first reference index, similarity of each reference frame to the target frame offset frames later),
    for the reference frames that have such a target frame."""
    first, last = max(0, -offset), min(len(reference), len(target) - offset)
    if last <= first:
        return first, numpy.zeros(0, numpy.float32)
    return first, numpy.einsum('ij,ij->i', reference[first:last], target[first + offset:last + offset])


def best_offset_near(reference, target, center):
    """The offset within 3 frames of center whose frames are the most similar in total."""
    return max(range(center - 3, center + 4), key=lambda offset: similarities_at(reference, target, offset)[1].sum())


def stretches(matched, longest_gap, shortest):
    """(start, end) index ranges where matched is true, joining ranges split by at most longest_gap false values,
    keeping ranges at least shortest long."""
    found = []
    for index in numpy.flatnonzero(matched):
        if found and index - found[-1][1] <= longest_gap:
            found[-1][1] = index + 1
        else:
            found.append([index, index + 1])
    return [(start, end) for start, end in found if end - start >= shortest]


def find_matches(reference, target, rate):
    """Stretches of the reference found in the target, as (reference start, reference end, offset, similarity)
    in frames, sorted by reference start. A reference frame belongs to at most one stretch."""
    votes, step = offset_votes(reference, target, rate)
    claimed = numpy.zeros(len(reference), bool)
    matches = []
    for center in candidate_offsets(votes, max(2, round(LEAST_VOTE_SECONDS * rate / step))):
        offset = best_offset_near(reference, target, center)
        first, similarity = similarities_at(reference, target, offset)
        matched = similarity >= MATCH_SIMILARITY
        for start, end in stretches(matched, round(LONGEST_GAP * rate), round(SHORTEST_STRETCH * rate)):
            start, end = start + first, end + first
            free = ~claimed[start:end]
            if free.sum() < SHORTEST_STRETCH * rate:
                continue
            claimed[start:end] = True
            matches.append((start, end, offset, float(similarity[start - first:end - first].mean())))
    return sorted(matches)


def find_pair(keyword):
    """(reference, target): the two videos in Downloads matching the keyword, shorter first."""
    matches = sorted(name for name in os.listdir(DOWNLOADS)
                     if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS and name_matches(name, keyword))
    if len(matches) != 2:
        print(f'{len(matches)} videos in {DOWNLOADS} contain "{keyword}", need exactly 2:')
        for name in matches:
            print(f'  {name}')
        sys.exit(2)
    return sorted((os.path.join(DOWNLOADS, name) for name in matches), key=duration)


def main():
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument('keyword', nargs='?')
    parser.add_argument('--reference')
    parser.add_argument('--target')
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    if arguments.keyword:
        if arguments.reference or arguments.target:
            sys.exit('give a keyword or --reference and --target, not both')
        reference_path, target_path = find_pair(arguments.keyword)
    elif arguments.reference and arguments.target:
        reference_path, target_path = arguments.reference, arguments.target
    else:
        sys.exit('give a keyword, or both --reference and --target')
    for path in (reference_path, target_path):
        if not os.path.isfile(path):
            sys.exit(f'not a file: {path}')

    rate = min(HIGHEST_RATE, max(frame_rate(reference_path), frame_rate(target_path)))
    target_duration = duration(target_path)
    print(f'reference: {os.path.basename(reference_path)} ({format_seconds(duration(reference_path))})')
    print(f'target:    {os.path.basename(target_path)} ({format_seconds(target_duration)})')
    print(f'comparing at {round(rate, 3):g} fps', flush=True)
    reference, target = thumbnails(reference_path, rate), thumbnails(target_path, rate)

    def time(frames):
        return format_seconds(frames / rate)

    matches = find_matches(reference, target, rate)
    if not matches:
        sys.exit('the reference was not found in the target')
    print()
    for start, end, offset, similarity in matches:
        print(f'match: reference {time(start)}-{time(end)} = target {time(start + offset)}-{time(end + offset)}'
              f' ({end - start} frames, similarity {similarity:.3f})')
    covered = sum(end - start for start, end, _, _ in matches)
    if covered < len(reference):
        print(f'not found: {time(len(reference) - covered)} of the reference\'s {time(len(reference))}')
    if len(matches) == 1:
        start, end, offset, _ = matches[0]
        trim_start = (start + offset) / rate
        trim_end = max(0.0, target_duration - (end + offset) / rate)
        print(f'trim: --trim-start {format_seconds(trim_start)} --trim-end {format_seconds(trim_end)}')
    else:
        print('trim: none, the reference is several stretches of the target')


if __name__ == '__main__':
    main()
