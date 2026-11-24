"""Keep a Google Sheets list of the videos in the folders listed in folders.txt: each video's bitrate, codec, size
and duration, next to columns of your own (status, tags) that the script never changes.

Usage: python -m video_list.list_video_info [<folder name> ...] [--spreadsheet <ID>]
  folder name    a name in folders.txt: update only these folders' videos (default: every folder in folders.txt)
  --spreadsheet  the spreadsheet to update, by the ID in its URL (default: SPREADSHEET_ID). It needs a worksheet
                 named 影片 and must be shared, as editor, with the service account whose key is KEY_FILE
Only new videos and videos whose size or modified time changed are probed with ffprobe; the rest keep their row.
A video gone from its folder is marked 已不存在 rather than removed, and one that was moved or renamed
(same size and modified time) keeps its row and your columns. A folder that cannot be found is skipped, untouched.
Needs gspread (pip install gspread). The spreadsheet can stay open in the browser, even be sorted, while it runs.
"""
import argparse
import collections
import datetime
import os
import sys
import time

import gspread
from gspread.utils import DateTimeOption, ValueRenderOption, rowcol_to_a1

from common.measure import VIDEO_EXTENSIONS, measure, measure_dimensions

SCRIPT_FOLDER = os.path.dirname(os.path.abspath(__file__))
FOLDERS = os.path.join(SCRIPT_FOLDER, 'folders.txt')
# The ID in the spreadsheet's URL: https://docs.google.com/spreadsheets/d/<ID>/edit
SPREADSHEET_ID = '1964Jce1kqjh0EYHQcSERfEThsWoSJUbp17NiyLmnJp4'
# The service account's key; the spreadsheet is shared with the account as editor
KEY_FILE = os.path.join(os.environ.get('APPDATA', ''), 'gspread', 'video-toolkit.json')
SHEET = '影片'

# The spreadsheet's columns are found by their header, so they can be moved around. The script writes only these;
# every other column (狀態, tags) is yours and is kept as it is.
FOLDER, NAME, EXTENSION, DIFF = '資料夾', '檔名', '副檔名', 'Diff'
KBPS, CODEC, MEGABYTES, DURATION = 'kbps(影像碼率)', '編碼', '大小(MB)', '長度'
WIDTH, HEIGHT = '寬', '高'
BYTES, MODIFIED = '位元組', '修改時間'
SCRIPT_COLUMNS = [NAME, FOLDER, EXTENSION, KBPS, CODEC, WIDTH, HEIGHT, MEGABYTES, DURATION, DIFF, MODIFIED,
                  BYTES]
MEASURED_COLUMNS = [KBPS, CODEC, WIDTH, HEIGHT, MEGABYTES, DURATION]
# An empty worksheet's columns, in order: the script's, then a narrow blank column (None) setting them apart from
# yours; 狀態 is yours, for marking videos to hide (e.g. 隱藏), and your tag columns go after it.
# 檔名 is first, frozen with the header row, so it stays in view while scrolling right.
NEW_SHEET_COLUMNS = [NAME, FOLDER, EXTENSION, KBPS, CODEC, WIDTH, HEIGHT, MEGABYTES, DURATION, MODIFIED, BYTES,
                     DIFF, None, '狀態']
SEPARATOR_PIXELS = 10
# Number formats for an empty worksheet's columns; later the rows a run adds copy the format of the row above
NUMBER_FORMATS = {DURATION: {'type': 'TIME', 'pattern': '[h]:mm:ss'},
                  MODIFIED: {'type': 'DATE_TIME', 'pattern': 'yyyy-mm-dd hh:mm:ss'},
                  BYTES: {'type': 'NUMBER', 'pattern': '0'}}
# Sheets keeps dates and durations as a number of days, dates counted from this day
SERIAL_EPOCH = datetime.datetime(1899, 12, 30)
# Diff is left blank while nothing about the file needs a look, so only what does stands out.
# 新增 marks a video a run added, 更新 one whose content changed or that was moved or renamed; both stay until you
# clear them (or the file goes missing or unreadable). A 新增 not yet cleared stays 新增 when its file changes.
PRESENT, NEW, UPDATED, GONE, UNREADABLE = None, '新增', '更新', '已不存在', '讀取失敗'
# While probing, save at least this often, so stopping a long run keeps what is done
SAVE_EVERY_SECONDS = 60

