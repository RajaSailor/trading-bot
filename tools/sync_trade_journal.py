"""Download the private journal workbook and atomically sync it to a local .xlsx."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from openpyxl import load_workbook

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_journal import (
    HEADERS,
    HEADER_ROW,
    MAX_EXPORT_BYTES,
    REVIEW_COLUMNS,
    SHEET_NAMES,
    _file_lock,
    _text,
    bot_row_checksum,
)

MAX_BACKUPS = 10
REPLACE_ATTEMPTS = 6
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
EXPORT_HEADER = "X-Trade-Journal-Token"


def _validate_configuration(url, token, destination):
    parsed = urlsplit(str(url or ""))
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        raise ValueError("journal URL must use HTTPS (HTTP is allowed only for loopback tests)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("journal URL must not contain credentials, query strings or fragments")
    if not parsed.hostname:
        raise ValueError("journal URL must include a host")
    if parsed.path.rstrip("/") != "/trade-journal.xlsx":
        raise ValueError("journal URL must target /trade-journal.xlsx")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("TRADE_JOURNAL_TOKEN must contain at least 32 characters")
    target = Path(destination).expanduser()
    if target.suffix.lower() != ".xlsx":
        raise ValueError("destination must end in .xlsx; .xls files are never renamed or overwritten")
    if target.is_symlink():
        raise ValueError("destination must not be a symbolic link")
    return target


def _workbook_header(sheet):
    headers = [cell.value for cell in sheet[HEADER_ROW]]
    if tuple(headers) != HEADERS:
        raise ValueError(f"unexpected journal columns in {sheet.title}")
    return headers


def _verify_row_checksum(sheet, row):
    id_column = HEADERS.index("Row ID") + 1
    checksum_column = HEADERS.index("Bot Data SHA-256") + 1
    identifier = sheet.cell(row, id_column).value
    if identifier in (None, ""):
        return
    values = [sheet.cell(row, column).value for column in range(1, checksum_column)]
    if sheet.cell(row, checksum_column).value != bot_row_checksum(values):
        raise ValueError("bot-owned journal cells were edited or corrupted")


def _review_values(path):
    if not path.exists():
        return {}
    if path.is_symlink() or path.suffix.lower() != ".xlsx":
        raise ValueError("existing destination is not a safe .xlsx workbook")
    _validate_download(path)
    workbook = load_workbook(path, read_only=False, data_only=False)
    try:
        if tuple(workbook.sheetnames) != SHEET_NAMES:
            raise ValueError("existing workbook has unexpected worksheets")
        result = {}
        for sheet in workbook.worksheets:
            _workbook_header(sheet)
            id_col = HEADERS.index("Row ID") + 1
            notes_col, review_col = REVIEW_COLUMNS["Notes"], REVIEW_COLUMNS["Review"]
            sheet_values = {}
            for row in range(HEADER_ROW + 1, sheet.max_row + 1):
                identifier = sheet.cell(row, id_col).value
                if identifier in (None, ""):
                    if any(
                        sheet.cell(row, REVIEW_COLUMNS[name]).value not in (None, "")
                        for name in REVIEW_COLUMNS
                    ):
                        raise ValueError("review text has no stable row id")
                    continue
                _verify_row_checksum(sheet, row)
                if not isinstance(identifier, str):
                    raise ValueError("journal row identifier was edited or corrupted")
                notes = sheet.cell(row, notes_col)
                review = sheet.cell(row, review_col)
                if notes.data_type == "f" or review.data_type == "f":
                    raise ValueError("formula found in a user review field")
                if identifier in sheet_values:
                    raise ValueError("duplicate stable row id in existing workbook")
                values = []
                for cell in (notes, review):
                    if cell.value is not None and not isinstance(cell.value, str):
                        raise ValueError("review fields must contain text only")
                    values.append(_text(cell.value))
                sheet_values[identifier] = tuple(values)
            result[sheet.title] = sheet_values
        return result
    finally:
        workbook.close()


def _merge_review(source_path, target_path, reviews):
    workbook = load_workbook(source_path, read_only=False, data_only=False)
    try:
        if tuple(workbook.sheetnames) != SHEET_NAMES:
            raise ValueError("downloaded workbook has unexpected worksheets")
        for sheet in workbook.worksheets:
            _workbook_header(sheet)
            sheet_reviews = reviews.get(sheet.title, {})
            id_col = HEADERS.index("Row ID") + 1
            seen = set()
            for row in range(HEADER_ROW + 1, sheet.max_row + 1):
                identifier = sheet.cell(row, id_col).value
                if identifier in (None, ""):
                    continue
                _verify_row_checksum(sheet, row)
                if not isinstance(identifier, str) or identifier in seen:
                    raise ValueError("downloaded workbook has invalid stable row identifiers")
                seen.add(identifier)
                if identifier in sheet_reviews:
                    for column, value in zip(
                        (REVIEW_COLUMNS["Notes"], REVIEW_COLUMNS["Review"]),
                        sheet_reviews[identifier],
                    ):
                        cell = sheet.cell(row, column)
                        cell.value = value
                        cell.number_format = "@"
            dropped = [
                identifier for identifier, values in sheet_reviews.items()
                if identifier not in seen and any(values)
            ]
            if dropped:
                raise ValueError("updated journal would discard user review fields")
        workbook.save(source_path)
    finally:
        workbook.close()


def _validate_download(path):
    if path.stat().st_size > MAX_EXPORT_BYTES or not zipfile.is_zipfile(path):
        raise ValueError("download is not a valid journal XLSX or exceeds the size limit")
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("journal workbook exceeds the uncompressed size limit")
        if archive.testzip() is not None:
            raise ValueError("downloaded workbook failed integrity validation")
    workbook = load_workbook(path, read_only=False, data_only=False)
    try:
        if tuple(workbook.sheetnames) != SHEET_NAMES:
            raise ValueError("downloaded workbook has unexpected worksheets")
        for sheet in workbook.worksheets:
            _workbook_header(sheet)
            for row in range(HEADER_ROW + 1, sheet.max_row + 1):
                _verify_row_checksum(sheet, row)
    finally:
        workbook.close()


def _backup_existing(target):
    if not target.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = target.with_name(target.stem + "." + stamp + ".bak.xlsx")
    backup = base
    suffix = 1
    while backup.exists():
        backup = target.with_name(target.stem + "." + stamp + f".{suffix}.bak.xlsx")
        suffix += 1
    shutil.copy2(target, backup)


def _prune_backups(target):
    backups = sorted(
        target.parent.glob(target.stem + ".*.bak.xlsx"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for old in backups[MAX_BACKUPS:]:
        try:
            old.unlink()
        except OSError:
            continue


def sync_workbook(url, token, destination, *, session=requests, sleep=time.sleep):
    """Sync over HTTPS without exposing the token in a URL or deleting old data."""
    target = _validate_configuration(url, token, destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    with _file_lock(lock_path):
        fd, temp_name = tempfile.mkstemp(
            prefix=".trade-journal-download-", suffix=".xlsx", dir=str(target.parent)
        )
        os.close(fd)
        temp = Path(temp_name)
        try:
            response = session.get(
                url,
                headers={EXPORT_HEADER: token},
                timeout=(5, 30),
                stream=True,
                allow_redirects=False,
            )
            try:
                if response.status_code != 200:
                    raise RuntimeError(f"journal download failed with HTTP {response.status_code}")
                total = 0
                with open(temp, "wb") as output:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_EXPORT_BYTES:
                            raise ValueError("journal download exceeds the size limit")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                response.close()
            _validate_download(temp)
            reviews = _review_values(target)
            _merge_review(temp, target, reviews)
            _validate_download(temp)
            if target.exists():
                _backup_existing(target)
            for attempt in range(REPLACE_ATTEMPTS):
                try:
                    os.replace(temp, target)
                    _prune_backups(target)
                    return target
                except PermissionError:
                    if attempt + 1 == REPLACE_ATTEMPTS:
                        raise
                    sleep(0.25 * (attempt + 1))
            raise RuntimeError("journal replacement failed")
        finally:
            temp.unlink(missing_ok=True)


def main():
    try:
        sync_workbook(
            os.environ.get("TRADE_JOURNAL_URL"),
            os.environ.get("TRADE_JOURNAL_TOKEN"),
            os.environ.get("TRADE_JOURNAL_DESTINATION"),
        )
    except Exception as exc:
        print(f"Trade journal sync failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("Trade journal workbook synced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
