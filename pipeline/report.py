"""
report.py — Genera docs/index.html con la tabla interactiva y gráficos
al estilo Koyfin: serie histórica · dos ejes · marcador ATH.
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT        = Path(__file__).parent.parent
HISTORY_DIR = ROOT / "data" / "history"
LATEST_DIR  = ROOT / "data" / "latest"
DOCS_DIR    = ROOT / "docs"

SHARES_HIST = HISTORY_DIR / "history_shares.parquet"
FLOAT_HIST  = HISTORY_DIR / "history_float.parquet"


def _build_sparklines(history: pd.DataFrame) -> dict:
    """Retorna {ticker: [{date, value}, ...]} para los tickers con flag ATH/NEAR_HIGH."""
    out = {}
    for ticker, grp in history.groupby("ticker"):
        grp = grp.sort_values("date")
        out[ticker] = [
            {"date": row["date"].strftime("%Y-%m-%d"), "value": row["value"]}
            for _, row in grp.iterrows()
        ]
    return out


def _load_data():
    shares_snap   = pd.read_parquet(LATEST_DIR / "latest_shares.parquet")
    float_snap    = pd.read_parquet(LATEST_DIR / "latest_float.parquet")
    shares_hist   = pd.read_parquet(SHARES_HIST)
    float_hist    = pd.read_parquet(FLOAT_HIST)

    # Solo tickers con flag interesante
    flagged = set(
        shares_snap[shares_snap["flag"] != "NORMAL"]["ticker"].tolist() +
        float_snap[float_snap["flag"] != "NORMAL"]["ticker"].tolist()
    )

    shares_hist_f = shares_hist[shares_hist["ticker"].isin(flagged)]
    float_hist_f  = float_hist[float_hist["ticker"].isin(flagged)]

    return shares_snap, float_snap, shares_hist_f, float_hist_f, flagged


def run_report():
    print("\n[REPORT] Generando index.html...")

    shares_snap, float_snap, shares_hist, float_hist, flagged = _load_data()

    # Merge snapshots
    combined = shares_snap.merge(
        float_snap[["ticker", "current", "all_time_high", "flag"]].rename(columns={
            "current":      "float_pct",
            "all_time_high":"float_ath",
            "flag":         "flag_float",
        }),
        on="ticker", how="outer"
    )
    combined = combined[combined["ticker"].isin(flagged)].sort_values("flag")

    # Sparkline data para el JS
    sparklines_shares = _build_sparklines(shares_hist)
    sparklines_float  = _build_sparklines(float_hist)

    updated = datetime.now().strftime("%b %d, %Y %H:%M UTC")

    # Serializar a JSON para incrustar en el HTML
    table_rows = []
    for _, row in combined.iterrows():
        ticker = row["ticker"]
        flag   = row.get("flag", "NORMAL")
        badge  = {"ATH": "🔴 ATH", "NEAR_HIGH": "🟠 Near High"}.get(flag, "")
        table_rows.append({
            "ticker":         ticker,
            "si_shares":      f"{row['current']:,.0f}" if pd.notna(row.get("current")) else "—",
            "si_ath":         f"{row['all_time_high']:,.0f}" if pd.notna(row.get("all_time_high")) else "—",
            "si_float":       f"{row['float_pct']:.2f}%" if pd.notna(row.get("float_pct")) else "—",
            "flag":           flag,
            "badge":          badge,
        })

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FINRA Short Interest Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:       #0d1117;
    --surface:  #161b22;
    --border:   #30363d;
    --text:     #e6edf3;
    --muted:    #8b949e;
    --blue:     #58a6ff;
    --purple:   #bc8cff;
    --red:      #f85149;
    --orange:   #e3b341;
    --green:    #3fb950;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}

  header {{ padding: 24px 32px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ font-size: 1.2rem; font-weight: 600; letter-spacing: -0.02em; }}
  header span {{ color: var(--muted); font-size: 0.8rem; }}

  .controls {{ padding: 16px 32px; display: flex; gap: 12px; align-items: center; border-bottom: 1px solid var(--border); flex-wrap: wrap; }}
  .controls input {{ background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; width: 200px; }}
  .controls input:focus {{ outline: none; border-color: var(--blue); }}
  .filter-btn {{ background: var(--surface); border: 1px solid var(--border); color: var(--muted); padding: 5px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; }}
  .filter-btn.active {{ color: var(--text); border-color: var(--blue); }}
  .filter-btn[data-flag="ATH"].active       {{ border-color: var(--red);    color: var(--red); }}
  .filter-btn[data-flag="NEAR_HIGH"].active {{ border-color: var(--orange); color: var(--orange); }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ text-align: left; padding: 10px 16px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--bg); }}
  td {{ padding: 10px 16px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  tr:hover td {{ background: var(--surface); cursor: pointer; }}
  .ticker-cell {{ font-weight: 600; color: var(--blue); }}
  .badge-ath       {{ color: var(--red);    font-size: 0.75rem; }}
  .badge-near      {{ color: var(--orange); font-size: 0.75rem; }}
  .num             {{ font-variant-numeric: tabular-nums; }}

  /* Modal */
  .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; }}
  .modal-overlay.open {{ display: flex; }}
  .modal {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; width: min(760px, 95vw); padding: 24px; position: relative; }}
  .modal h2 {{ font-size: 1rem; margin-bottom: 4px; }}
  .modal .sub {{ color: var(--muted); font-size: 0.8rem; margin-bottom: 20px; }}
  .close-btn {{ position: absolute; top: 16px; right: 16px; background: none; border: none; color: var(--muted); font-size: 1.2rem; cursor: pointer; }}
  .metric-row {{ display: flex; gap: 12px; margin-top: 16px; }}
  .metric-card {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; flex: 1; }}
  .metric-label {{ font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
  .metric-value {{ font-size: 1.3rem; font-weight: 600; margin-top: 2px; }}
  .metric-value.blue   {{ color: var(--blue); }}
  .metric-value.purple {{ color: var(--purple); }}
  .chart-wrap {{ position: relative; height: 260px; margin-top: 20px; }}
</style>
</head>
<body>

<header>
  <h1>📉 FINRA Short Interest Monitor</h1>
  <span>Actualizado: {updated}</span>
</header>

<div class="controls">
  <input type="text" id="search" placeholder="Buscar ticker...">
  <button class="filter-btn active" data-flag="ALL">All</button>
  <button class="filter-btn" data-flag="ATH">🔴 ATH</button>
  <button class="filter-btn" data-flag="NEAR_HIGH">🟠 Near High</button>
  <span id="count" style="color:var(--muted);font-size:0.8rem;margin-left:auto;"></span>
</div>

<div style="overflow-x:auto; padding: 0 0 80px 0;">
<table id="main-table">
  <thead>
    <tr>
      <th>Ticker</th>
      <th>Short Interest (Shares)</th>
      <th>All-Time High</th>
      <th>Short Float %</th>
      <th>Flag</th>
    </tr>
  </thead>
  <tbody id="table-body"></tbody>
</table>
</div>

<!-- Modal -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <button class="close-btn" id="close-modal">✕</button>
    <h2 id="modal-ticker"></h2>
    <div class="sub">Short Interest — Serie Histórica</div>
    <div class="chart-wrap"><canvas id="chart"></canvas></div>
    <div class="metric-row">
      <div class="metric-card">
        <div class="metric-label">SI Shares (actual)</div>
        <div class="metric-value blue" id="m-shares">—</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Short Float %</div>
        <div class="metric-value purple" id="m-float">—</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Estado</div>
        <div class="metric-value" id="m-flag">—</div>
      </div>
    </div>
  </div>
</div>

<script>
const TABLE_DATA = {json.dumps(table_rows, ensure_ascii=False)};
const SPARK_SHARES = {json.dumps(sparklines_shares, ensure_ascii=False)};
const SPARK_FLOAT  = {json.dumps(sparklines_float,  ensure_ascii=False)};

let activeFilter = 'ALL';
let chartInstance = null;

function renderTable(data) {{
  const tbody = document.getElementById('table-body');
  tbody.innerHTML = '';
  data.forEach(row => {{
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="ticker-cell">${{row.ticker}}</td>
      <td class="num">${{row.si_shares}}</td>
      <td class="num">${{row.si_ath}}</td>
      <td class="num">${{row.si_float}}</td>
      <td>${{row.badge}}</td>
    `;
    tr.onclick = () => openModal(row);
    tbody.appendChild(tr);
  }});
  document.getElementById('count').textContent = `${{data.length}} tickers`;
}}

function filter() {{
  const q = document.getElementById('search').value.toUpperCase();
  let data = TABLE_DATA.filter(r => r.ticker.includes(q));
  if (activeFilter !== 'ALL') data = data.filter(r => r.flag === activeFilter);
  renderTable(data);
}}

document.getElementById('search').addEventListener('input', filter);
document.querySelectorAll('.filter-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = btn.dataset.flag;
    filter();
  }});
}});

function openModal(row) {{
  document.getElementById('modal-ticker').textContent = row.ticker + ' Short Interest';
  document.getElementById('m-shares').textContent = row.si_shares;
  document.getElementById('m-float').textContent  = row.si_float;
  document.getElementById('m-flag').textContent   = row.badge || 'Normal';

  const sharesData = SPARK_SHARES[row.ticker] || [];
  const floatData  = SPARK_FLOAT[row.ticker]  || [];
  const labels = sharesData.map(d => d.date);

  if (chartInstance) chartInstance.destroy();
  chartInstance = new Chart(document.getElementById('chart'), {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{
          label: 'Short Interest (M shares)',
          data: sharesData.map(d => +(d.value / 1e6).toFixed(2)),
          borderColor: '#58a6ff',
          backgroundColor: 'rgba(88,166,255,0.08)',
          yAxisID: 'yShares',
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        }},
        {{
          label: 'Short Float %',
          data: floatData.map(d => +d.value.toFixed(2)),
          borderColor: '#bc8cff',
          backgroundColor: 'rgba(188,140,255,0.08)',
          yAxisID: 'yFloat',
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#8b949e', font: {{ size: 11 }} }} }},
        tooltip: {{ backgroundColor: '#161b22', borderColor: '#30363d', borderWidth: 1 }},
      }},
      scales: {{
        x: {{ ticks: {{ color: '#8b949e', maxTicksLimit: 8 }}, grid: {{ color: '#21262d' }} }},
        yShares: {{
          position: 'left',
          ticks: {{ color: '#58a6ff' }},
          grid: {{ color: '#21262d' }},
          title: {{ display: true, text: 'M Shares', color: '#58a6ff' }},
        }},
        yFloat: {{
          position: 'right',
          ticks: {{ color: '#bc8cff', callback: v => v + '%' }},
          grid: {{ drawOnChartArea: false }},
          title: {{ display: true, text: 'Float %', color: '#bc8cff' }},
        }},
      }}
    }}
  }});

  document.getElementById('modal').classList.add('open');
}}

document.getElementById('close-modal').onclick = () => {{
  document.getElementById('modal').classList.remove('open');
}};
document.getElementById('modal').addEventListener('click', e => {{
  if (e.target === document.getElementById('modal')) {{
    document.getElementById('modal').classList.remove('open');
  }}
}});

// Init
renderTable(TABLE_DATA);
</script>
</body>
</html>"""

    out_path = DOCS_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"   ✓ docs/index.html generado — {len(table_rows)} tickers")
    return out_path


if __name__ == "__main__":
    run_report()
