"""Server-rendered HTML for the alobo-bot web page (no JS framework, no build step).

Kept separate from :mod:`web` so the markup stays pure and testable without
FastAPI installed. Everything is a plain function returning an HTML fragment, so
the page works with JavaScript disabled; a few lines of progressive enhancement
only add a "searching…" state to the form.
"""

from __future__ import annotations

import html

from .report import money
from .search import FindResult

MONTHS = "01 02 03 04 05 06 07 08 09 10 11 12".split()

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9; --fg: #16181d; --muted: #5b6472; --card: #ffffff;
  --line: #e2e5ea; --accent: #0b6b53; --accent-fg: #ffffff; --warn: #8a1c1c;
  --radius: 10px;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#101317; --fg:#e8ebef; --muted:#9aa4b2; --card:#181c22;
          --line:#2a313a; --accent:#2ea884; --accent-fg:#08150f; --warn:#ff9c9c; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font: 16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
a { color: var(--accent); }
header, main, footer { max-width: 60rem; margin-inline: auto; padding-inline: 1rem; }
header { padding-block: 1.5rem 0.5rem; }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 1.75rem 0 .5rem; }
p.sub { color: var(--muted); margin: 0; }
.visually-hidden:where(:not(:focus-within, :active)) {
  position:absolute !important; clip-path: inset(50%) !important; overflow:hidden !important;
  width:1px !important; height:1px !important; margin:-1px !important; padding:0 !important;
  border:0 !important; white-space:nowrap !important;
}
.skip-link { position:absolute; left:-999px; }
.skip-link:focus { position:static; display:inline-block; padding:.5rem; background:var(--accent);
  color:var(--accent-fg); border-radius:var(--radius); }
:where(a, button, input, select):focus-visible { outline:3px solid var(--accent); outline-offset:2px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  padding:1rem; }
form { display:grid; gap:.85rem 1rem; grid-template-columns:repeat(auto-fit, minmax(11rem, 1fr)); }
.field { display:flex; flex-direction:column; gap:.25rem; min-inline-size:0; }
.field.wide { grid-column: 1 / -1; }
label { font-weight:600; font-size:.85rem; }
.hint { color:var(--muted); font-size:.78rem; }
input, select { font:inherit; padding:.5rem .6rem; border:1px solid var(--line);
  border-radius:8px; background:var(--bg); color:var(--fg); min-inline-size:0; }
button { font:inherit; font-weight:600; padding:.6rem 1.1rem; border:0; border-radius:8px;
  background:var(--accent); color:var(--accent-fg); cursor:pointer; }
.actions { display:flex; align-items:center; gap:.75rem; grid-column:1 / -1; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:var(--radius);
  background:var(--card); }