Video = collections.namedtuple('Video', 'folder name extension path size modified')


def read_folders():
    """Read folders.txt: lines of `name = path`, # starts a comment line. Returns {name: path} in file order."""
    if not os.path.exists(FOLDERS):
        sys.exit(f'找不到 {FOLDERS}：複製同資料夾的 folders.example.txt 成 folders.txt，再改成自己的資料夾')
    folders = {}
    with open(FOLDERS, encoding='utf-8-sig') as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                sys.exit(f'folders.txt 每行要寫成「名稱 = 路徑」：{line}')
            name, path = (part.strip() for part in line.split('=', 1))
            if name in folders:
                sys.exit(f'folders.txt 有兩個「{name}」')
            folders[name] = os.path.join(SCRIPT_FOLDER, path.strip('"'))  # an absolute path stays as it is
    return folders


def video_key(folder, name, extension):
    """What identifies a video's row; case-insensitive like Windows file names."""
    return str(folder), str(name).casefold(), str(extension or '').casefold()


def file_stamp(path):
    """(size in bytes, modified time to the second): the file counts as changed when either differs."""
    status = os.stat(path)
    return status.st_size, datetime.datetime.fromtimestamp(status.st_mtime).replace(microsecond=0)


def scan(folder, root):
    """Every video under root, with its name relative to root, without the extension."""
    for directory, _, file_names in os.walk(root):
        for file_name in file_names:
            extension = os.path.splitext(file_name)[1]
            if extension.lower() in VIDEO_EXTENSIONS:
                path = os.path.join(directory, file_name)
                name = os.path.splitext(os.path.relpath(path, root))[0].replace(os.sep, '/')
                yield Video(folder, name, extension[1:].lower(), path, *file_stamp(path))


def to_cell(value):
    """A value as the spreadsheet keeps it: dates and durations as days, shown through the cell's number format."""
    if isinstance(value, datetime.datetime):
        return (value - SERIAL_EPOCH) / datetime.timedelta(days=1)
    if isinstance(value, datetime.timedelta):
        return value / datetime.timedelta(days=1)
    return value


def headers_of(grid):
    """{column title: column number} from the grid's first row."""
    columns = {}
    for column, value in enumerate(grid[0] if grid else [], 1):
        if value is not None:
            title = str(value).strip()
            if title in columns:
                sys.exit(f'「{SHEET}」第 1 列有兩個「{title}」欄')
            columns[title] = column
    return columns


def value_in(values, column):
    return values[column - 1] if column <= len(values) else None


