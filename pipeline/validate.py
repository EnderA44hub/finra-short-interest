"""
validate.py — Verifica que el DataFrame descargado de FINRA sea válido
antes de continuar el pipeline.
"""

import pandas as pd

REQUIRED_COLUMNS  = {"ticker", "date", "short_interest_shares"}
MIN_ROWS_EXPECTED = 5_000       # si FINRA devuelve menos, algo falló


class ValidationError(Exception):
    pass


def run_validate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Valida el DataFrame crudo.
    Retorna el mismo df si todo está bien, lanza ValidationError si no.
    """
    print("\n[VALIDATE] Verificando calidad del dato...")

    errors = []

    # 1. Columnas requeridas
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        errors.append(f"Columnas faltantes: {missing}")

    # 2. Mínimo de filas
    if len(df) < MIN_ROWS_EXPECTED:
        errors.append(f"Solo {len(df):,} filas — se esperaban al menos {MIN_ROWS_EXPECTED:,}")

    # 3. Nulos en columnas críticas
    for col in REQUIRED_COLUMNS:
        if col in df.columns:
            null_count = df[col].isna().sum()
            if null_count > 0:
                errors.append(f"Nulos en '{col}': {null_count:,}")

    # 4. short_interest_shares no puede ser negativo
    if "short_interest_shares" in df.columns:
        neg = (df["short_interest_shares"] < 0).sum()
        if neg > 0:
            errors.append(f"Valores negativos en short_interest_shares: {neg:,}")

    if errors:
        msg = "\n   ✗ ".join([""] + errors)
        raise ValidationError(f"Validación fallida:{msg}")

    print(f"   ✓ {len(df):,} filas — esquema OK — sin nulos críticos")
    return df
