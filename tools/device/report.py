"""Render the device experiment ledger as a self-contained HTML fragment."""
import argparse
import datetime as dt
import html
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
AB_DIR = ROOT / "build/device-ab"


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def read_ledger(path):
    rows = []
    try:
        for line in path.read_text().splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def esc(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def number(value, places=2):
    return "—" if not isinstance(value, (int, float)) or not math.isfinite(value) else f"{value:.{places}f}"


def entry_metrics(entry, metric, arm="candidate"):
    metrics = (entry or {}).get("metrics") or {}
    value = metrics.get(metric)
    if isinstance(value, dict):
        return value.get(arm)
    return value


def by_note(entry, notes):
    label = entry.get("label", "")
    for note in notes:
        if note.get("ab_label") == label or note.get("id") == label:
            return note
    return {"id": label, "title": label or "Unlabelled run", "category": "—",
            "status": "baseline" if entry.get("kind") == "baseline" else "queued"}


def chip(status):
    cls = status if status in {"kept", "rejected", "running", "queued", "baseline"} else "queued"
    return f'<span class="pill {cls}">{esc(status or "queued")}</span>'


def delta_state(metric, value, noise):
    if not isinstance(value, (int, float)) or not isinstance(noise, (int, float)) or abs(value) <= noise:
        return "quiet"
    higher_better = metric == "fps"
    return "good" if (value > 0 if higher_better else value < 0) else "bad"


def metric_delta(entry, metric):
    row = (entry.get("metrics") or {}).get(metric)
    if isinstance(row, dict):
        return row.get("delta"), row.get("control_noise")
    return None, None


def svg_chart(points, metric, label, unit, target=None):
    width, height = 1040, 190
    left, right, top, bottom = 52, 18, 18, 34
    vals = [p[metric] for p in points if isinstance(p.get(metric), (int, float))]
    if target is not None:
        vals.append(target)
    if not vals:
        return f'<div class="chart-empty">No {esc(label.lower())} measurements yet.</div>'
    low, high = min(vals), max(vals)
    pad = max((high - low) * .14, 1.0 if metric == "fps" else .5)
    low, high = max(0, low - pad), high + pad
    if high <= low:
        high = low + 1
    x = lambda i: left + (width - left - right) * (i / max(1, len(points) - 1))
    y = lambda v: top + (height - top - bottom) * ((high - v) / (high - low))
    parts = [f'<svg class="plot" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)} by experiment order">']
    for i in range(4):
        val = low + (high - low) * i / 3
        yy = y(val)
        parts.append(f'<line class="gridline" x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="tick" x="{left-8}" y="{yy+4:.1f}" text-anchor="end">{number(val, 0 if metric == "fps" else 1)}</text>')
    if target is not None and low <= target <= high:
        yy = y(target)
        parts.append(f'<line class="target" x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="target-label" x="{width-right-2}" y="{yy-5:.1f}" text-anchor="end">60 fps target</text>')
    valid = [(i, p) for i, p in enumerate(points) if isinstance(p.get(metric), (int, float))]
    if len(valid) > 1:
        coords = " ".join(f'{x(i):.1f},{y(p[metric]):.1f}' for i, p in valid)
        parts.append(f'<polyline class="series" points="{coords}"/>')
    for i, point in valid:
        status = point.get("status", "queued")
        cls = status if status in {"kept", "rejected", "running", "queued", "baseline"} else "queued"
        xx, yy = x(i), y(point[metric])
        parts.append(f'<circle class="dot {cls}" cx="{xx:.1f}" cy="{yy:.1f}" r="5"><title>{esc(point.get("title"))}: {number(point[metric])} {esc(unit)}</title></circle>')
        parts.append(f'<text class="tick" x="{xx:.1f}" y="{height-10}" text-anchor="middle">{i+1}</text>')
    parts.append('</svg>')
    return "".join(parts)


def render(ledger, notes, backlog, updated=None):
    notes = notes if isinstance(notes, list) else []
    entries = sorted(ledger, key=lambda e: e.get("ts", ""))
    enriched = [(entry, by_note(entry, notes)) for entry in entries]
    matched = {e.get("label") for e in entries}
    for note in notes:
        # Queued notes are backlog candidates, not experiments that ran.
        if note.get("status") == "queued" or not (note.get("ab_label") or note.get("id")):
            continue
        if note.get("ab_label") in matched or note.get("id") in matched:
            continue
        enriched.append(({"label": note.get("id"), "kind": "note"}, note))
    baseline = next((e for e in entries if e.get("kind") == "baseline"), None)
    best = max((e for e, n in enriched if n.get("status") == "kept" or e.get("kind") == "baseline"),
               key=lambda e: entry_metrics(e, "fps") or float("-inf"), default=None)
    score_specs = [
        ("FPS", "fps", "fps", "game fps"),
        ("Late frames >18 ms", "late_frames_over_18_pct", "%", "late %"),
        ("Present p99", "present_dt_ms_p99", "ms", "present p99"),
        ("GameThread", "gamethread_cores", "cores", "GameThread cores"),
        ("Total CPU", "process_cores", "cores", "total CPU cores"),
        ("GPU p50", "gpu_ms_p50", "ms", "GPU ms p50"),
        ("Audio underruns", "audio_underruns_delta_gameplay", "", "audio underruns"),
    ]
    cards = []
    for title, key, suffix, _ in score_specs:
        b = entry_metrics(baseline, key) if baseline else None
        c = entry_metrics(best, key) if best else None
        delta = c - b if isinstance(c, (int, float)) and isinstance(b, (int, float)) else None
        state = delta_state(key, delta, 0) if delta is not None else "quiet"
        sign = "+" if isinstance(delta, (int, float)) and delta > 0 else ""
        cards.append(f'<div class="score"><span class="score-label">{esc(title)}</span><strong>{number(c)}{(" " + esc(suffix)) if suffix else ""}</strong><span class="score-compare">base {number(b)} · Δ {sign}{number(delta)}</span><span class="state {state}">{"better" if state == "good" else "worse" if state == "bad" else "within noise" if delta is not None else "no data"}</span></div>')

    points = []
    for entry, note in enriched:
        if entry.get("kind") == "baseline" or note.get("status") == "kept":
            points.append({"title": note.get("title", entry.get("label")), "status": note.get("status", "baseline"),
                           "fps": entry_metrics(entry, "fps"), "late": entry_metrics(entry, "late_frames_over_18_pct")})
    rows = []
    for idx, (entry, note) in enumerate(enriched, 1):
        status = note.get("status", "queued")
        vals = [("fps", "FPS", "fps"), ("late_frames_over_18_pct", "Late", "%"),
                ("cpu_game_ms_p50", "CPU p50", "ms"), ("gamethread_cores", "GT", "cores"),
                ("gpu_ms_p50", "GPU", "ms")]
        changes = []
        for key, label, unit in vals:
            delta, noise = metric_delta(entry, key)
            state = delta_state(key, delta, noise)
            text = f'{label} {number(delta)}{(" " + unit) if unit else ""}' if delta is not None else f'{label} —'
            changes.append(f'<span class="delta {state}">{esc(text)}</span>')
        tr_id = f"detail-{idx}"
        fields = []
        for metric, title in [("fps", "FPS"), ("late_frames_over_18_pct", "Late frames >18 ms (%)"),
                              ("present_dt_ms_p50", "Present p50 (ms)"), ("present_dt_ms_p99", "Present p99 (ms)"),
                              ("cpu_game_ms_p50", "Game CPU p50 (ms)"), ("cpu_game_ms_p95", "Game CPU p95 (ms)"),
                              ("gamethread_cores", "GameThread cores"), ("process_cores", "Total CPU cores"),
                              ("gpu_ms_p50", "GPU p50 (ms)"), ("gpu_ms_p95", "GPU p95 (ms)"),
                              ("audio_underruns_delta_gameplay", "Audio underruns")]:
            metric_row = (entry.get("metrics") or {}).get(metric)
            if isinstance(metric_row, dict):
                c, n, d, pct, noise = (metric_row.get(k) for k in ("control", "candidate", "delta", "delta_pct", "control_noise"))
            else:
                c, n, d, pct, noise = None, metric_row, None, None, None
            fields.append(f'<tr><th>{esc(title)}</th><td>{number(c)}</td><td>{number(n)}</td><td>{number(d)}</td><td>{(number(pct)+"%") if pct is not None else "—"}</td><td>{number(noise)}</td></tr>')
        env = json.dumps({"control": entry.get("control"), "candidate": entry.get("candidate")}, sort_keys=True)
        dirs = " · ".join(entry.get("run_dirs") or []) or "—"
        detail = (f'<tr class="detail" id="{tr_id}" hidden><td colspan="7"><div class="detail-grid">'
                  f'<p><b>Hypothesis</b><br>{esc(note.get("hypothesis"))}</p><p><b>Summary</b><br>{esc(note.get("summary"))}</p>'
                  f'<p><b>Graphics</b><br>{esc(note.get("graphics"))}</p><p><b>Environment</b><br><code>{esc(env)}</code></p>'
                  f'<p><b>Run directories</b><br><code>{esc(dirs)}</code></p></div>'
                  f'<div class="table-scroll"><table class="metric-table"><thead><tr><th>Metric</th><th>Control</th><th>Candidate</th><th>Delta</th><th>Delta %</th><th>Noise</th></tr></thead><tbody>{"".join(fields)}</tbody></table></div></td></tr>')
        sha = (entry.get("git_sha") or note.get("commit") or "—")[:8]
        rows.append(f'<tr class="experiment" data-status="{esc(status)}"><td><button class="expand" type="button" aria-expanded="false" aria-controls="{tr_id}">＋</button> {idx}</td><td>{esc(note.get("title", entry.get("label")))}</td><td>{esc(note.get("category"))}</td><td>{chip(status)}</td><td class="deltas">{"".join(changes)}</td><td><code>{esc(sha)}</code></td></tr>{detail}')

    ladder = []
    for note in notes:
        if note.get("category") != "quality-ladder":
            continue
        match = next((e for e in reversed(entries) if e.get("label") in (note.get("ab_label"), note.get("id"))), None)
        ladder.append(f'<tr><th>{esc(note.get("title"))}<small>{esc(note.get("graphics"))}</small></th><td>{number(entry_metrics(match, "fps"))}</td><td>{number(entry_metrics(match, "late_frames_over_18_pct"))}</td><td>{number(entry_metrics(match, "gpu_ms_p50"))}</td><td>{chip(note.get("status", "queued"))}</td></tr>')
    if not ladder:
        ladder.append('<tr><td colspan="5" class="empty">No quality ladder entries in notes.</td></tr>')

    items = backlog.get("items", []) if isinstance(backlog, dict) else backlog if isinstance(backlog, list) else []
    note_status = {n.get("id"): n.get("status") for n in notes}
    backlog_rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        expected = item.get("expected_win") or {}
        backlog_rows.append(f'<tr><td>{esc(item.get("priority"))}</td><th>{esc(item.get("id"))}<small>{esc(item.get("title"))}</small></th><td>{esc(item.get("category"))}</td><td>{esc(expected.get("metric", "—"))} · {esc(expected.get("level", "—"))}</td><td>{esc(item.get("effort"))}</td><td>{esc(item.get("risk"))}</td><td>{chip(note_status.get(item.get("id"), "untested"))}</td></tr>')
    if not backlog_rows:
        backlog_rows.append('<tr><td colspan="7" class="empty">No research backlog available.</td></tr>')

    device_bits = []
    latest = entries[-1] if entries else {}
    for key in ("device", "ios", "ios_version"):
        value = latest.get(key)
        if value:
            device_bits.append(str(value))
    device_line = " · ".join(device_bits) if device_bits else "Device and iOS details not present in ledger"
    stamp = updated or dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return f'''<title>FIFA iPhone Perf Lab</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=JetBrains+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600;700&display=swap');
:root {{ --bg:#f2f4f6; --surface:#ffffff; --fg:#132033; --muted:#647287; --rule:#cbd3dd; --accent:#ad6a00; --accent-soft:#f3e4c8; --good:#167451; --good-soft:#dcefe7; --warn:#916000; --warn-soft:#f3e8ce; --bad:#b23b46; --bad-soft:#f5dfe1; --plot:#75869a; --target:#ad6a00; --no-fill:none; color-scheme:light; }}
:root[data-theme="dark"] {{ --bg:#091321; --surface:#101e2e; --fg:#e7edf4; --muted:#9baabd; --rule:#2b3b4d; --accent:#efb84f; --accent-soft:#332817; --good:#62c99b; --good-soft:#12362a; --warn:#e6b958; --warn-soft:#392f1a; --bad:#f07880; --bad-soft:#3a2026; --plot:#8ca0b6; --target:#efb84f; --no-fill:none; color-scheme:dark; }}
* {{ box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--fg); margin:0; font:16px/1.45 'Source Sans 3',sans-serif; }}
.lab {{ max-width:1140px; margin:0 auto; padding-block:32px 48px; padding-left:max(16px,env(safe-area-inset-left,0px)); padding-right:max(16px,env(safe-area-inset-right,0px)); }}
h1,h2,.big {{ font-family:'Barlow Condensed','Source Sans 3',sans-serif; letter-spacing:.02em; }}
h1 {{ font-size:clamp(2rem,6vw,3rem); line-height:1; margin:0; }}
h2 {{ font-size:1.5rem; margin:0; }}
.masthead {{ border-bottom:2px solid var(--fg); padding:12px 0 22px; display:flex; justify-content:space-between; gap:20px; align-items:end; }}
.purpose {{ margin:9px 0 0; color:var(--muted); }}
.updated {{ color:var(--muted); text-align:right; font:12px 'JetBrains Mono',monospace; }}
.device {{ display:block; margin-top:5px; font:12px 'Source Sans 3',sans-serif; }}
section {{ margin-top:32px; min-width:0; }}
.section-head {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; padding-bottom:10px; border-bottom:1px solid var(--rule); margin-bottom:14px; }}
.scoreboard {{ display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); border-bottom:1px solid var(--rule); }}
.score {{ min-width:0; padding:13px 12px 16px; border-left:1px solid var(--rule); display:flex; flex-direction:column; gap:3px; }}
.score:first-child {{ border-left:0; padding-left:0; }}
.score-label,.score-compare {{ color:var(--muted); font-size:12px; }}
.score strong {{ font:600 clamp(1.3rem,3vw,2rem)/1.15 'Barlow Condensed',sans-serif; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }}
.score-compare {{ font:10px 'JetBrains Mono',monospace; }}
.state,.pill {{ align-self:flex-start; padding:2px 7px; border:1px solid var(--muted); border-radius:2px; font:600 10px 'Source Sans 3',sans-serif; text-transform:uppercase; letter-spacing:.06em; }}
.good,.kept {{ color:var(--good); }} .bad,.rejected {{ color:var(--bad); }} .quiet,.queued {{ color:var(--muted); }} .running,.baseline {{ color:var(--accent); }}
.plot {{ width:100%; height:auto; display:block; overflow:visible; }}
.gridline {{ stroke:var(--rule); stroke-width:1; }} .target {{ stroke:var(--target); stroke-width:1.5; stroke-dasharray:5 4; }}
.series {{ fill:var(--no-fill); stroke:var(--plot); stroke-width:2; }} .tick,.target-label {{ fill:var(--muted); font:11px 'JetBrains Mono',monospace; }} .target-label {{ fill:var(--target); }}
.dot {{ stroke:var(--bg); stroke-width:2; }} .dot.kept {{ fill:var(--good); }} .dot.baseline {{ fill:var(--accent); }} .dot.rejected {{ fill:var(--bad); }} .dot.running {{ fill:var(--accent); }} .dot.queued {{ fill:var(--muted); }}
.chart-label {{ color:var(--muted); font-size:12px; margin:8px 0 0; }} .chart-empty,.empty {{ color:var(--muted); padding:18px 0; }}
.legend {{ color:var(--muted); font-size:12px; }}
.toolbar {{ display:flex; align-items:center; gap:8px; color:var(--muted); }}
select {{ background:var(--surface); color:var(--fg); border:1px solid var(--rule); padding:5px 8px; font:inherit; }}
.table-scroll {{ width:100%; overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; text-align:left; }}
th,td {{ border-bottom:1px solid var(--rule); padding:9px 8px; vertical-align:top; }}
thead th {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.07em; }}
tbody th {{ font-weight:600; }}
small {{ display:block; color:var(--muted); font-weight:400; }}
.log-table {{ min-width:850px; }} .log-table td:nth-child(2) {{ min-width:190px; }}
.deltas {{ white-space:nowrap; }} .delta {{ display:inline-block; margin:0 7px 3px 0; font:11px 'JetBrains Mono',monospace; }}
code {{ font:11px 'JetBrains Mono',monospace; overflow-wrap:anywhere; }}
.expand {{ background:var(--surface); border:1px solid var(--rule); color:var(--fg); width:25px; height:25px; cursor:pointer; font:16px 'Source Sans 3',sans-serif; }}
.detail td {{ background:var(--surface); padding:16px; }} .detail-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px 18px; }} .detail-grid p {{ margin:0 0 12px; min-width:0; }}
.metric-table {{ min-width:590px; font:12px 'JetBrains Mono',monospace; font-variant-numeric:tabular-nums; }} .metric-table th {{ font-family:'Source Sans 3',sans-serif; }}
.compact {{ min-width:650px; }}
.method {{ color:var(--muted); max-width:780px; }}
button:focus-visible,select:focus-visible {{ outline:2px solid var(--accent); outline-offset:3px; }}
@media (max-width:760px) {{ .scoreboard {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .score:nth-child(odd) {{ border-left:0; }} .score:first-child {{ padding-left:12px; }} .masthead {{ align-items:start; flex-direction:column; }} .updated {{ text-align:left; }} .detail-grid {{ grid-template-columns:1fr; }} }}
@media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ scroll-behavior:auto !important; animation-duration:0.01ms !important; transition-duration:0.01ms !important; }} }}
</style>
<main class="lab">
<header class="masthead"><div><h1>FIFA iPhone Perf Lab</h1><p class="purpose">A measured field log for gameplay pacing, CPU headroom, and render cost.</p></div><div class="updated">Updated {esc(stamp)}<span class="device">{esc(device_line)}</span></div></header>
<section aria-label="Performance scoreboard"><div class="section-head"><h2>Scoreboard</h2><span class="legend">Baseline vs current best{f' · {esc(best.get("label"))}' if best else ''}</span></div><div class="scoreboard">{"".join(cards)}</div></section>
<section><div class="section-head"><h2>Progress</h2><span class="legend">Experiment order · kept and baseline entries</span></div><p class="chart-label">Gameplay frame rate · fps</p>{svg_chart(points, "fps", "Gameplay frame rate", "fps", 60)}<p class="chart-label">Late frames over 18 ms · percent</p>{svg_chart(points, "late", "Late frames over 18 ms", "%")}</section>
<section><div class="section-head"><h2>Experiment log</h2><label class="toolbar" for="status-filter">Filter <select id="status-filter"><option value="all">All statuses</option><option>kept</option><option>rejected</option><option>running</option><option>queued</option><option>baseline</option></select></label></div><div class="table-scroll"><table class="log-table"><thead><tr><th>#</th><th>Experiment</th><th>Category</th><th>Status</th><th>Key deltas</th><th>Commit</th></tr></thead><tbody>{''.join(rows) if rows else '<tr><td colspan="6" class="empty">No experiment runs recorded yet.</td></tr>'}</tbody></table></div></section>
<section><div class="section-head"><h2>Graphics ladder</h2><span class="legend">Quality setting vs measured cost</span></div><div class="table-scroll"><table class="compact"><thead><tr><th>Setting</th><th>FPS</th><th>Late %</th><th>GPU ms</th><th>Verdict</th></tr></thead><tbody>{''.join(ladder)}</tbody></table></div></section>
<section><div class="section-head"><h2>Research backlog</h2><span class="legend">Ranked hypotheses · wins are expected, not measured</span></div><div class="table-scroll"><table class="compact"><thead><tr><th>Priority</th><th>Research</th><th>Category</th><th>Expected win</th><th>Effort</th><th>Risk</th><th>Status</th></tr></thead><tbody>{''.join(backlog_rows)}</tbody></table></div></section>
<section><div class="section-head"><h2>Method</h2></div><p class="method">Runs use ABBA control/candidate pairs over 120-second confirmed gameplay windows, with cooldown between runs. Baselines repeat the same window without a candidate. A change counts only when its delta exceeds the spread among control runs; smaller differences are within control noise. Measure gameplay FPS on-device and keep guest video timing unchanged.</p></section>
</main>
<script>
document.querySelectorAll('.expand').forEach(button => button.addEventListener('click', () => {{ const row = document.getElementById(button.getAttribute('aria-controls')); const open = button.getAttribute('aria-expanded') === 'true'; button.setAttribute('aria-expanded', String(!open)); button.textContent = open ? '＋' : '−'; row.hidden = open; }}));
document.getElementById('status-filter').addEventListener('change', event => {{ document.querySelectorAll('.experiment').forEach(row => {{ row.hidden = event.target.value !== 'all' && row.dataset.status !== event.target.value; const detail = document.getElementById(row.nextElementSibling.id); if (row.hidden && detail) detail.hidden = true; }}); }});
</script>'''


def page(content):
    """Wrap a report fragment as a complete standalone HTML page."""
    cut = content.find("<main")
    if cut < 0:
        raise ValueError("content has no '<main' element; cannot build a full HTML page")
    head_part, body_part = content[:cut], content[cut:]
    return ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            + head_part + "</head>\n<body>\n" + body_part + "\n</body></html>\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(AB_DIR / "report.html"), help="output HTML fragment path")
    parser.add_argument("--page", default=None, help="also write a complete standalone HTML page to PATH")
    args = parser.parse_args(argv)
    notes_path = AB_DIR / "notes.json"
    backlog_path = AB_DIR / "backlog.json"
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    content = render(read_ledger(AB_DIR / "ledger.jsonl"), read_json(notes_path, []), read_json(backlog_path, {}))
    out.write_text(content + "\n")
    print(out)
    if args.page:
        page_out = pathlib.Path(args.page)
        if not page_out.is_absolute():
            page_out = ROOT / page_out
        page_out.parent.mkdir(parents=True, exist_ok=True)
        page_out.write_text(page(content))
        print(page_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
