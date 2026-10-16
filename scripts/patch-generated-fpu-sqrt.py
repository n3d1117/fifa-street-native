#!/usr/bin/env python3
"""Audit or repair legacy generated COP1 SQRT.S translations.

The scan is intentionally limited to generated/SLES-50350-symbolmap-entry.
Without --apply, it only prints a JSON dry-run report. Applying creates a
source-preserving backup tree before changing any generated C++ file.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "generated/SLES-50350-symbolmap-entry"
BACKUP = ROOT / "reports/codegen-backups/sqrt"
COMMENT = re.compile(r"^\s*//\s*(0x[0-9a-fA-F]+):\s*(0x[0-9a-fA-F]+)\b")
LEGACY = re.compile(
    r"ctx->f\[(\d+)\]\s*=\s*FPU_SQRT_S\(ctx->f\[(\d+)\]\);"
)


def decode(word: int) -> dict[str, int] | None:
    if ((word >> 26) & 0x3F) != 0x11:
        return None
    if ((word >> 21) & 0x1F) != 0x10 or (word & 0x3F) != 0x04:
        return None
    return {
        "ft": (word >> 16) & 0x1F,
        "fs": (word >> 11) & 0x1F,
        "fd": (word >> 6) & 0x1F,
    }


def canonical(fd: int, ft: int) -> str:
    return (
        f"{{ const float sqrtFt = ctx->f[{ft}]; uint32_t sqrtFtBits; "
        "std::memcpy(&sqrtFtBits, &sqrtFt, sizeof(sqrtFtBits)); "
        f"ctx->f[{fd}] = ((sqrtFtBits & 0x7fffffffu) == 0) ? "
        "((sqrtFtBits & 0x80000000u) ? -0.0f : 0.0f) : "
        "FPU_SQRT_S(fabsf(sqrtFt)); }"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="back up and patch candidates")
    args = parser.parse_args()

    report: list[dict[str, object]] = []
    replacements: dict[Path, str] = {}
    for path in sorted(ACTIVE.glob("*.cpp")):
        original = path.read_text()
        lines = original.splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            match = COMMENT.match(line)
            if not match:
                continue
            pc, word_hex = match.groups()
            operands = decode(int(word_hex, 16))
            if operands is None:
                continue
            # Generated code may put a ctx->pc assignment between the decoded
            # instruction comment and its emitted statement. Stop at the next
            # instruction comment to avoid associating a later instruction.
            for j in range(i + 1, min(i + 5, len(lines))):
                if j > i + 1 and COMMENT.match(lines[j]):
                    break
                old = LEGACY.search(lines[j])
                if old is None:
                    continue
                old_fd, old_src = map(int, old.groups())
                # Only repair the expected legacy lowering: destination must
                # match decoded Fd and old source must be decoded Fs.
                if old_fd != operands["fd"] or old_src != operands["fs"]:
                    report.append({
                        "file": str(path.relative_to(ROOT)), "pc": pc,
                        "word": word_hex, **operands, "status": "unexpected-lowering",
                        "generated_fd": old_fd, "generated_source": old_src,
                    })
                    break
                report.append({
                    "file": str(path.relative_to(ROOT)), "pc": pc,
                    "word": word_hex, **operands,
                    "old_operand": f"Fs=f{old_src}",
                    "new_operand": f"Ft=f{operands['ft']}",
                    "status": "patch" if args.apply else "candidate",
                })
                lines[j] = lines[j][:old.start()] + canonical(operands["fd"], operands["ft"]) + lines[j][old.end():]
                changed = True
                break
        if changed:
            replacements[path] = "".join(lines)

    summary = {
        "scope": str(ACTIVE.relative_to(ROOT)),
        "mode": "apply" if args.apply else "dry-run",
        "candidate_count": sum(item["status"] in ("candidate", "patch") for item in report),
        "operand_mismatch_count": sum(item.get("fs") != item.get("ft") for item in report
                                        if item["status"] in ("candidate", "patch")),
        "unexpected_count": sum(item["status"] == "unexpected-lowering" for item in report),
        "sites": report,
    }
    if args.apply:
        BACKUP.mkdir(parents=True, exist_ok=True)
        for path, replacement in replacements.items():
            rel = path.relative_to(ACTIVE)
            backup = BACKUP / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(replacement)
        summary["backup_dir"] = str(BACKUP.relative_to(ROOT))
        summary["patched_file_count"] = len(replacements)
    print(json.dumps(summary, indent=2))
    return 1 if summary["unexpected_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
