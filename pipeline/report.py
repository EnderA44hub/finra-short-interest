"""
report.py — Genera docs/index.html con tres tabs:
  · Shares          → Short Interest absoluto (Pipeline A)
  · Short Float %   → SI relativo al float (Pipeline B)
  · Squeeze Screen  → tabla estilo "Freshly Squeezed" con heatmap

Gráficos al estilo Koyfin: serie histórica · dos ejes · modal por ticker.
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT        = Path(__file__).parent.parent
HISTORY_DIR = ROOT / "data" / "history"
LATEST_DIR  = ROOT / "data" / "latest"
RAW_DIR     = ROOT / "data" / "raw"
REGISTRY    = ROOT / "data" / "registry.json"
DOCS_DIR    = ROOT / "docs"

SHARES_HIST = HISTORY_DIR / "history_shares.parquet"
FLOAT_HIST  = HISTORY_DIR / "history_float.parquet"

SQUEEZE_MIN_MCAP = 50e6   # ignorar micro-caps ilíquidas en el screen
SQUEEZE_TOP_N    = 500    # filas máximas — amplio para que el filtro de mcap tenga material


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_sparklines(history: pd.DataFrame, tickers: set) -> dict:
    """{ticker: [{date, value}, ...]} solo para los tickers indicados."""
    out = {}
    sub = history[history["ticker"].isin(tickers)]
    for ticker, grp in sub.groupby("ticker"):
        grp = grp.sort_values("date")
        out[ticker] = [
            {"date": row["date"].strftime("%Y-%m-%d"), "value": float(row["value"])}
            for _, row in grp.iterrows()
        ]
    return out


def _fmt_mcap(v) -> str:
    if pd.isna(v):
        return "—"
    v = float(v)
    if v >= 1e9:
        return f"${v/1e9:,.1f}B"
    return f"${v/1e6:,.0f}M"


def _load_days_to_cover() -> pd.DataFrame:
    """Lee days_to_cover del raw parquet más reciente."""
    try:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        dates = reg.get("processed_dates", [])
        if dates:
            raw_path = RAW_DIR / f"{dates[0]}.parquet"
            if raw_path.exists():
                raw = pd.read_parquet(raw_path)
                if "days_to_cover" in raw.columns:
                    return raw[["ticker", "days_to_cover"]].drop_duplicates("ticker")
    except Exception:
        pass
    return pd.DataFrame(columns=["ticker", "days_to_cover"])


def _si_change_6m(shares_hist: pd.DataFrame, tickers: set) -> pd.DataFrame:
    """
    Calcula el cambio % del SI (shares) en los últimos ~6 meses por ticker.
    Positivo = los cortos están agregando posición. Negativo = cubriendo.
    """
    rows = []
    sub  = shares_hist[shares_hist["ticker"].isin(tickers)]
    if sub.empty:
        return pd.DataFrame(columns=["ticker", "si_chg_6m"])

    cutoff = sub["date"].max() - pd.Timedelta(days=183)

    for t, g in sub.groupby("ticker"):
        g = g.sort_values("date")
        cur = g["value"].iloc[-1]
        old = g[g["date"] <= cutoff]
        if old.empty:
            continue
        o = old["value"].iloc[-1]
        if o and o > 0:
            rows.append({"ticker": t, "si_chg_6m": (cur / o - 1) * 100})

    return pd.DataFrame(rows)


# ── carga de datos ────────────────────────────────────────────────────────────

def _load_data():
    shares_snap = pd.read_parquet(LATEST_DIR / "latest_shares.parquet")
    float_snap  = pd.read_parquet(LATEST_DIR / "latest_float.parquet")
    shares_hist = pd.read_parquet(SHARES_HIST)
    float_hist  = pd.read_parquet(FLOAT_HIST)

    ym_path = LATEST_DIR / "yahoo_metrics.parquet"
    yahoo   = pd.read_parquet(ym_path) if ym_path.exists() else pd.DataFrame(
        columns=["ticker", "float_shares", "market_cap", "price",
                 "low_52wk", "pct_off_52wk_low", "ytd_change"]
    )

    dtc = _load_days_to_cover()

    return shares_snap, float_snap, shares_hist, float_hist, yahoo, dtc


# ── construcción de filas ─────────────────────────────────────────────────────

def _build_shares_rows(shares_snap: pd.DataFrame) -> list:
    rows = []
    flagged = shares_snap[shares_snap["flag"] != "NORMAL"].sort_values(
        "pct_of_ath", ascending=False
    )
    for _, r in flagged.iterrows():
        rows.append({
            "ticker":   r["ticker"],
            "current":  f"{r['current']:,.0f}",
            "ath":      f"{r['all_time_high']:,.0f}",
            "pct":      f"{r['pct_of_ath']*100:.1f}%",
            "flag":     r["flag"],
        })
    return rows


def _build_float_rows(float_snap: pd.DataFrame) -> list:
    rows = []
    flagged = float_snap[float_snap["flag"] != "NORMAL"].sort_values(
        "current", ascending=False
    )
    for _, r in flagged.iterrows():
        rows.append({
            "ticker":   r["ticker"],
            "current":  f"{r['current']:.2f}%",
            "ath":      f"{r['all_time_high']:.2f}%",
            "pct":      f"{r['pct_of_ath']*100:.1f}%",
            "flag":     r["flag"],
        })
    return rows


def _build_squeeze_rows(shares_snap, float_snap, yahoo, dtc, shares_hist) -> list:
    """Tabla estilo Freshly Squeezed, ordenada por Short Float % desc."""
    df = float_snap.rename(columns={"current": "float_pct"})[
        ["ticker", "float_pct", "flag"]
    ].copy()

    # Flag del pipeline de SHARES (3 años de historia — el confiable para ATH)
    df = df.merge(
        shares_snap[["ticker", "current", "flag"]].rename(columns={
            "current": "si_shares", "flag": "flag_shares",
        }),
        on="ticker", how="left",
    )
    df = df.merge(yahoo, on="ticker", how="left")
    df = df.merge(dtc, on="ticker", how="left")

    df = df[df["float_pct"].notna()]
    if "market_cap" in df.columns:
        df = df[df["market_cap"].fillna(0) >= SQUEEZE_MIN_MCAP]

    df = df.sort_values("float_pct", ascending=False).head(SQUEEZE_TOP_N)

    # Tendencia del SI en 6 meses (solo para los tickers del screen)
    si_trend = _si_change_6m(shares_hist, set(df["ticker"]))
    df = df.merge(si_trend, on="ticker", how="left")

    rows = []
    for _, r in df.iterrows():
        flag_sh = r.get("flag_shares") if pd.notna(r.get("flag_shares")) else "NORMAL"
        rows.append({
            "ticker":        r["ticker"],
            "si_shares_fmt": f"{r['si_shares']:,.0f}" if pd.notna(r.get("si_shares")) else "—",
            "mcap":          _fmt_mcap(r.get("market_cap")),
            "mcap_raw":      float(r["market_cap"]) if pd.notna(r.get("market_cap")) else 0,
            "si_pct":        round(float(r["float_pct"]), 1),
            "si_pct_fmt":    f"{r['float_pct']:.1f}%",
            "dsi6":          round(float(r["si_chg_6m"]), 1) if pd.notna(r.get("si_chg_6m")) else None,
            "dsi6_fmt":      f"{r['si_chg_6m']:+.0f}%" if pd.notna(r.get("si_chg_6m")) else "—",
            "dtc":           round(float(r["days_to_cover"]), 1) if pd.notna(r.get("days_to_cover")) else None,
            "dtc_fmt":       f"{r['days_to_cover']:.1f}" if pd.notna(r.get("days_to_cover")) else "—",
            "ytd":           round(float(r["ytd_change"]), 1) if pd.notna(r.get("ytd_change")) else None,
            "ytd_fmt":       f"{r['ytd_change']:+.0f}%" if pd.notna(r.get("ytd_change")) else "—",
            "off_high":      round(float(r["pct_off_52wk_high"]), 1) if pd.notna(r.get("pct_off_52wk_high")) else None,
            "off_high_fmt":  f"{r['pct_off_52wk_high']:.0f}%" if pd.notna(r.get("pct_off_52wk_high")) else "—",
            "off_low":       round(float(r["pct_off_52wk_low"]), 1) if pd.notna(r.get("pct_off_52wk_low")) else None,
            "off_low_fmt":   f"{r['pct_off_52wk_low']:.0f}%" if pd.notna(r.get("pct_off_52wk_low")) else "—",
            "flag":          r.get("flag", "NORMAL"),
            "flag_sh":       flag_sh,
        })
    return rows


# ── entry point ───────────────────────────────────────────────────────────────

def run_report():
    print("\n[REPORT] Generando index.html...")

    shares_snap, float_snap, shares_hist, float_hist, yahoo, dtc = _load_data()

    shares_rows  = _build_shares_rows(shares_snap)
    float_rows   = _build_float_rows(float_snap)
    squeeze_rows = _build_squeeze_rows(shares_snap, float_snap, yahoo, dtc, shares_hist)

    # Sparklines para todos los tickers que aparecen en alguna tab
    visible = (
        {r["ticker"] for r in shares_rows}
        | {r["ticker"] for r in float_rows}
        | {r["ticker"] for r in squeeze_rows}
    )
    spark_shares = _build_sparklines(shares_hist, visible)
    spark_float  = _build_sparklines(float_hist,  visible)

    updated = datetime.now().strftime("%b %d, %Y %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FINRA Short Interest Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --blue: #58a6ff; --purple: #bc8cff; --red: #f85149;
    --orange: #e3b341; --green: #3fb950;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text);
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}

  header {{ padding: 20px 32px; border-bottom: 1px solid var(--border);
            display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ font-size: 1.15rem; font-weight: 600; }}
  header span {{ color: var(--muted); font-size: 0.78rem; }}

  /* Tabs */
  .tabs {{ display: flex; gap: 4px; padding: 12px 32px 0; border-bottom: 1px solid var(--border); }}
  .tab {{ background: none; border: none; color: var(--muted); padding: 10px 18px;
          font-size: 0.88rem; cursor: pointer; border-bottom: 2px solid transparent; }}
  .tab.active {{ color: var(--text); border-bottom-color: var(--blue); font-weight: 600; }}
  .tab:hover {{ color: var(--text); }}

  /* Info box */
  details.info {{ margin: 14px 32px 0; background: var(--surface);
                  border: 1px solid var(--border); border-radius: 8px;
                  font-size: 0.83rem; color: var(--muted); }}
  details.info summary {{ padding: 10px 16px; cursor: pointer; color: var(--text);
                          font-weight: 500; user-select: none; }}
  details.info .body {{ padding: 0 16px 14px; line-height: 1.55; }}
  details.info b {{ color: var(--text); }}
  details.info .formula {{ font-family: monospace; color: var(--purple);
                           background: var(--bg); padding: 1px 6px; border-radius: 4px; }}

  .controls {{ padding: 14px 32px; display: flex; gap: 12px; align-items: center;
               flex-wrap: wrap; }}
  .controls input {{ background: var(--surface); border: 1px solid var(--border);
                     color: var(--text); padding: 6px 12px; border-radius: 6px;
                     font-size: 0.85rem; width: 200px; }}
  .controls input:focus {{ outline: none; border-color: var(--blue); }}
  .filter-btn {{ background: var(--surface); border: 1px solid var(--border);
                 color: var(--muted); padding: 5px 12px; border-radius: 6px;
                 font-size: 0.8rem; cursor: pointer; }}
  .filter-btn.active {{ color: var(--text); border-color: var(--blue); }}
  .filter-btn[data-flag="ATH"].active       {{ border-color: var(--red);    color: var(--red); }}
  .filter-btn[data-flag="NEAR_HIGH"].active {{ border-color: var(--orange); color: var(--orange); }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ text-align: left; padding: 10px 16px; color: var(--muted); font-weight: 500;
        border-bottom: 1px solid var(--border); position: sticky; top: 0;
        background: var(--bg); white-space: nowrap; }}
  td {{ padding: 9px 16px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  tr:hover td {{ background: var(--surface); cursor: pointer; }}
  .ticker-cell {{ font-weight: 600; color: var(--blue); }}
  .num {{ font-variant-numeric: tabular-nums; }}
  .heat {{ font-variant-numeric: tabular-nums; border-radius: 4px; }}

  .badge-ATH       {{ color: var(--red);    font-size: 0.75rem; font-weight: 600; }}
  .badge-NEAR_HIGH {{ color: var(--orange); font-size: 0.75rem; font-weight: 600; }}

  /* Modal */
  .modal-overlay {{ display: none; position: fixed; inset: 0;
                    background: rgba(0,0,0,0.7); z-index: 100;
                    align-items: center; justify-content: center; }}
  .modal-overlay.open {{ display: flex; }}
  .modal {{ background: var(--surface); border: 1px solid var(--border);
            border-radius: 12px; width: min(760px, 95vw); padding: 24px; position: relative; }}
  .modal h2 {{ font-size: 1rem; margin-bottom: 4px; }}
  .modal .sub {{ color: var(--muted); font-size: 0.8rem; margin-bottom: 20px; }}
  .close-btn {{ position: absolute; top: 16px; right: 16px; background: none;
                border: none; color: var(--muted); font-size: 1.2rem; cursor: pointer; }}
  .metric-row {{ display: flex; gap: 12px; margin-top: 16px; }}
  .metric-card {{ background: var(--bg); border: 1px solid var(--border);
                  border-radius: 8px; padding: 12px 16px; flex: 1; }}
  .metric-label {{ font-size: 0.7rem; color: var(--muted); text-transform: uppercase;
                   letter-spacing: 0.05em; }}
  .metric-value {{ font-size: 1.25rem; font-weight: 600; margin-top: 2px; }}
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

<div class="tabs">
  <button class="tab active" data-tab="shares">SI Shares</button>
  <button class="tab" data-tab="float">Short Float %</button>
  <button class="tab" data-tab="squeeze">🔥 Squeeze Screen</button>
</div>

<details class="info">
  <summary>¿Qué significan estas métricas?</summary>
  <div class="body">
    <b>SI Shares</b> — cantidad absoluta de acciones vendidas en corto reportadas a FINRA.
    Mide cuánta apuesta bajista existe en términos brutos.<br><br>
    <b>Short Float %</b> — <span class="formula">SI Shares ÷ Float × 100</span>.
    El float son las acciones realmente disponibles para operar (excluye insiders y holdings restringidos).
    Es la medida <b>relativa</b>: 10M de shares en corto son irrelevantes en AAPL pero enormes en una small-cap.<br><br>
    <b>Days to Cover</b> — <span class="formula">SI Shares ÷ Volumen promedio diario</span>.
    Cuántos días de volumen completo necesitarían los cortos para cerrar todas sus posiciones.
    Un DTC alto significa que los cortos están atrapados: si el precio sube, su propia compra forzada acelera el movimiento — el mecanismo del squeeze.<br><br>
    <b>La relación:</b> Short Float % alto = mucha presión bajista relativa.
    DTC alto = difícil escapar. Ambos altos al mismo tiempo es la receta clásica del short squeeze.<br><br>
    <b>⚠️ Nota sobre los flags:</b> el flag de la tab <b>SI Shares</b> y del <b>Squeeze Screen</b>
    usa 3 años de historia FINRA — es el confiable. El flag de la tab <b>Short Float %</b>
    tiene historial joven (empezó con este monitor) y marcará "ATH" con facilidad hasta
    acumular meses de datos. Ante la duda, abrí el gráfico del ticker y mirá la línea azul.
  </div>
</details>

<div class="controls">
  <input type="text" id="search" placeholder="Buscar ticker...">
  <span id="flag-filters">
    <button class="filter-btn active" data-flag="ALL">All</button>
    <button class="filter-btn" data-flag="ATH">🔴 ATH</button>
    <button class="filter-btn" data-flag="NEAR_HIGH">🟠 Near High</button>
  </span>
  <span id="mcap-filters" style="display:none; gap:6px;">
    <button class="filter-btn mcap-btn active" data-mcap="0">Todas</button>
    <button class="filter-btn mcap-btn" data-mcap="300000000">$300M+</button>
    <button class="filter-btn mcap-btn" data-mcap="1000000000">$1B+</button>
    <button class="filter-btn mcap-btn" data-mcap="5000000000">$5B+</button>
    <button class="filter-btn mcap-btn" data-mcap="10000000000">$10B+</button>
  </span>
  <span id="sig-filters" style="display:none; gap:6px;">
    <button class="filter-btn" id="f-nearhigh" title="Precio a menos de 15% de su máximo de 52 semanas">📈 Cerca de máximos</button>
    <button class="filter-btn" id="f-siath" title="Short Interest (shares) en máximo histórico o cerca">🧨 SI en ATH</button>
    <button class="filter-btn" id="f-sirising" title="Los cortos agregaron posición en los últimos 6 meses">➕ SI subiendo</button>
  </span>
  <span id="count" style="color:var(--muted);font-size:0.8rem;margin-left:auto;"></span>
</div>

<div style="overflow-x:auto; padding: 0 0 80px 0;">
<table>
  <thead id="table-head"></thead>
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
const SHARES_ROWS  = {json.dumps(shares_rows,  ensure_ascii=False)};
const FLOAT_ROWS   = {json.dumps(float_rows,   ensure_ascii=False)};
const SQUEEZE_ROWS = {json.dumps(squeeze_rows, ensure_ascii=False)};
const SPARK_SHARES = {json.dumps(spark_shares, ensure_ascii=False)};
const SPARK_FLOAT  = {json.dumps(spark_float,  ensure_ascii=False)};

let activeTab    = 'shares';
let activeFilter = 'ALL';
let activeMcap   = 0;
let fNearHigh    = false;   // precio a ≤15% del máximo 52wk
let fSiAth       = false;   // SI shares en ATH / Near High
let fSiRising    = false;   // SI subió en los últimos 6 meses
let chartInstance = null;

// ── Heatmap helpers ─────────────────────────────────────────────────────────
function heatGreen(v, lo, hi) {{
  if (v === null || v === undefined) return '';
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  return `background: rgba(63,185,80,${{(t * 0.45).toFixed(3)}});`;
}}
function heatRedGreen(v, span) {{
  if (v === null || v === undefined) return '';
  const t = Math.max(-1, Math.min(1, v / span));
  return t >= 0
    ? `background: rgba(63,185,80,${{(t * 0.45).toFixed(3)}});`
    : `background: rgba(248,81,73,${{(-t * 0.45).toFixed(3)}});`;
}}

function badge(flag) {{
  if (flag === 'ATH')       return '<span class="badge-ATH">🔴 ATH</span>';
  if (flag === 'NEAR_HIGH') return '<span class="badge-NEAR_HIGH">🟠 Near High</span>';
  return '';
}}

// ── Render por tab ──────────────────────────────────────────────────────────
const TABS = {{
  shares: {{
    head: '<tr><th>Ticker</th><th>SI Shares</th><th>All-Time High</th><th>% of ATH</th><th>Flag</th></tr>',
    data: () => SHARES_ROWS,
    row: r => `
      <td class="ticker-cell">${{r.ticker}}</td>
      <td class="num">${{r.current}}</td>
      <td class="num">${{r.ath}}</td>
      <td class="num">${{r.pct}}</td>
      <td>${{badge(r.flag)}}</td>`,
  }},
  float: {{
    head: '<tr><th>Ticker</th><th>Short Float %</th><th>All-Time High</th><th>% of ATH</th><th>Flag</th></tr>',
    data: () => FLOAT_ROWS,
    row: r => `
      <td class="ticker-cell">${{r.ticker}}</td>
      <td class="num">${{r.current}}</td>
      <td class="num">${{r.ath}}</td>
      <td class="num">${{r.pct}}</td>
      <td>${{badge(r.flag)}}</td>`,
  }},
  squeeze: {{
    head: '<tr><th>Ticker</th><th>Market Cap</th><th>Short Float %</th><th>Δ SI 6m</th><th>Days to Cover</th><th>YTD</th><th>% Off 52-Wk High</th><th>% Off 52-Wk Low</th><th>Flag</th></tr>',
    data: () => SQUEEZE_ROWS,
    row: r => `
      <td class="ticker-cell">${{r.ticker}}</td>
      <td class="num">${{r.mcap}}</td>
      <td class="heat" style="${{heatGreen(r.si_pct, 5, 40)}}">${{r.si_pct_fmt}}</td>
      <td class="heat" style="${{heatRedGreen(r.dsi6, 80)}}">${{r.dsi6_fmt}}</td>
      <td class="heat" style="${{heatGreen(r.dtc, 1, 15)}}">${{r.dtc_fmt}}</td>
      <td class="heat" style="${{heatRedGreen(r.ytd, 100)}}">${{r.ytd_fmt}}</td>
      <td class="heat" style="${{heatGreen(r.off_high, -50, 0)}}">${{r.off_high_fmt}}</td>
      <td class="heat" style="${{heatGreen(r.off_low, 0, 200)}}">${{r.off_low_fmt}}</td>
      <td>${{badge(r.flag_sh)}}</td>`,
  }},
}};

function renderTable() {{
  const tab   = TABS[activeTab];
  const q     = document.getElementById('search').value.toUpperCase();
  let data    = tab.data().filter(r => r.ticker.includes(q));
  if (activeFilter !== 'ALL') {{
    // En el Squeeze Screen el flag mostrado/filtrado es el de SHARES (3 años de historia)
    data = data.filter(r =>
      (activeTab === 'squeeze' ? r.flag_sh : r.flag) === activeFilter
    );
  }}

  // Filtros del Squeeze Screen
  if (activeTab === 'squeeze') {{
    if (activeMcap > 0) data = data.filter(r => (r.mcap_raw || 0) >= activeMcap);
    if (fNearHigh)      data = data.filter(r => r.off_high !== null && r.off_high >= -15);
    if (fSiAth)         data = data.filter(r => r.flag_sh === 'ATH' || r.flag_sh === 'NEAR_HIGH');
    if (fSiRising)      data = data.filter(r => r.dsi6 !== null && r.dsi6 > 0);
  }}

  // Mostrar controles del squeeze solo en su tab
  const squeezeVisible = activeTab === 'squeeze' ? 'inline-flex' : 'none';
  document.getElementById('mcap-filters').style.display = squeezeVisible;
  document.getElementById('sig-filters').style.display  = squeezeVisible;

  document.getElementById('table-head').innerHTML = tab.head;
  const tbody = document.getElementById('table-body');
  tbody.innerHTML = '';
  data.forEach(r => {{
    const tr = document.createElement('tr');
    tr.innerHTML = tab.row(r);
    tr.onclick = () => openModal(r);
    tbody.appendChild(tr);
  }});
  document.getElementById('count').textContent = `${{data.length}} tickers`;
}}

// ── Eventos ─────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeTab = btn.dataset.tab;
    renderTable();
  }});
}});

document.getElementById('search').addEventListener('input', renderTable);

document.querySelectorAll('#flag-filters .filter-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('#flag-filters .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = btn.dataset.flag;
    renderTable();
  }});
}});

document.querySelectorAll('.mcap-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.mcap-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeMcap = Number(btn.dataset.mcap);
    renderTable();
  }});
}});

// Toggles de la firma (combinables entre sí)
document.getElementById('f-nearhigh').addEventListener('click', function() {{
  fNearHigh = !fNearHigh;
  this.classList.toggle('active', fNearHigh);
  renderTable();
}});
document.getElementById('f-siath').addEventListener('click', function() {{
  fSiAth = !fSiAth;
  this.classList.toggle('active', fSiAth);
  renderTable();
}});
document.getElementById('f-sirising').addEventListener('click', function() {{
  fSiRising = !fSiRising;
  this.classList.toggle('active', fSiRising);
  renderTable();
}});

// ── Modal con gráfico ───────────────────────────────────────────────────────
function openModal(row) {{
  const t = row.ticker;
  document.getElementById('modal-ticker').textContent = t + ' Short Interest';

  // Métricas del modal (toleran filas de distintas tabs)
  document.getElementById('m-shares').textContent =
    row.si_shares_fmt || row.current || '—';
  document.getElementById('m-float').textContent =
    row.si_pct_fmt || '—';
  document.getElementById('m-flag').innerHTML = badge(row.flag) || 'Normal';

  const sharesData = SPARK_SHARES[t] || [];
  const floatData  = SPARK_FLOAT[t]  || [];
  const labels = (sharesData.length ? sharesData : floatData).map(d => d.date);

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
          yAxisID: 'yShares', tension: 0.3, pointRadius: 0, borderWidth: 2,
        }},
        {{
          label: 'Short Float %',
          data: floatData.map(d => +d.value.toFixed(2)),
          borderColor: '#bc8cff',
          backgroundColor: 'rgba(188,140,255,0.08)',
          yAxisID: 'yFloat', tension: 0.3, pointRadius: 0, borderWidth: 2,
        }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#8b949e', font: {{ size: 11 }} }} }},
        tooltip: {{ backgroundColor: '#161b22', borderColor: '#30363d', borderWidth: 1 }},
      }},
      scales: {{
        x: {{ ticks: {{ color: '#8b949e', maxTicksLimit: 8 }}, grid: {{ color: '#21262d' }} }},
        yShares: {{
          position: 'left', ticks: {{ color: '#58a6ff' }}, grid: {{ color: '#21262d' }},
          title: {{ display: true, text: 'M Shares', color: '#58a6ff' }},
        }},
        yFloat: {{
          position: 'right', ticks: {{ color: '#bc8cff', callback: v => v + '%' }},
          grid: {{ drawOnChartArea: false }},
          title: {{ display: true, text: 'Float %', color: '#bc8cff' }},
        }},
      }}
    }}
  }});

  document.getElementById('modal').classList.add('open');
}}

document.getElementById('close-modal').onclick = () =>
  document.getElementById('modal').classList.remove('open');
document.getElementById('modal').addEventListener('click', e => {{
  if (e.target === document.getElementById('modal'))
    document.getElementById('modal').classList.remove('open');
}});

// Init
renderTable();
</script>
</body>
</html>"""

    out_path = DOCS_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    total = len(shares_rows) + len(float_rows) + len(squeeze_rows)
    print(f"   ✓ docs/index.html generado — shares: {len(shares_rows)} · "
          f"float: {len(float_rows)} · squeeze: {len(squeeze_rows)}")
    return out_path


if __name__ == "__main__":
    run_report()
