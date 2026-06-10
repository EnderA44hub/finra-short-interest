"""
float_fetcher.py — Obtiene y cachea datos de Yahoo Finance para cada ticker:
  · float_shares       → para Short Float %
  · market_cap         → para Squeeze Screen
  · price              → precio actual
  · low_52wk           → mínimo de 52 semanas
  · pct_off_52wk_low   → % por encima del mínimo de 52 semanas
  · ytd_change         → cambio % en el año

Estrategia de eficiencia:
  · Precio, 52wk low e YTD se calculan desde yf.download() batcheado
    (1 request por cada 100 tickers — rápido)
  · float_shares y market_cap requieren .info por ticker (lento) —
    se cachean 30 días y solo se re-fetchan los nuevos/vencidos

Cache: data/cache/float_cache.parquet
"""

import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import datetime, timedelta

ROOT        = Path(__file__).parent.parent
CACHE_PATH  = ROOT / "data" / "cache" / "float_cache.parquet"
CACHE_TTL   = timedelta(days=30)    # refrescar float/mcap cada 30 días

CACHE_COLS  = ["ticker", "float_shares", "market_cap", "fetched_at"]


# ── cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> pd.DataFrame:
    if CACHE_PATH.exists():
        df = pd.read_parquet(CACHE_PATH)
        # Migrar cache viejo que no tenía market_cap
        for c in CACHE_COLS:
            if c not in df.columns:
                df[c] = pd.NA
        return df[CACHE_COLS]
    return pd.DataFrame(columns=CACHE_COLS)


def _save_cache(df: pd.DataFrame):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE_PATH, index=False)


def _is_stale(fetched_at) -> bool:
    if pd.isna(fetched_at):
        return True
    return (datetime.now() - pd.Timestamp(fetched_at).to_pydatetime()) > CACHE_TTL


# ── métricas de precio (batch, rápido) ────────────────────────────────────────

def fetch_price_metrics(tickers: list[str]) -> pd.DataFrame:
    """
    Descarga 1 año de precios en batches y calcula por ticker:
      price · low_52wk · pct_off_52wk_low · ytd_change
    """
    print(f"   📈 Descargando precios (1y) para {len(tickers):,} tickers...")

    rows       = []
    batch_size = 100
    year_start = pd.Timestamp(datetime.now().year, 1, 1)

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            raw = yf.download(
                batch,
                period="1y",
                interval="1d",
                progress=False,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
            )
        except Exception as e:
            print(f"   ⚠ Error en batch precios {i//batch_size + 1}: {e}")
            continue

        for t in batch:
            try:
                # Con group_by="ticker", las columnas son MultiIndex (ticker, campo)
                if len(batch) > 1:
                    if t not in raw.columns.get_level_values(0):
                        continue
                    hist = raw[t].dropna(subset=["Close"])
                else:
                    hist = raw.dropna(subset=["Close"])

                if hist.empty:
                    continue

                price    = float(hist["Close"].iloc[-1])
                low_52wk = float(hist["Low"].min())

                # YTD: primer cierre del año actual
                ytd_hist = hist[hist.index >= year_start]
                if not ytd_hist.empty:
                    first_close = float(ytd_hist["Close"].iloc[0])
                    ytd_change  = (price / first_close - 1) * 100 if first_close else None
                else:
                    ytd_change = None

                pct_off_low = (price / low_52wk - 1) * 100 if low_52wk else None

                rows.append({
                    "ticker":           t,
                    "price":            round(price, 2),
                    "low_52wk":         round(low_52wk, 2),
                    "pct_off_52wk_low": round(pct_off_low, 1) if pct_off_low is not None else None,
                    "ytd_change":       round(ytd_change, 1)  if ytd_change is not None else None,
                })
            except Exception:
                continue

        done = min(i + batch_size, len(tickers))
        if done % 1000 < batch_size:
            print(f"      ... {done:,}/{len(tickers):,}")

    print(f"   ✓ Métricas de precio para {len(rows):,} tickers")
    return pd.DataFrame(rows)


# ── float + market cap (por ticker, cacheado) ─────────────────────────────────

def fetch_float(tickers: list[str]) -> pd.DataFrame:
    """
    Para la lista de tickers retorna DataFrame con:
      ticker · float_shares · market_cap · price · low_52wk ·
      pct_off_52wk_low · ytd_change
    Usa cache de 30 días para float/market_cap; precios siempre frescos.
    """
    print(f"\n[FLOAT] Obteniendo datos Yahoo para {len(tickers):,} tickers...")

    cache  = _load_cache()
    now    = datetime.now()
    result = []

    # ── 1. Separar cache vigente vs fetch necesario ───────────────────────────
    needs_fetch = []
    for t in tickers:
        row = cache[cache["ticker"] == t]
        if row.empty or _is_stale(row.iloc[0]["fetched_at"]):
            needs_fetch.append(t)
        else:
            result.append({
                "ticker":       t,
                "float_shares": row.iloc[0]["float_shares"],
                "market_cap":   row.iloc[0]["market_cap"],
            })

    print(f"   📦 Desde cache: {len(result):,}  |  Fetching Yahoo: {len(needs_fetch):,}")

    # ── 2. Fetch .info para float + market cap (lento, solo los necesarios) ───
    new_cache_rows = []
    for idx, ticker in enumerate(needs_fetch, 1):
        try:
            info      = yf.Ticker(ticker).info
            float_val = info.get("floatShares") or info.get("sharesOutstanding")
            mcap      = info.get("marketCap")

            if float_val:
                result.append({
                    "ticker":       ticker,
                    "float_shares": float_val,
                    "market_cap":   mcap,
                })
                new_cache_rows.append({
                    "ticker":       ticker,
                    "float_shares": float_val,
                    "market_cap":   mcap,
                    "fetched_at":   pd.Timestamp(now),
                })
        except Exception:
            pass    # ticker inválido o sin dato — se omite

        if idx % 500 == 0:
            print(f"      ... {idx:,}/{len(needs_fetch):,} (.info)")
            # Guardar cache parcial por si el proceso se interrumpe
            if new_cache_rows:
                partial = pd.DataFrame(new_cache_rows)
                cache   = cache[~cache["ticker"].isin(partial["ticker"])]
                cache   = pd.concat([cache, partial], ignore_index=True)
                _save_cache(cache)
                new_cache_rows = []

    if new_cache_rows:
        partial = pd.DataFrame(new_cache_rows)
        cache   = cache[~cache["ticker"].isin(partial["ticker"])]
        cache   = pd.concat([cache, partial], ignore_index=True)

    _save_cache(cache)

    df_float = pd.DataFrame(result)
    print(f"   ✓ Float disponible para {len(df_float):,} tickers")

    if df_float.empty:
        return df_float

    # ── 3. Métricas de precio (batch, rápido) — solo tickers con float ────────
    df_prices = fetch_price_metrics(df_float["ticker"].tolist())

    if not df_prices.empty:
        df_float = df_float.merge(df_prices, on="ticker", how="left")

    # ── 4. Persistir para que report.py pueda leerlas ─────────────────────────
    out_path = ROOT / "data" / "latest" / "yahoo_metrics.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_float.to_parquet(out_path, index=False)
    print(f"   💾 Métricas Yahoo guardadas: data/latest/yahoo_metrics.parquet")

    return df_float