class VideoSheet:
    """The spreadsheet's 影片 worksheet, read whole into memory: columns found by header, rows found by video.
    Changes stay in memory until save(), which writes only the changed cells. It finds each row again by its video
    first, so a row sorted or moved in the browser while the script runs still gets its own values."""

    def __init__(self, spreadsheet_id):
        if not os.path.exists(KEY_FILE):
            sys.exit(f'找不到 service account 的金鑰 {KEY_FILE}')
        try:
            self.spreadsheet = gspread.service_account(filename=KEY_FILE).open_by_key(spreadsheet_id)
            self.worksheet = self.spreadsheet.worksheet(SHEET)
        except gspread.WorksheetNotFound:
            sys.exit(f'試算表沒有名為「{SHEET}」的工作表')
        except (gspread.SpreadsheetNotFound, gspread.exceptions.APIError) as error:
            sys.exit(f'打不開試算表 {spreadsheet_id}：確認 ID 正確，而且已經共用給 service account（編輯者）。{error}')
        self.url = self.spreadsheet.url
        self.grid = self.read()  # grid[row - 1][column - 1], None for a blank cell

        self.columns = headers_of(self.grid)
        if self.columns:  # columns this version adds, or deleted by hand, go after the last one
            missing = [title for title in SCRIPT_COLUMNS if title not in self.columns]
            for title in missing:
                self.columns[title] = max(self.columns.values()) + 1
            if missing:
                self.add_headers(missing, empty=False)
        else:
            self.columns = {title: column for column, title in enumerate(NEW_SHEET_COLUMNS, 1) if title}
            self.add_headers([title for title in NEW_SHEET_COLUMNS if title], empty=True)

        self.rows = {}  # video_key -> row number, as the rows were when read; the script keeps using these numbers
        self.saved_keys = {}  # row number -> the video_key of the row as last read or saved: how save() finds it
        self.last_row = 1
        for row in range(2, len(self.grid) + 1):
            if any(value is not None for value in self.grid[row - 1]):
                self.last_row = row
            folder, name = self.get(row, FOLDER), self.get(row, NAME)
            if folder is None or name is None:
                continue
            key = video_key(folder, name, self.get(row, EXTENSION))
            if key in self.rows:
                sys.exit(f'第 {self.rows[key]} 列和第 {row} 列是同一部影片：{folder}/{name}')
            self.rows[key] = row
            self.saved_keys[row] = key
        self.changes = collections.defaultdict(dict)  # row -> {column title: value}, not saved yet
        self.unsaved_rows = []  # rows add() started, not in the spreadsheet yet
        self.unhide_diff = False

    def read(self):
        """Every row's values, None for a blank cell; numbers as numbers, dates and durations as days."""
        rows = self.worksheet.get(value_render_option=ValueRenderOption.unformatted,
                                  date_time_render_option=DateTimeOption.serial_number)
        return [[None if value == '' else value for value in row] for row in rows]

    def area(self, first_row=None, last_row=None, first_column=None, last_column=None):
        """A range of whole rows and columns (numbered from 1, ends included, None for open) as batchUpdate takes it."""
        area = {'sheetId': self.worksheet.id}
        for name, number in (('startRowIndex', first_row and first_row - 1), ('endRowIndex', last_row),
                             ('startColumnIndex', first_column and first_column - 1), ('endColumnIndex', last_column)):
            if number is not None:
                area[name] = number
        return area

    def add_headers(self, titles, empty):
        """Write the bold headers of the columns the worksheet lacks. An empty worksheet also gets the header row and
        檔名 frozen, number formats for the columns that need them, and the narrow blank column."""
        last_column = max(self.columns.values())
        if last_column > self.worksheet.col_count:
            self.worksheet.add_cols(last_column - self.worksheet.col_count)
        requests = []
        for title in titles:
            column = self.columns[title]
            requests.append({'repeatCell': {
                'range': self.area(1, 1, column, column),
                'cell': {'userEnteredValue': {'stringValue': title},
                         'userEnteredFormat': {'textFormat': {'bold': True}}},
                'fields': 'userEnteredValue,userEnteredFormat.textFormat.bold'}})
            if empty and title in NUMBER_FORMATS:
                requests.append({'repeatCell': {
                    'range': self.area(2, None, column, column),
                    'cell': {'userEnteredFormat': {'numberFormat': NUMBER_FORMATS[title]}},
                    'fields': 'userEnteredFormat.numberFormat'}})
        if empty:
            requests.append({'updateSheetProperties': {
                'properties': {'sheetId': self.worksheet.id,
                               'gridProperties': {'frozenRowCount': 1, 'frozenColumnCount': 1}},
                'fields': 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}})
            separator = NEW_SHEET_COLUMNS.index(None) + 1
            requests.append({'updateDimensionProperties': {
                'range': {'sheetId': self.worksheet.id, 'dimension': 'COLUMNS',
                          'startIndex': separator - 1, 'endIndex': separator},
                'properties': {'pixelSize': SEPARATOR_PIXELS}, 'fields': 'pixelSize'}})
        self.spreadsheet.batch_update({'requests': requests})
        if not self.grid:
            self.grid.append([])
        for title in titles:
            self.grid[0].extend([None] * (self.columns[title] - len(self.grid[0])))
            self.grid[0][self.columns[title] - 1] = title

    def get(self, row, title):
        return value_in(self.grid[row - 1], self.columns[title]) if row <= len(self.grid) else None

    def set(self, row, title, value):
        value = to_cell(value)
        if self.get(row, title) == value:
            return
        while len(self.grid) < row:
            self.grid.append([])
        values, column = self.grid[row - 1], self.columns[title]
        values.extend([None] * (column - len(values)))
        values[column - 1] = value
        self.changes[row][title] = value

    def label(self, row):
        return f'{self.get(row, FOLDER)}/{self.get(row, NAME)}.{self.get(row, EXTENSION)}'

    def stamp(self, row):
        """The (size, modified) recorded for the row, or None when missing or edited into something else."""
        size, modified = self.get(row, BYTES), self.get(row, MODIFIED)
        if isinstance(size, float) and size.is_integer():
            size = int(size)
        if not isinstance(size, int) or not isinstance(modified, (int, float)):
            return None
        # A time kept as a fraction of a day may read back a microsecond off
        modified = SERIAL_EPOCH + datetime.timedelta(days=modified, microseconds=500_000)
        return size, modified.replace(microsecond=0)

    def is_current(self, row, video):
        """True when the row's measurement still holds for the file on disk, so it needn't be probed again."""
        return (self.stamp(row) == (video.size, video.modified)
                and self.get(row, DIFF) in (PRESENT, NEW, UPDATED, GONE)
                and self.get(row, KBPS) is not None)

    def place(self, row, video):
        """Point the row at this video: its folder, name and extension."""
        self.set(row, FOLDER, video.folder)
        self.set(row, NAME, video.name)
        self.set(row, EXTENSION, video.extension)
        self.rows[video_key(video.folder, video.name, video.extension)] = row

    def add(self, video):
        """Start a row for a new video, after the last row; save() formats it like the row above it."""
        self.last_row += 1
        self.unsaved_rows.append(self.last_row)
        self.place(self.last_row, video)
        return self.last_row

    def mark(self, row, mark):
        """Write a 新增 or 更新 mark in Diff; a 新增 not yet cleared stays, as the video is still new to you."""
        if self.get(row, DIFF) != NEW:
            self.set(row, DIFF, mark)

    def record(self, row, video, measured, mark=None):
        """Write what ffprobe found, together with the size and modified time it was found for, and the mark."""
        if measured is None:
            for title in MEASURED_COLUMNS:
                self.set(row, title, None)
            self.set(row, DIFF, UNREADABLE)
        else:
            self.set(row, KBPS, measured['kbps'])
            self.set(row, CODEC, measured['codec'])
            self.set(row, WIDTH, measured['width'])
            self.set(row, HEIGHT, measured['height'])
            self.set(row, MEGABYTES, measured['megabytes'])
            self.set(row, DURATION, datetime.timedelta(seconds=round(measured['seconds'])))
            if mark:
                self.mark(row, mark)
            elif self.get(row, DIFF) in (GONE, UNREADABLE):  # readable again; a 新增 or 更新 stays
                self.set(row, DIFF, PRESENT)
        self.set(row, BYTES, video.size)
        self.set(row, MODIFIED, video.modified)

    def show_diff(self):
        """Unhide the Diff column at the next save, so the marks a run leaves are seen."""
        self.unhide_diff = True

    def save(self):
        """Write the changed cells. Each row is found again by its video, as rows may have been sorted, inserted or
        deleted in the browser meanwhile; a row no longer found is reported and its changes dropped. New rows go
        after the last row."""
        if not self.changes and not self.unhide_diff:
            return
        columns, requests, filter_requests, data, lost = self.columns, [], [], [], []
        if self.changes:
            grid = self.read()
            columns = headers_of(grid)
            missing = [title for title in SCRIPT_COLUMNS if title not in columns]
            if missing:
                sys.exit(f'「{SHEET}」第 1 列找不到「{"」「".join(missing)}」欄（執行中被改掉了？），'
                         f'還沒存的結果沒有寫入')
            located, last_row = {}, 1
            for row, values in enumerate(grid[1:], 2):
                if any(value is not None for value in values):
                    last_row = row
                folder, name = value_in(values, columns[FOLDER]), value_in(values, columns[NAME])
                if folder is not None and name is not None:
                    located[video_key(folder, name, value_in(values, columns[EXTENSION]))] = row
            targets = {}  # our row number -> its row in the spreadsheet now
            for row in self.changes:
                if row not in self.unsaved_rows:
                    if self.saved_keys.get(row) in located:
                        targets[row] = located[self.saved_keys[row]]
                    else:
                        lost.append(row)
            for offset, row in enumerate(self.unsaved_rows, 1):
                targets[row] = last_row + offset
            if self.unsaved_rows:
                requests, filter_requests = self.new_row_requests(last_row + 1, last_row + len(self.unsaved_rows))
            for row, target in targets.items():
                # one range per run of neighbouring columns, so the cells between them (yours) are left alone
                cells = sorted((columns[title], value) for title, value in self.changes[row].items())
                runs = []
                for column, value in cells:
                    if runs and runs[-1][0] + len(runs[-1][1]) == column:
                        runs[-1][1].append(value)
                    else:
                        runs.append((column, [value]))
                for column, values in runs:
                    data.append({'range': f"'{SHEET}'!{rowcol_to_a1(target, column)}:"
                                          f"{rowcol_to_a1(target, column + len(values) - 1)}",
                                 'values': [['' if value is None else value for value in values]]})
        if self.unhide_diff:
            filter_requests.append({'updateDimensionProperties': {
                'range': {'sheetId': self.worksheet.id, 'dimension': 'COLUMNS',
                          'startIndex': columns[DIFF] - 1, 'endIndex': columns[DIFF]},
                'properties': {'hiddenByUser': False}, 'fields': 'hiddenByUser'}})
        if requests:  # first, so the rows the values go in exist
            self.spreadsheet.batch_update({'requests': requests})
        if data:  # RAW, so a name like 0.MV or 20230329 stays text
            self.spreadsheet.values_batch_update({'valueInputOption': 'RAW', 'data': data})
        if filter_requests:  # last: nothing here may move a row, but if it did, the values are already in place
            self.spreadsheet.batch_update({'requests': filter_requests})

        for row in self.changes:
            if row not in lost:
                self.saved_keys[row] = video_key(self.get(row, FOLDER), self.get(row, NAME),
                                                 self.get(row, EXTENSION))
        if lost:
            print(f'找不到這些列（執行中被刪掉了，或檔名被改了？），它們這次的結果沒有寫入：'
                  f'{"、".join(self.label(row) for row in lost)}')
        self.changes.clear()
        self.unsaved_rows = []
        self.unhide_diff = False

    def new_row_requests(self, first_row, last_row):
        """Requests making rows first_row to last_row ready for new videos: (those to send before writing the
        values: added to the grid if beyond it, given the format and dropdowns of the row above; those to send
        after: the filter stretched over them, its conditions kept)."""
        sheet = next(sheet for sheet in self.spreadsheet.fetch_sheet_metadata(
            {'fields': 'sheets(properties(sheetId,gridProperties),basicFilter)'})['sheets']
                     if sheet['properties']['sheetId'] == self.worksheet.id)
        grid = sheet['properties']['gridProperties']
        requests = []
        if last_row > grid['rowCount']:
            requests.append({'appendDimension': {'sheetId': self.worksheet.id, 'dimension': 'ROWS',
                                                 'length': last_row - grid['rowCount']}})
        if first_row > 2:  # not the header's format
            requests.append({'copyPaste': {
                'source': self.area(first_row - 1, first_row - 1, 1, grid['columnCount']),
                'destination': self.area(first_row, last_row, 1, grid['columnCount']),
                'pasteType': 'PASTE_FORMAT'}})
        filter_requests = []
        basic_filter = sheet.get('basicFilter')
        if basic_filter and basic_filter['range'].get('endRowIndex', last_row) < last_row:
            del basic_filter['range']['endRowIndex']  # to the last row, however many there are
            if 'filterSpecs' in basic_filter:
                basic_filter.pop('criteria', None)  # the older form of filterSpecs; the API takes one of them
            # sortSpecs records the filter's last sort, and setting a filter with it sorts every row again
            basic_filter.pop('sortSpecs', None)
            filter_requests.append({'setBasicFilter': {'filter': basic_filter}})
        return requests, filter_requests


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folders', nargs='*', metavar='folder name')
    parser.add_argument('--spreadsheet', default=SPREADSHEET_ID)
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    arguments = parser.parse_args()

    folders = read_folders()
    unknown = [name for name in arguments.folders if name not in folders]
    if unknown:
        sys.exit(f'folders.txt 沒有：{"、".join(unknown)}（可用：{"、".join(folders)}）')
    sheet = VideoSheet(arguments.spreadsheet)

    scanned, on_disk = set(), {}
    for folder in arguments.folders or folders:
        if not os.path.isdir(folders[folder]):
            print(f'略過「{folder}」：找不到 {folders[folder]}')
            continue
        scanned.add(folder)
        for video in scan(folder, folders[folder]):
            on_disk[video_key(video.folder, video.name, video.extension)] = video

    # Rows whose file is gone: those in the scanned folders, plus those elsewhere whose file is known to be gone
    # (its folder is online) or can't be checked (its folder was renamed or dropped in folders.txt).
    # A new video with the same size and modified time as exactly one of them is that file, moved or renamed.
    gone_rows, gone_by_stamp = set(), collections.defaultdict(list)
    for key, row in sheet.rows.items():
        if key in on_disk:
            continue
        folder = str(sheet.get(row, FOLDER))
        if folder in scanned:
            gone_rows.add(row)
        elif folder in folders and (not os.path.isdir(folders[folder]) or os.path.exists(
                os.path.join(folders[folder], f'{sheet.get(row, NAME)}.{sheet.get(row, EXTENSION)}'))):
            continue
        if sheet.stamp(row):
            gone_by_stamp[sheet.stamp(row)].append(row)

    report = collections.defaultdict(list)
    unchanged = 0
    to_probe = []  # (row, or None for a new video; video; the mark for Diff, or None)
    to_fill = []  # (row, video) for an unchanged video whose 寬/高 are missing
    for key, video in sorted(on_disk.items()):
        row = sheet.rows.get(key)
        label = f'{video.folder}/{video.name}.{video.extension}'
        mark = None
        if row is None:
            matches = gone_by_stamp.get((video.size, video.modified), [])
            if len(matches) != 1:
                to_probe.append((None, video, NEW))
                report['新增'].append(label)
                continue
            row = matches.pop()
            report['搬移或改名'].append(f'{sheet.label(row)} → {label}')
            gone_rows.discard(row)
            mark = UPDATED
        elif not sheet.is_current(row, video):
            if sheet.get(row, DIFF) != UNREADABLE:  # an unreadable video is simply tried again
                report['內容變了'].append(label)
                mark = UPDATED
        elif sheet.get(row, DIFF) == GONE:
            report['重新出現'].append(label)
        else:
            unchanged += 1
        sheet.place(row, video)  # also fixes a name whose case changed
        if sheet.is_current(row, video):
            if mark:  # moved or renamed, same content
                sheet.mark(row, mark)
            elif sheet.get(row, DIFF) == GONE:  # back again; a 新增 or 更新 stays until you clear it
                sheet.set(row, DIFF, PRESENT)
            if sheet.get(row, WIDTH) is None:  # measured before 寬/高 existed: read just those, not the bitrate
                to_fill.append((row, video))
        else:
            to_probe.append((row, video, mark))
    for row in sorted(gone_rows):
        if sheet.get(row, DIFF) != GONE:
            sheet.set(row, DIFF, GONE)
            report['已不存在'].append(sheet.label(row))

    last_save = time.monotonic()

    def save_now_and_then():
        """Save if it's been a while; a failed save (e.g. the network dropped) keeps its changes for the next one."""
        nonlocal last_save
        if time.monotonic() - last_save > SAVE_EVERY_SECONDS:
            try:
                sheet.save()
            except Exception as error:  # carry on probing; Ctrl+C or the end of the run saves again
                print(f'存檔失敗，下次存檔時再試：{error}', flush=True)
            last_save = time.monotonic()

    try:
        for index, (row, video, mark) in enumerate(to_probe, 1):
            print(f'[{index}/{len(to_probe)}] {video.folder}/{video.name}.{video.extension}', flush=True)
            measured = measure(video.path)
            sheet.record(row or sheet.add(video), video, measured, mark)
            if measured is None:
                report['讀取失敗'].append(f'{video.folder}/{video.name}.{video.extension}')
            save_now_and_then()
        for index, (row, video) in enumerate(to_fill, 1):
            print(f'[解析度 {index}/{len(to_fill)}] {video.folder}/{video.name}.{video.extension}', flush=True)
            width, height = measure_dimensions(video.path)
            sheet.set(row, WIDTH, width)
            sheet.set(row, HEIGHT, height)
            save_now_and_then()
    except KeyboardInterrupt:
        sheet.save()
        sys.exit('中斷，已存下完成的部分；再跑一次會從沒做完的影片繼續')
    sheet.show_diff()
    sheet.save()

    print(f'掃描 {len(on_disk)} 部影片（{"、".join(sorted(scanned, key=list(folders).index)) or "無"}），'
          f'probe {len(to_probe)} 部，沒變 {unchanged} 部 → {sheet.url}')
    if to_fill:
        print(f'補上解析度 {len(to_fill)} 部')
    for kind, names in report.items():
        print(f'{kind} {len(names)} 部：')
        for name in names:
            print(f'  {name}')


if __name__ == '__main__':
    main()
