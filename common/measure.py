"""Measure videos with ffprobe (on PATH): video-only bitrate, codec, dimensions, size and duration,
and format lengths of time for printing."""
import json
import os
import subprocess

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.flv', '.avi', '.mov', '.wmv', '.webm', '.ts', '.m4v'}
CODEC_NAMES = {'hevc': 'h265'}
# The titles of describe()'s values, after a file name column
HEADER = ['檔名', 'kbps(影像碼率)', '編碼', '大小(MB)', '長度']


def probe(arguments):
    result = subprocess.run(['ffprobe', '-v', 'error', '-of', 'json'] + arguments,
                            capture_output=True, text=True, encoding='utf-8')
    return json.loads(result.stdout or '{}')


def format_duration(seconds):
    total = round(seconds)
    return f'{total // 3600}:{total % 3600 // 60:02}:{total % 60:02}'


def format_seconds(seconds):
    """1:05.25 style: minutes:seconds with up to 3 decimals, trailing zeros dropped."""
    minutes, rest = divmod(seconds, 60)
    return f'{int(minutes)}:{rest:06.3f}'.rstrip('0').rstrip('.')


DIMENSION_ENTRIES = 'stream=width,height:stream_side_data=rotation:stream_tags=rotate'


def dimensions_of(stream):
    """(width, height) as the video is shown: swapped when it is stored rotated by 90° (e.g. filmed on a phone)."""
    width, height = stream.get('width'), stream.get('height')
    rotation = stream.get('tags', {}).get('rotate', 0)
    for side_data in stream.get('side_data_list', []):
        rotation = side_data.get('rotation', rotation)
    if int(float(rotation)) % 180:
        width, height = height, width
    return width, height


def measure_dimensions(path):
    """(width, height) of the video, reading only the file's header: fast, for rows measured before 寬/高 existed."""
    return dimensions_of((probe(['-select_streams', 'v:0', '-show_entries', DIMENSION_ENTRIES, path])
                          .get('streams') or [{}])[0])


def measure(path):
    """Return {kbps, codec, width, height, megabytes, seconds} for one video, or None if ffprobe can't read it."""
    info = probe(['-select_streams', 'v:0',
                  '-show_entries', f'format=duration:stream=codec_name,duration:{DIMENSION_ENTRIES}', path])
    stream = (info.get('streams') or [{}])[0]
    try:
        duration = float(info['format']['duration'])
    except (KeyError, ValueError):
        return None
    try:
        video_duration = float(stream['duration'])
    except (KeyError, ValueError):
        video_duration = duration
    # Video-only bitrate: total size of the video stream's packets over its duration.
    # Works the same for every container, unlike the bitrate a file records about itself.
    # This reads the whole file, so it is what makes probing slow.
    packets = probe(['-select_streams', 'v:0', '-show_entries', 'packet=size', path]).get('packets', [])
    video_bytes = sum(int(packet['size']) for packet in packets)
    codec = stream.get('codec_name', '?')
    width, height = dimensions_of(stream)
    return {'kbps': round(video_bytes * 8 / video_duration / 1000),
            'codec': CODEC_NAMES.get(codec, codec),
            'width': width,
            'height': height,
            'megabytes': round(os.path.getsize(path) / 1e6),
            'seconds': duration}


def describe(path):
    """Return [kbps, codec, size, duration] as text for one video, or None if ffprobe can't read it."""
    measured = measure(path)
    if measured is None:
        return None
    return [str(measured['kbps']), measured['codec'], str(measured['megabytes']),
            format_duration(measured['seconds'])]
