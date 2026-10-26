"""Session report: one HTML page per session in reports/, rewritten after every lap.

Open it in a browser between stints: lap times, where you lose time corner by corner against your
best lap, your theoretical best, your habits, and in a race your tyre deg.
"""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, 'reports')

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{--bg:#f6f6f4;--fg:#1b1d21;--muted:#6b6f76;--card:#fff;--line:#e3e3df;--loss:#d1453b;--gain:#2e8b57}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111418;--fg:#eceef1;--muted:#9097a1;--card:#1a1e24;--line:#2a3038;--loss:#ff6b5e;--gain:#4cc38a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:28px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}}
.stats{{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:130px}}
.stat b{{display:block;font-size:20px;font-variant-numeric:tabular-nums}} .stat span{{color:var(--muted);font-size:13px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
td,th{{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;font-variant-numeric:tabular-nums}}
th{{color:var(--muted);font-weight:600;font-size:13px}} .best td{{font-weight:700}}
svg text{{fill:var(--fg);font-size:12px}} .muted{{color:var(--muted)}} ul{{margin:0;padding-left:20px}}
</style></head><body><main>
<h1>{title}</h1><div class="muted">{subtitle}</div>
<div class="stats">{stats}</div>
{sections}
</main></body></html>"""


def lap_time(ms):
    m, s = divmod(ms / 1000, 60)
    return f"{int(m)}:{s:06.3f}"


def corner_chart(laps):
    """Average time lost (red) or gained (green) per corner against your best lap."""
    totals = {}
    for lap in laps:
        for name, loss in lap['losses'].items():
            totals.setdefault(name, []).append(loss)
    if not totals:
        return ''
    avg = sorted(((n, sum(v) / len(v)) for n, v in totals.items()), key=lambda c: (len(c[0]), c[0]))  # turn 2 before turn 10
    top = max(abs(a) for _, a in avg) or 1
    row, mid, half = 22, 300, 220
    bars = []
    for i, (name, a) in enumerate(avg):
        y, w = i * row, abs(a) / top * half
        x = mid if a >= 0 else mid - w
        colour = 'var(--loss)' if a >= 0 else 'var(--gain)'
        bars.append(f'<text x="0" y="{y + 15}">{html.escape(name.capitalize())}</text>'
                    f'<rect x="{x:.0f}" y="{y + 4}" width="{max(w, 1):.0f}" height="14" rx="2" fill="{colour}"/>'
                    f'<text x="{mid + half + 10 if a >= 0 else mid + 10}" y="{y + 15}">{a:+.2f}s</text>')
    h = len(avg) * row
    return (f'<h2>Average per corner vs your best lap</h2><svg viewBox="0 0 620 {h}" width="100%" role="img" '
            f'aria-label="Average seconds lost per corner"><line x1="{mid}" x2="{mid}" y1="0" y2="{h}" '
            f'stroke="var(--line)"/>{"".join(bars)}</svg>')


def write(eng):
    """Rewrite this session's report from the engineer's state."""
    from engineer import HABITS, TRACKS
    info = eng.info() or {}
    track = info.get('name') or TRACKS.get(int(eng.track or -1), f'Track {eng.track}')
    mode = {'race': 'Race', 'quali': 'Qualifying'}.get(eng.mode(), 'Time trial' if eng.session_type == 18 else 'Practice')
    laps = eng.laps_log
    best = min(lap['ms'] for lap in laps)
    stats = [(lap_time(best), 'best lap'), (str(len(laps)), 'laps')]
    theo = eng.theoretical()
    if theo and theo[1]:
        stats.append((lap_time(theo[0]), 'theoretical best'))
    if eng.deg is not None:
        stats.append((f"{eng.deg:.2f}s", 'tyre deg per lap'))
    if eng.own_window:
        stats.append((f"lap {eng.own_window}", 'best pit stop'))
    rows = ''.join(
        f'<tr class="{"best" if lap["ms"] == best else ""}"><td>{lap["lap"]}</td><td>{lap_time(lap["ms"])}</td>'
        f'<td>+{(lap["ms"] - best) / 1000:.3f}</td><td>{html.escape(worst_corner(lap))}</td></tr>' for lap in laps)
    sections = [corner_chart(laps),
                f'<h2>Laps</h2><table><tr><th>Lap</th><th>Time</th><th>Gap</th><th>Most time lost</th></tr>{rows}</table>']
    habits = []
    for name, h in eng.memory().get('habits', {}).items():
        for key, words in HABITS.items():
            if h['laps'] >= 3 and h.get(key, 0) / h['laps'] >= 0.5:
                habits.append(f"<li>{html.escape(name.capitalize())}: {words} ({h[key]} of {h['laps']} laps)</li>")
    if habits:
        sections.append(f'<h2>Your habits here</h2><ul>{"".join(habits)}</ul>')
    page = PAGE.format(title=html.escape(f'{track} {mode}'), subtitle=eng.started.replace('_', ' '),
                       stats=''.join(f'<div class="stat"><b>{v}</b><span>{k}</span></div>' for v, k in stats),
                       sections=''.join(sections))
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f'{eng.started}_{track.replace(" ", "_")}_{mode.replace(" ", "_")}.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(page)
    return path


def worst_corner(lap):
    if not lap['losses']:
        return ''
    name, loss = max(lap['losses'].items(), key=lambda c: c[1])
    return f"{name.capitalize()} ({loss:+.2f}s)" if loss > 0.02 else ''
