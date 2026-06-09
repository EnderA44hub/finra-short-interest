"""
transform.py — Filtra NYSE/NASDAQ y limpia el DataFrame.
Solo mantiene: ticker · date · short_interest_shares

Estrategia de filtrado (en orden de preferencia):
  1. API de NASDAQ Screener  → lista exacta de tickers listados
  2. Heurística de formato   → fallback si la API no responde
     · 1–4 letras            → casi siempre NYSE/NASDAQ
     · 5 letras sin sufijo F/Y/E/Q/K → probablemente NYSE/NASDAQ (GOOGL, CMCSA…)
     · 5 letras con sufijo F/Y/E/Q/K → casi siempre OTC/Pink Sheet
"""

import requests
import pandas as pd
from pathlib import Path

ROOT          = Path(__file__).parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

# Sufijos OTC del 5to carácter (Pink Sheet / foreign / delinquent)
OTC_FIFTH_LETTER = {"F", "Y", "E", "Q", "K"}

# NASDAQ Screener API — devuelve todos los tickers listados en cada exchange
_SCREENER_URLS = [
    ("NASDAQ", "https://api.nasdaq.com/api/screener/stocks?tableonly=true&exchange=nasdaq&limit=10000&offset=0&download=true"),
    ("NYSE",   "https://api.nasdaq.com/api/screener/stocks?tableonly=true&exchange=nyse&limit=10000&offset=0&download=true"),
    ("AMEX",   "https://api.nasdaq.com/api/screener/stocks?tableonly=true&exchange=amex&limit=10000&offset=0&download=true"),
]

_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.nasdaq.com/",
}


# ── Lista oficial vía NASDAQ API ──────────────────────────────────────────────

def get_listed_tickers_api() -> set:
    """Intenta obtener tickers listados desde la API de NASDAQ Screener."""
    listed = set()

    for exchange_name, url in _SCREENER_URLS:
        try:
            resp = requests.get(url, headers=_API_HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("data", {}).get("table", {}).get("rows") or []
            symbols = {r["symbol"].upper().strip() for r in rows if r.get("symbol")}
            listed.update(symbols)
            print(f"   {exchange_name}: {len(symbols):,} tickers")
        except Exception as e:
            print(f"   ⚠ {exchange_name} API falló: {e}")

    return listed


# ── Heurística de fallback ────────────────────────────────────────────────────

def is_likely_listed(ticker: str) -> bool:
    """
    Determina si un ticker es probablemente NYSE/NASDAQ por su formato.
    Basado en convenciones FINRA para el 5to carácter de tickers OTC.
    """
    if not isinstance(ticker, str):
        return False
    t = ticker.upper().strip()
    # Limpiar separadores de clase (BRK.A → BRKA, BF-B → BFB)
    clean = t.replace(".", "").replace("-", "")
    if not clean.isalpha():
        return False
    n = len(clean)
    if n <= 4:
        return True          # 1-4 letras: casi siempre NYSE/NASDAQ
    if n == 5:
        return clean[-1] not in OTC_FIFTH_LETTER   # GOOGL→True, AAALF→False
    return False             # 6+ letras: siempre OTC


# ── Transform principal ───────────────────────────────────────────────────────

def run_transform(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra el bulk file para quedarse solo con NYSE/NASDAQ.
    Retorna df limpio con columnas: ticker · date · short_interest_shares
    """
    print("\n[TRANSFORM] Filtrando y limpiando...")
    total_before = len(df)

    # Intentar lista oficial primero
    listed_tickers = get_listed_tickers_api()

    if listed_tickers:
        df = df[df["ticker"].str.upper().isin(listed_tickers)].copy()
        method = "API NASDAQ Screener"
    else:
        # Fallback: heurística de formato
        print("   → Usando heurística de formato (fallback)...")
        mask = df["ticker"].apply(is_likely_listed)
        df   = df[mask].copy()
        method = "heurística de formato"

    print(f"   → Filtro NYSE+NASDAQ ({method}): {total_before:,} → {len(df):,} filas")

    # ── Limpiar tipos ─────────────────────────────────────────────────────────
    df["ticker"]                = df["ticker"].str.upper().str.strip()
    df["date"]                  = pd.to_datetime(df["date"])
    df["short_interest_shares"] = df["short_interest_shares"].astype("int64")

    df = df[["ticker", "date", "short_interest_shares"]].drop_duplicates()

    if len(df) == 0:
        print("   ⚠ DataFrame vacío después del filtro")
        return df

    # ── Guardar en processed/ ─────────────────────────────────────────────────
    date_str = df["date"].iloc[0].strftime("%Y-%m-%d")
    out_path = PROCESSED_DIR / f"{date_str}.parquet"
    df.to_parquet(out_path, index=False)
    print(f"   💾 Guardado: data/processed/{date_str}.parquet  ({len(df):,} tickers)")

    return df
