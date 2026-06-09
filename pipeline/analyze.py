"""
analyze.py — Acumula histórico y detecta ATH / Near High para cada ticker.
Genera dos archivos de historia:
  · data/history/history_shares.parquet  → Pipeline A (shares absolutas)
  · data/history/history_float.parquet   → Pipeline B (short float %)
"""

import pandas as pd
from pathlib import Path

ROOT         = Path(__file__).parent.parent
HISTORY_DIR  = ROOT / "data" / "history"
LATEST_DIR   = ROOT / "data" / "latest"

SHARES_HIST  = HISTORY_DIR / "history_shares.parquet"
FLOAT_HIST   = HISTORY_DIR / "history_float.parquet"

NEAR_HIGH_PCT = 0.95    # percentil para "Near High"


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_history(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["ticker", "date", "value"])


def _append_new(history: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    """Agrega las filas nuevas evitando duplicados por ticker+date."""
    combined = pd.concat([history, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["ticker", "date"], keep="last")
    combined = combined.sort_values(["ticker", "date"])
    return combined


def _flag_ath(history: pd.DataFrame) -> pd.DataFrame:
    """
    Para cada ticker calcula:
      · all_time_high   → máximo histórico de value
      · pct_of_ath      → current / ath  (1.0 = exactamente en ATH)
      · flag            → ATH / NEAR_HIGH / NORMAL
    Retorna solo la última fila por ticker (snapshot actual).
    """
    latest = history.sort_values("date").groupby("ticker").last().reset_index()
    latest = latest.rename(columns={"value": "current"})

    ath = (
        history.groupby("ticker")["value"]
        .max()
        .reset_index()
        .rename(columns={"value": "all_time_high"})
    )

    latest = latest.merge(ath, on="ticker")
    latest["pct_of_ath"] = latest["current"] / latest["all_time_high"]

    def assign_flag(row):
        if row["pct_of_ath"] >= 1.0:
            return "ATH"
        elif row["pct_of_ath"] >= NEAR_HIGH_PCT:
            return "NEAR_HIGH"
        else:
            return "NORMAL"

    latest["flag"] = latest.apply(assign_flag, axis=1)
    return latest


# ── Pipeline A — shares absolutas ────────────────────────────────────────────

def run_analyze_shares(df_processed: pd.DataFrame) -> pd.DataFrame:
    """
    df_processed: ticker · date · short_interest_shares
    """
    print("\n[ANALYZE A] Short Interest en shares...")

    new_rows = df_processed.rename(columns={"short_interest_shares": "value"})[
        ["ticker", "date", "value"]
    ]

    history  = _load_history(SHARES_HIST)
    history  = _append_new(history, new_rows)
    history.to_parquet(SHARES_HIST, index=False)

    snapshot = _flag_ath(history)
    snapshot.to_parquet(LATEST_DIR / "latest_shares.parquet", index=False)

    ath_count  = (snapshot["flag"] == "ATH").sum()
    near_count = (snapshot["flag"] == "NEAR_HIGH").sum()
    print(f"   🔴 ATH: {ath_count:,}  |  🟠 Near High: {near_count:,}  |  Total: {len(snapshot):,}")

    return snapshot


# ── Pipeline B — short float % ───────────────────────────────────────────────

def run_analyze_float(df_processed: pd.DataFrame, df_float: pd.DataFrame) -> pd.DataFrame:
    """
    df_processed: ticker · date · short_interest_shares
    df_float:     ticker · float_shares
    """
    print("\n[ANALYZE B] Short Float %...")

    merged = df_processed.merge(df_float, on="ticker", how="inner")
    merged["value"] = (
        merged["short_interest_shares"] / merged["float_shares"] * 100
    ).round(4)

    new_rows = merged[["ticker", "date", "value"]]

    history  = _load_history(FLOAT_HIST)
    history  = _append_new(history, new_rows)
    history.to_parquet(FLOAT_HIST, index=False)

    snapshot = _flag_ath(history)
    snapshot.to_parquet(LATEST_DIR / "latest_float.parquet", index=False)

    ath_count  = (snapshot["flag"] == "ATH").sum()
    near_count = (snapshot["flag"] == "NEAR_HIGH").sum()
    print(f"   🔴 ATH: {ath_count:,}  |  🟠 Near High: {near_count:,}  |  Total: {len(snapshot):,}")

    return snapshot
