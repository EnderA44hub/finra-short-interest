"""
transform.py — Filtra NYSE/NASDAQ y limpia el DataFrame.
Solo mantiene: ticker · date · short_interest_shares
"""

import pandas as pd
from pathlib import Path

ROOT           = Path(__file__).parent.parent
PROCESSED_DIR  = ROOT / "data" / "processed"

# Códigos de mercado en FINRA para NYSE y NASDAQ
NYSE_NASDAQ_CODES = {"N", "Q", "A"}   # N=NYSE, Q=NASDAQ, A=NYSE American


def run_transform(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra el bulk file para quedarse solo con NYSE/NASDAQ.
    Retorna df limpio con columnas: ticker · date · short_interest_shares
    """
    print("\n[TRANSFORM] Filtrando y limpiando...")

    total_before = len(df)

    # Si viene la columna de mercado, filtrar; si no, pasar todo
    if "marketCategoryCode" in df.columns:
        df = df[df["marketCategoryCode"].isin(NYSE_NASDAQ_CODES)].copy()
        print(f"   → Filtro NYSE/NASDAQ: {total_before:,} → {len(df):,} filas")
    else:
        print("   ⚠ marketCategoryCode no presente — se conservan todos los tickers")

    # Asegurar tipos correctos
    df["ticker"]                = df["ticker"].str.upper().str.strip()
    df["date"]                  = pd.to_datetime(df["date"])
    df["short_interest_shares"] = df["short_interest_shares"].astype("int64")

    # Solo las 3 columnas que necesitamos
    df = df[["ticker", "date", "short_interest_shares"]].drop_duplicates()

    # Guardar en processed/
    date_str = df["date"].iloc[0].strftime("%Y-%m-%d")
    out_path = PROCESSED_DIR / f"{date_str}.parquet"
    df.to_parquet(out_path, index=False)
    print(f"   💾 Guardado: data/processed/{date_str}.parquet")

    return df
