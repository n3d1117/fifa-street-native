#!/usr/bin/env python3
"""Verify and extract your own disc (identity: config/game.json).

  tools/disc.py <iso>               verify SHA-1s, extract to extracted/disc
  tools/disc.py <iso> --dest DIR    same, into DIR

Exit codes: 0 ok, 1 verification failed, 2 missing prerequisite.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
from pathlib import Path, PurePosixPath

from game import DISC, ELF, GAME, ROOT

STAMP = ROOT / "build/disc.stamp"


class Fail(Exception):
    """Verification failure (exit 1)."""


class Missing(Exception):
    """Missing prerequisite (exit 2)."""


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(iso_path: str) -> Path:
    """Normalize ISO path and strip ISO9660 ;version suffixes safely."""
    parts = []
    for component in PurePosixPath(iso_path).parts:
        if component in ("/", "", "."):
            continue
        if component == ".." or "/" in component or "\\" in component:
            raise ValueError(f"unsafe ISO path component: {component!r}")
        component = re.sub(r";[0-9]+$", "", component)
        if component in ("", ".", ".."):
            raise ValueError(f"unsafe normalized ISO path: {iso_path!r}")
        parts.append(component)
    if not parts:
        raise ValueError(f"not a file path: {iso_path!r}")
    return Path(*parts)


def read_boot_elf(iso) -> bytes:
    buf = io.BytesIO()
    iso.get_file_from_iso_fp(buf, iso_path=f"/{GAME['elf']};1")
    return buf.getvalue()


LBN_MAP = ".lbn-map"
LBN_GAPS = ".lbn-gaps.bin"
SECTOR = 2048


def write_lbn_map(iso_path: Path, dest: Path, records, volume_sectors: int) -> None:
    """The game reads by disc LBN. Record every file's extent, plus the non-zero sectors no file covers
    (volume descriptors, path tables, directory records) in a blob, so any LBN read is servable from
    the extraction. Format, one line each: `volume <sectors>`, `file <lbn> <sectors> <relative path>`,
    `gap <lbn> <sectors> <blob offset>`. Sectors outside both read as zero."""
    extents = sorted((r["lbn"], (r["size_bytes"] + SECTOR - 1) // SECTOR, r["relative_path"]) for r in records)
    lines = [f"volume {volume_sectors}"]
    lines += [f"file {lbn} {count} {path}" for lbn, count, path in extents]
    blob = bytearray()
    zero = bytes(SECTOR)
    with iso_path.open("rb") as image:
        cursor = 0
        spans = [(lbn, lbn + count) for lbn, count, _ in extents] + [(volume_sectors, volume_sectors)]
        for start, end in spans:
            run_start = None
            for lbn in range(cursor, start):
                image.seek(lbn * SECTOR)
                data = image.read(SECTOR)
                if data and data != zero:
                    if run_start is None:
                        run_start, run_offset = lbn, len(blob)
                    blob += data.ljust(SECTOR, b"\0")
                elif run_start is not None:
                    lines.append(f"gap {run_start} {lbn - run_start} {run_offset}")
                    run_start = None
            if run_start is not None:
                lines.append(f"gap {run_start} {start - run_start} {run_offset}")
            cursor = max(cursor, end)
    (dest / LBN_MAP).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (dest / LBN_GAPS).write_bytes(bytes(blob))
    print(f"lbn map: {len(extents)} files, {len(blob) // SECTOR} non-file sectors")


def extract(iso_path: Path, dest: Path) -> None:
    import pycdlib
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    try:
        got = hashlib.sha1(read_boot_elf(iso)).hexdigest()
        if got != GAME["elf_sha1"]:
            raise Fail(f"{GAME['elf']} SHA-1 mismatch: got {got}, expected {GAME['elf_sha1']}; use the supported PAL disc and rerun ./rx disc <path-to-iso>")
        records, seen = [], set()
        for dirpath, _dirs, files in iso.walk(iso_path="/"):
            for filename in files:
                source = (dirpath.rstrip("/") + "/" + filename) if dirpath != "/" else "/" + filename
                relative = safe_relative(source)
                key = relative.as_posix().casefold()
                if key in seen:
                    raise Fail(f"duplicate path after stripping ISO version suffix: {relative}")
                seen.add(key)
                record = iso.get_record(iso_path=source)
                records.append({"iso_path": source, "relative_path": relative.as_posix(),
                                "size_bytes": record.data_length, "lbn": record.extent_location()})
        records.sort(key=lambda r: r["relative_path"].casefold())
        dest.mkdir(parents=True, exist_ok=True)
        for record in records:
            target = (dest / record["relative_path"]).resolve()
            if dest != target and dest not in target.parents:
                raise Fail(f"path escaped extraction root: {record['relative_path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not target.is_file() or target.stat().st_size != record["size_bytes"]:
                    raise Fail(f"existing extraction path conflicts or has wrong size: {target}")
                continue
            iso.get_file_from_iso(local_path=str(target), iso_path=record["iso_path"])
        write_lbn_map(iso_path, dest, records, iso.pvd.space_size)
        print(f"extracted {len(records)} files to {dest}")
    finally:
        iso.close()


def stamp_key(iso_path: Path) -> str:
    st = iso_path.stat()
    return hashlib.sha256(f"{iso_path}:{st.st_size}:{st.st_mtime_ns}:{GAME['elf_sha1']}".encode()
                          + Path(__file__).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("iso", type=Path, help="path to your own disc image")
    parser.add_argument("--dest", type=Path, default=DISC)
    args = parser.parse_args()
    try:
        iso_path = args.iso.expanduser().resolve()
        if not iso_path.is_file():
            raise Missing(f"ISO not found: {iso_path} -> ./rx disc <path-to-iso>")
        try:
            import pycdlib  # noqa: F401
        except ImportError:
            raise Missing("pycdlib not installed -> ./rx setup")
        dest = args.dest.expanduser().resolve()
        default_dest = dest == DISC.resolve()
        if default_dest and ELF.is_file() and STAMP.is_file() and STAMP.read_text().strip() == stamp_key(iso_path):
            print("disc up to date: extracted/ matches this ISO (delete build/disc.stamp to force)")
            return 0
        if file_digest(iso_path, "sha1") != GAME["iso_sha1"]:
            raise Fail(f"ISO SHA-1 mismatch for {iso_path.name}; expected {GAME['title']} ({GAME['id']}), {GAME['iso_sha1']}")
        extract(iso_path, dest)
        if default_dest:
            STAMP.parent.mkdir(parents=True, exist_ok=True)
            STAMP.write_text(stamp_key(iso_path) + "\n")
    except Missing as exc:
        print(exc, file=sys.stderr)
        return 2
    except Fail as exc:
        print(f"disc failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