table { border-collapse:collapse; inline-size:100%; font-size:.9rem; }
caption { text-align:left; padding:.7rem .9rem .2rem; color:var(--muted); font-size:.85rem; }
th, td { padding:.5rem .9rem; text-align:left; border-top:1px solid var(--line); }
thead th { border-top:0; background:color-mix(in srgb, var(--line) 35%, transparent);
  position:sticky; top:0; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
tr.top td { font-weight:600; }
.rank { color:var(--muted); }
.empty, .error { border-radius:var(--radius); padding:.9rem 1rem; }
.empty { background:var(--card); border:1px solid var(--line); color:var(--muted); }
.error { background:color-mix(in srgb, var(--warn) 12%, var(--card));
  border:1px solid var(--warn); color:var(--warn); }
.venue-sub { color:var(--muted); font-size:.82rem; }
footer { color:var(--muted); font-size:.8rem; padding-block:2rem 3rem; }
""".strip()

SCRIPT = """
addEventListener('submit', (e) => {
  const form = e.target;
  const btn = form.querySelector('button[type=submit]');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = 'Đang tìm… / Searching…';
  form.setAttribute('aria-busy', 'true');
});
""".strip()


def esc(text: object) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def field(
    name: str,
    label: str,
    value: str = "",
    *,
    type: str = "text",
    hint: str = "",
    placeholder: str = "",
    step: str = "",
    wide: bool = False,
) -> str:
    """One labelled control, with its hint wired up via aria-describedby."""
    described = f' aria-describedby="{name}-hint"' if hint else ""
    step_attr = f' step="{step}"' if step else ""
    placeholder_attr = f' placeholder="{esc(placeholder)}"' if placeholder else ""
    hint_html = f'<span class="hint" id="{name}-hint">{esc(hint)}</span>' if hint else ""
    return f"""<div class="field{' wide' if wide else ''}">
  <label for="{name}">{esc(label)}</label>
  <input id="{name}" name="{name}" type="{type}" value="{esc(value)}"{step_attr}{placeholder_attr}{described}>
  {hint_html}
</div>"""


def sport_select(sports: list[tuple[str, str]], selected: str) -> str:
    options = "".join(
        f'<option value="{esc(key)}"{" selected" if key == selected else ""}>{esc(name)}</option>'
        for key, name in sports
    )
    return f"""<div class="field">
  <label for="sport">Môn / Sport</label>
  <select id="sport" name="sport">{options}</select>
  <span class="hint" id="sport-hint">Chỉ hỗ trợ các môn có sân cho thuê theo giờ.</span>
</div>"""


def render_form(query: dict, sports: list[tuple[str, str]]) -> str:
    return f"""<form method="get" action="/" class="card">
  {field("place", "Khu vực / Area", query.get("place") or "", wide=True,
         hint="Tên đường, quận, thành phố — hoặc để trống và dùng toạ độ.",
         placeholder="Cầu Giấy, Hà Nội")}
  {field("lat", "Vĩ độ / Latitude", query.get("lat") or "", type="number", step="any",
         hint="Để trống nếu tìm theo tên khu vực.")}
  {field("lng", "Kinh độ / Longitude", query.get("lng") or "", type="number", step="any",
         hint="Cần nhập cùng với vĩ độ.")}
  {field("radius", "Bán kính / Radius", query.get("radius") or "", type="number", step="any",
         hint="km, chỉ dùng khi có toạ độ.")}
  {field("date", "Ngày / Date", query.get("date") or "", type="date",
         hint="Mặc định là hôm nay.")}
  {field("from", "Từ / From", query.get("from") or "18:00", placeholder="18:00")}
  {field("to", "Đến / To", query.get("to") or "21:00", placeholder="21:00")}
  {sport_select(sports, query.get("sport") or "pickleball")}
  <div class="actions">
    <button type="submit">Tìm sân rẻ nhất</button>
    <span class="hint">Chỉ đọc — không đặt sân, không thanh toán.</span>
  </div>
</form>"""


def render_options(result: FindResult) -> str:
    ranked = result.ranked
    if not ranked:
        return ('<p class="empty">Không tìm thấy sân nào có giá trong khung giờ này. '
                'Hãy thử mở rộng bán kính, đổi khu vực hoặc ngày khác.</p>')
    rows = []
    for index, opt in enumerate(ranked[:50], start=1):
        distance = f"{opt.distance_km:.1f} km" if opt.distance_km is not None else "—"
        rows.append(
            f'<tr class="{"top" if index == 1 else ""}">'
            f'<td class="rank">{index}</td>'
            f'<td class="num">{money(opt.total_price)}</td>'
            f'<td class="num">{money(opt.hourly_price)}</td>'
            f'<td>{esc(opt.core.name)}<div class="venue-sub">{esc(opt.core_type.name)}</div></td>'
            f'<td><a href="{esc(opt.booking_url)}" rel="noopener">{esc(opt.branch.name)}</a>'
            f'<div class="venue-sub">{esc(opt.branch.address)}</div></td>'
            f'<td class="num">{distance}</td></tr>'
        )
    return f"""<div class="table-wrap">
<table>
  <caption>Giá thuê sân cho khung giờ đã chọn, rẻ nhất trước — {len(ranked)} sân</caption>
  <thead><tr>
    <th scope="col">#</th>
    <th scope="col" class="num">Tổng</th>
    <th scope="col" class="num">Mỗi giờ</th>
    <th scope="col">Sân</th>
    <th scope="col">Chi nhánh</th>
    <th scope="col" class="num">Khoảng cách</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>"""


def render_sessions(result: FindResult) -> str:
    rows = []
    for branch_result in result.results:
        for session in sorted(branch_result.sessions, key=lambda s: s.ticket_price):
            when = f"{session.start:%H:%M}" if session.start else "—"
            rows.append(
                f"<tr><td class='num'>{money(session.ticket_price)}</td>"
                f"<td>{esc(session.name)}<div class='venue-sub'>"
                f"{esc(', '.join(session.court_names))}</div></td>"
                f"<td>{when}</td><td class='num'>{session.spots_left}</td>"
                f"<td>{esc(branch_result.branch.name)}</td></tr>"
            )
    if not rows:
        return ""
    return f"""<h2>Suất chơi chung (giá mỗi người)</h2>
<div class="table-wrap">
<table>
  <caption>Social / open-play trong khung giờ — trả theo vé, không phải theo sân</caption>
  <thead><tr>
    <th scope="col" class="num">Vé</th><th scope="col">Suất</th><th scope="col">Bắt đầu</th>
    <th scope="col" class="num">Chỗ còn</th><th scope="col">Chi nhánh</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>"""


def render_results(result: FindResult) -> str:
    query = result.query
    area = query.place or f"{query.latitude:.4f}, {query.longitude:.4f}"
    start = f"{query.start_minute // 60:02d}:{query.start_minute % 60:02d}"
    end = f"{query.end_minute // 60:02d}:{query.end_minute % 60:02d}"
    from .pricing import window_bounds  # local import keeps module import cycle-free

    start_dt, end_dt = window_bounds(query.day, query.start_minute, query.end_minute)
    end_label = f"{end_dt:%H:%M}" if start_dt.date() == end_dt.date() else f"{end_dt:%d/%m %H:%M}"
    return f"""<section aria-labelledby="results-heading">
  <h2 id="results-heading">Kết quả — {esc(result.sport_name)}</h2>
  <p class="sub">{esc(area)} · {query.day:%d/%m/%Y} {start}–{end_label} ·
     đã quét {result.branches_scanned} chi nhánh · cập nhật {result.generated_at:%H:%M}</p>
  {render_options(result)}
  {render_sessions(result)}
</section>"""


def render_page(
    *,
    sports: list[tuple[str, str]],
    query: dict | None = None,
    result: FindResult | None = None,
    error: str | None = None,
) -> str:
    """The whole page: header, search form, then results (or an error)."""
    body = []
    if error:
        body.append(f'<p class="error" role="alert">Lỗi: {esc(error)}</p>')
    if result is not None:
        body.append(render_results(result))
    elif not error:
        body.append('<p class="empty">Nhập khu vực và khung giờ để so sánh giá sân.</p>')
    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>alobo-bot — sân pickleball rẻ nhất</title>
<style>{STYLE}</style>
</head>
<body>
<header>
  <a class="skip-link" href="#content">Bỏ qua tới nội dung</a>
  <h1>alobo-bot</h1>
  <p class="sub">Tìm sân Pickleball rẻ nhất trên AloBooking theo khu vực và khung giờ.
     Dữ liệu công khai · chỉ đọc.</p>
</header>
<main id="content" tabindex="-1">
  <h2>Tiêu chí tìm</h2>
  {render_form(query or {}, sports)}
  {''.join(body)}
</main>
<footer>
  <p>Nguồn: API công khai của datlich.alobo.vn. Giá là giá niêm yết cho thuê lẻ theo giờ.
     <a href="/report.json">JSON</a> · <a href="/health">trạng thái</a> ·
     <a href="?place=H%C3%A0%20N%E1%BB%99i&amp;from=18:00&amp;to=21:00">ví dụ: Hà Nội 18:00–21:00</a></p>
  <p>Bot không kiểm tra tình trạng còn trống và không đặt sân — vui lòng xác nhận trong ứng dụng AloBooking.</p>
</footer>
<script>{SCRIPT}</script>
</body>
</html>
"""
