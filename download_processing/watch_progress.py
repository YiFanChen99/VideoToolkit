"""Report a running convert_video.py encode's progress now and then, for a Claude Code Monitor.

Usage: python -m download_processing.watch_progress <output file>
  output file  the file the background convert_video.py run writes its stdout to
Prints one line per report, e.g. "第 1/2 趟 35%，全部約剩 6 分": the first as soon as the time left can be told
(within FIRST_REPORT_SECONDS either way), then sooner when little time is left and further apart for a long
encode. Exits when the run's output ends with "[exited with code", which Claude Code
appends when the background command ends, or after STALL_MINUTES without any new output (reported).
"""
import os
import re
import sys
import time

# HandBrakeCLI progress, e.g. "Encoding: task 2 of 2, 32.77 % (29.41 fps, avg 32.75 fps, ETA 00h02m25s)".
# Lines end in \r and the last one may still be half written, so only a whole line counts.
PROGRESS = re.compile(r'Encoding: task (\d+) of (\d+), ([\d.]+) %(?: \(.*ETA (\d+)h(\d+)m(\d+)s\))?')
FINISHED = '[exited with code'
# With the AV1 2560 preset's multi-pass, the second pass ran at about 31 fps and the first at about 85 fps
# (a 4:43 1080p video on this machine), so the second pass takes about this many times as long as the first.
SECOND_PASS_SLOWER = 2.7
# The first pass's speed is measured from its first progress line seen, over at least this long and this much
# progress, so the time spent probing and scanning before encoding starts doesn't count.
MEASURE_SECONDS = 10
MEASURE_PERCENT = 1
FIRST_REPORT_SECONDS = 30  # report by then even when the time left can't be told yet
POLL_SECONDS = 5
STALL_MINUTES = 10
TAIL_BYTES = 4096


def latest_progress(path):
    """The last progress line's (task, tasks, percent, ETA seconds or None), or None before the first one."""
    with open(path, 'rb') as file:
        file.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
        tail = file.read().decode('utf-8', errors='replace')
    lines = [PROGRESS.fullmatch(line.strip()) for line in re.split(r'[\r\n]', tail)]
    matches = [match for match in lines if match]
    if not matches:
        return None
    task, tasks, percent, hours, minutes, seconds = matches[-1].groups()
    eta = None if hours is None else int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    return int(task), int(tasks), float(percent), eta


def is_first_of_two_passes(progress):
    return progress[:2] == (1, 2)


def remaining_seconds(progress, first_seen, now):
    """Seconds left for the whole encode, or None while too early to tell.
    first_seen is (time, percent) of the first pass's first progress line seen."""
    task, tasks, percent, eta = progress
    if not is_first_of_two_passes(progress):
        return eta  # the last pass (or the only one): HandBrakeCLI's own ETA covers the rest
    seen_at, seen_percent = first_seen
    if now - seen_at < MEASURE_SECONDS or percent - seen_percent < MEASURE_PERCENT:
        return None
    seconds_per_percent = (now - seen_at) / (percent - seen_percent)
    return seconds_per_percent * (100 - percent + SECOND_PASS_SLOWER * 100)


def describe_remaining(seconds):
    minutes = round(seconds / 60)
    if minutes < 1:
        return '剩不到 1 分'
    if minutes < 60:
        return f'約剩 {minutes} 分'
    return f'約剩 {minutes // 60} 小時 {minutes % 60} 分'


def report(progress, remaining):
    task, tasks, percent, _ = progress
    if remaining is None:
        left = '還算不出剩多久'
    else:
        left = ('全部' if tasks > 1 else '') + describe_remaining(remaining)
    passes = f'第 {task}/{tasks} 趟 ' if tasks > 1 else ''
    return f'{passes}{percent:.0f}%，{left}'


def wait_minutes(remaining):
    """How long to wait before the next report: often near the end, rarely on a long encode."""
    if remaining is None:
        return 1
    if remaining <= 10 * 60:
        return 2
    if remaining <= 60 * 60:
        return 5
    return 10


def finished(path):
    with open(path, 'rb') as file:
        file.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
        return FINISHED in file.read().decode('utf-8', errors='replace')


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    sys.stdout.reconfigure(encoding='utf-8')
    started = os.path.getctime(path)  # the run's output file is created when the run starts
    next_report = started + FIRST_REPORT_SECONDS
    first_seen = None
    estimated = False  # whether a report has told the time left yet
    while True:
        if finished(path):
            return
        now = time.time()
        if now - os.path.getmtime(path) > STALL_MINUTES * 60:
            print(f'已經 {STALL_MINUTES} 分鐘沒有新輸出，轉檔可能卡住了', flush=True)
            sys.exit(1)
        progress = latest_progress(path)
        if progress is not None:
            if first_seen is None and is_first_of_two_passes(progress):
                first_seen = (now, progress[2])
            remaining = remaining_seconds(progress, first_seen, now)
            first_estimate = remaining is not None and not estimated
            if first_estimate or now >= next_report:
                print(report(progress, remaining), flush=True)
                estimated = estimated or remaining is not None
                next_report = now + wait_minutes(remaining) * 60
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
