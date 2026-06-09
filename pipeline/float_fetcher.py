"""
float_fetcher.py — Obtiene y cachea el float de cada ticker desde Yahoo Finance.
El float se guarda en data/cache/float_cache.parquet y se reutiliza.
Solo se re-fetcha si el ticker no está en cache o si hace >30 días.
"""

import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import datetime, timedelta

ROOT        = Path(__file__).parent.parent
CACHE_PATH  = ROOT / "data" / "cache" / "float_cache.parquet"
CACHE_TTL   = timedelta(days=30)    # refrescar float cada 30 días


def _load_cache() -> pd.DataFrame:
    if CACHE_PATH.exists():
        return pd.read_parquet(CACHE_PATH)
    return pd.DataFrame(columns=["ticker", "float_shares", "fetched_at"])


def _save_cache(df: pd.DataFrame):
    df.to_parquet(CACHE_PATH, index=False)


def _is_stale(fetched_at: pd.Timestamp) -> bool:
    return (datetime.now() - fetched_at.to_pydatetime()) > CACHE_TTL


def fetch_float(tickers: list[str]) -> pd.DataFrame:
    """
    Para la lista de tickers retorna DataFrame con:
    ticker · float_shares
    Usa cache cuando está vigente; va a Yahoo solo para los nuevos/vencidos.
    """
    print(f"\n[FLOAT] Obteniendo float para {len(tickers):,} tickers...")

    cache  = _load_cache()
    now    = datetime.now()
    result = []

    # Separar los que necesitan fetch de los que están en cache vigente
    needs_fetch = []
    for t in tickers:
        row = cache[cache["ticker"] == t]
        if row.empty or _is_stale(row.iloc[0]["fetched_at"]):
            needs_fetch.append(t)
        else:
            result.append({
                "ticker":        t,
                "float_shares":  row.iloc[0]["float_shares"],
            })

    print(f"   📦 Desde cache: {len(result):,}  |  Fetching Yahoo: {len(needs_fetch):,}")

    # Fetch en lotes de 100 para no saturar Yahoo
    batch_size = 100
    for i in range(0, len(needs_fetch), batch_size):
        batch = needs_fetch[i : i + batch_size]
        try:
            raw = yf.download(
                batch,
                period="1d",
                progress=False,
                group_by="ticker",
            )
            for ticker in batch:
                try:
                    info  = yf.Ticker(ticker).info
                    float_val = info.get("floatShares") or info.get("sharesOutstanding")
                    if float_val:
                        result.append({"ticker": ticker, "float_shares": float_val})
                        # Actualizar cache
                        cache = cache[cache["ticker"] != ticker]
                        new_row = pd.DataFrame([{
                            "ticker":      ticker,
                            "float_shares": float_val,
                            "fetched_at":  pd.Timestamp(now),
                        }])
                        cache = pd.concat([cache, new_row], ignore_index=True)
                except Exception:
                    pass    # ticker inválido o sin dato — se omite
        except Exception as e:
            print(f"   ⚠ Error en batch {i//batch_size + 1}: {e}")

    _save_cache(cache)
    print(f"   ✓ Float disponible para {len(result):,} tickers")

    return pd.DataFrame(result)
