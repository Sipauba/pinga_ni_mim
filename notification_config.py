"""Configuracoes nao sensiveis da notificacao via Evolution API."""

from __future__ import annotations

import re
from collections.abc import Iterable


# O programa dispara uma notificacao quando a queda alcanca cada limiar.
DEFAULT_NOTIFICATION_THRESHOLDS_MINUTES = (1, 15, 30, 60)
NOTIFICATION_THRESHOLDS_MINUTES = DEFAULT_NOTIFICATION_THRESHOLDS_MINUTES


def normalize_thresholds_minutes(values: Iterable[int]) -> tuple[int, ...]:
    """Retorna intervalos positivos, unicos e ordenados."""

    normalized = sorted({int(value) for value in values if int(value) > 0})
    if not normalized:
        raise ValueError("Informe pelo menos um intervalo maior que zero.")

    return tuple(normalized)


def parse_thresholds_text(value: str) -> tuple[int, ...]:
    """Converte texto como '1, 15, 30' em uma tupla de minutos."""

    parts = [part for part in re.split(r"[,;\s]+", value.strip()) if part]
    if not parts:
        raise ValueError("Informe pelo menos um intervalo maior que zero.")

    try:
        numbers = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("Use apenas numeros inteiros separados por virgula.") from exc

    return normalize_thresholds_minutes(numbers)


def format_thresholds_text(values: Iterable[int]) -> str:
    """Formata os intervalos para exibicao na interface."""

    return ", ".join(str(value) for value in normalize_thresholds_minutes(values))
