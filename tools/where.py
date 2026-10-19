#!/usr/bin/env python3
"""Locate a guest function: generated file, config entries, HLE handler. Also a symbol resolver for probe.py."""
import bisect
import re
import sys

from game import GENERATED as GEN, ROOT, TEMPLATE as CONFIG

CALL_LIST = ROOT / "deps/ps2recomp/ps2xRuntime/include/ps2_call_list.h"
HLE_SRC = ROOT / "deps/ps2recomp/ps2xRuntime/src"


def load_symbols():
    """Function map from the generated file names (<name>_0x<start>.cpp); End is the next start."""
    starts = []
    if GEN.is_dir():
        for f in GEN.glob("*_0x*.cpp"):
            m = re.fullmatch(r"(.+)_0x([0-9a-f]+)\.cpp", f.name)
            if m:
                starts.append((int(m[2], 16), m[1]))
    starts.sort()
    return [(s, starts[i + 1][0] if i + 1 < len(starts) else s + 4, n) for i, (s, n) in enumerate(starts)]


def symbol_at(address, rows=None):
    """Return (name, start) of the function containing address, or None."""
    rows = rows if rows is not None else load_symbols()
    i = bisect.bisect_right([r[0] for r in rows], address) - 1
    if i >= 0 and rows[i][0] <= address < max(rows[i][1], rows[i][0] + 4):
        return rows[i][2], rows[i][0]
    return None


def find(query, rows):
    """Resolve a hex address or name to [(name, start)]."""
    if re.fullmatch(r"(0x)?[0-9a-fA-F]{5,8}", query) and (query.startswith("0x") or re.fullmatch(r"[0-9a-fA-F]+", query)):
        hit = symbol_at(int(query, 16), rows)
        return [hit] if hit else []
    exact = [(n, s) for s, e, n in rows if n == query]
    if exact:
        return exact
    return [(n, s) for s, e, n in rows if n.startswith(query + "__") or n.startswith(query)][:5] or \
           [(n, s) for s, e, n in rows if query in n][:5]


def config_hits(name, start):
    if not CONFIG.is_file():
        return []
    hits, section = [], ""
    pat = re.compile(r'"%s"|0x0*%x\b' % (re.escape(name), start), re.I)
    for no, line in enumerate(CONFIG.read_text(errors="replace").splitlines(), 1):
        m = re.match(r"^(\[[^\]]+\]|[a-z_]+)\s*=?\s*\[?\s*$", line)
        if m:
            section = m.group(1)
        if pat.search(line):
            hits.append(f"config/{CONFIG.name}:{no} [{section}] {line.strip()[:70]}")
    return hits[:3]


def hle_hits(name):
    base = name.split("__")[0]
    if not CALL_LIST.is_file():
        return []
    names = re.findall(r"\bX\((\w+)\)", CALL_LIST.read_text(errors="replace"))
    base = resolve_name(base, names)
    if base:
        pat = re.compile(r"\bvoid\s+%s\s*\(" % re.escape(base))
        for p in sorted(HLE_SRC.rglob("*.cpp")):
            for no, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if pat.search(line):
                    return [f"HLE {p.relative_to(ROOT)}:{no} ({base})"]
        return [f"HLE bound in ps2_call_list.h ({base}); handler not found"]
    return []


def resolve_name(name, names):
    if name in names:
        return name
    if name.startswith("_"):
        return name[1:] if name[1:] in names else ""
    return next((n for n in names if n == "_" + name), "")


def configured_stubs():
    configured = re.search(r"(?ms)^stubs\s*=\s*\[(.*?)\]", CONFIG.read_text()).group(1)
    return re.findall(r'"([^"\n]+)"', configured)


def unimplemented():
    """Count configured direct TODO handlers, including the runtime's underscore aliases."""
    if not CONFIG.is_file() or not CALL_LIST.is_file():
        return 2, ["config or runtime missing -> ./rx setup"]
    names = [selector.split("@", 1)[0] for selector in configured_stubs()]
    handlers = re.findall(r"\bX\((\w+)\)", CALL_LIST.read_text())
    todo = {}
    for path in sorted((HLE_SRC / "lib/Kernel").rglob("*.cpp")):
        for match in re.finditer(r"\bvoid\s+(\w+)\([^)]*\)\s*\{\s*TODO_NAMED\(", path.read_text()):
            todo[match[1]] = path.relative_to(ROOT)
    found = [(name, resolve_name(name, handlers)) for name in names]
    found = [(name, handler) for name, handler in found if handler in todo]
    aliases = sum(name != handler for name, handler in found)
    return 0, [f"{len(found)} configured unimplemented HLE names ({len(found) - aliases} exact, {aliases} underscore aliases)"] + [
        f"  {name} -> {handler} ({todo[handler]})" for name, handler in found]


def where(query):
    rows = load_symbols()
    if not rows:
        return None, ["generated/ missing (no function map yet): ./rx build"]
    found = find(query, rows)
    if not found:
        return 1, [f"no symbol or function contains {query}"]
    out = []
    for name, start in found[:3]:
        out.append(f"{name} @ 0x{start:x}")
        files = sorted(GEN.glob(f"*_0x{start:x}.cpp")) if GEN.is_dir() else []
        out += [f"  gen {f.relative_to(ROOT)}" for f in files] or ["  gen (none: generated/ missing or function skipped)"]
        out += ["  " + h for h in config_hits(name, start)]
        out += ["  " + h for h in hle_hits(name)] or ["  HLE: not bound (recompiled guest code)"]
    return 0, out[:10]


if __name__ == "__main__":
    code, lines = where(sys.argv[1]) if len(sys.argv) > 1 else (2, ["usage: where.py <0xADDR|name>"])
    print("\n".join(lines))
    sys.exit(code or 0)
