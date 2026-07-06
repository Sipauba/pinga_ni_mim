"""Configuracoes nao sensiveis da notificacao via Evolution API."""

from __future__ import annotations

import re
from collections.abc import Iterable


# O programa dispara uma notificacao quando a queda alcanca cada limiar.
DEFAULT_NOTIFICATION_THRESHOLDS_MINUTES = (1, 15, 30, 60)
NOTIFICATION_THRESHOLDS_MINUTES = DEFAULT_NOTIFICATION_THRESHOLDS_MINUTES
DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS = tuple(
    minute * 60 for minute in DEFAULT_NOTIFICATION_THRESHOLDS_MINUTES
)
NOTIFICATION_THRESHOLDS_SECONDS = DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS

_THRESHOLD_PATTERN = re.compile(
    r"\s*(?P<number>\d+)\s*(?P<unit>[a-zA-Z]+)?\s*(?:[,;]|\s+|$)"
)
_SECONDS_UNITS = {"s", "seg", "segs", "segundo", "segundos", "sec", "secs", "second", "seconds"}
_MINUTES_UNITS = {"m", "min", "mins", "minuto", "minutos", "minute", "minutes"}
_HOURS_UNITS = {"h", "hr", "hrs", "hora", "horas", "hour", "hours"}


def normalize_thresholds_minutes(values: Iterable[int]) -> tuple[int, ...]:
    """Retorna intervalos positivos, unicos e ordenados."""

    normalized = sorted({int(value) for value in values if int(value) > 0})
    if not normalized:
        raise ValueError("Informe pelo menos um intervalo maior que zero.")

    return tuple(normalized)


def normalize_thresholds_seconds(values: Iterable[int]) -> tuple[int, ...]:
    """Retorna intervalos em segundos, positivos, unicos e ordenados."""

    normalized = sorted({int(value) for value in values if int(value) > 0})
    if not normalized:
        raise ValueError("Informe pelo menos um intervalo maior que zero.")

    return tuple(normalized)


def minutes_to_seconds(values: Iterable[int]) -> tuple[int, ...]:
    """Converte intervalos em minutos para segundos."""

    return normalize_thresholds_seconds(int(value) * 60 for value in values)


def parse_thresholds_text(value: str) -> tuple[int, ...]:
    """Converte texto como '5s, 30s, 1m' em segundos."""

    text = value.strip()
    if not text:
        raise ValueError("Informe pelo menos um intervalo maior que zero.")

    values: list[int] = []
    position = 0
    while position < len(text):
        match = _THRESHOLD_PATTERN.match(text, position)
        if match is None:
            raise ValueError(
                "Use numeros com unidade opcional, como 5s, 30s, 1m ou 2h. "
                "Sem unidade, o valor e tratado como minutos."
            )

        number = int(match.group("number"))
        unit = (match.group("unit") or "m").lower()
        values.append(_to_seconds(number, unit))
        position = match.end()

    return normalize_thresholds_seconds(values)


def format_thresholds_text(values: Iterable[int]) -> str:
    """Formata os intervalos para exibicao na interface."""

    return ", ".join(_format_seconds(value) for value in normalize_thresholds_seconds(values))


def _to_seconds(number: int, unit: str) -> int:
    """Converte um numero com unidade para segundos."""

    if unit in _SECONDS_UNITS:
        multiplier = 1
    elif unit in _MINUTES_UNITS:
        multiplier = 60
    elif unit in _HOURS_UNITS:
        multiplier = 3600
    else:
        raise ValueError(
            f"Unidade invalida: {unit}. Use s para segundos, m para minutos ou h para horas."
        )

    return number * multiplier


def _format_seconds(total_seconds: int) -> str:
    """Formata segundos usando uma unidade simples."""

    seconds = int(total_seconds)
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"

    return f"{seconds}s"
