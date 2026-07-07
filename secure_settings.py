"""Armazenamento criptografado das configuracoes sensiveis.

No Windows, este modulo usa DPAPI. O arquivo gerado so pode ser descriptografado
pelo mesmo usuario do Windows que salvou as configuracoes.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from app_paths import APP_DIR
from notification_config import (
    DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS,
    minutes_to_seconds,
    normalize_thresholds_seconds,
)


SETTINGS_FILE = APP_DIR / "configuracoes_sensiveis.dat"
CRYPTPROTECT_UI_FORBIDDEN = 0x01
DEFAULT_OFFLINE_FAILURE_THRESHOLD = 3
DEFAULT_FLAPPING_TRANSITION_COUNT = 4
DEFAULT_FLAPPING_WINDOW_MINUTES = 10


@dataclass(frozen=True)
class NotificationSettings:
    """Configuracoes usadas para enviar alertas pelo WhatsApp."""

    api_url: str = ""
    api_key: str = ""
    whatsapp_number: str = ""
    thresholds_seconds: tuple[int, ...] = DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS
    group_thresholds_seconds: dict[str, tuple[int, ...]] = field(default_factory=dict)
    offline_failure_threshold: int = DEFAULT_OFFLINE_FAILURE_THRESHOLD
    flapping_transition_count: int = DEFAULT_FLAPPING_TRANSITION_COUNT
    flapping_window_minutes: int = DEFAULT_FLAPPING_WINDOW_MINUTES

    def is_complete(self) -> bool:
        """Indica se todos os campos obrigatorios foram preenchidos."""

        return bool(self.api_url and self.api_key and self.whatsapp_number)


class SettingsStorageError(Exception):
    """Erro ao salvar ou carregar configuracoes criptografadas."""


class SecureSettingsStore:
    """Salva e carrega configuracoes sensiveis em arquivo criptografado."""

    def __init__(self, file_path: Path = SETTINGS_FILE) -> None:
        self.file_path = file_path

    def load(self) -> NotificationSettings:
        """Carrega as configuracoes criptografadas, se existirem."""

        if not self.file_path.exists():
            return NotificationSettings()

        try:
            encrypted_data = self.file_path.read_bytes()
            if not encrypted_data:
                return NotificationSettings()

            plain_data = _dpapi_decrypt(encrypted_data)
            payload = json.loads(plain_data.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SettingsStorageError(
                "Nao foi possivel carregar as configuracoes criptografadas."
            ) from exc

        return NotificationSettings(
            api_url=str(payload.get("api_url", "")).strip(),
            api_key=str(payload.get("api_key", "")).strip(),
            whatsapp_number=str(payload.get("whatsapp_number", "")).strip(),
            thresholds_seconds=_thresholds_from_payload(payload),
            group_thresholds_seconds=_group_thresholds_from_payload(payload),
            offline_failure_threshold=_positive_int_from_payload(
                payload,
                "offline_failure_threshold",
                DEFAULT_OFFLINE_FAILURE_THRESHOLD,
            ),
            flapping_transition_count=_positive_int_from_payload(
                payload,
                "flapping_transition_count",
                DEFAULT_FLAPPING_TRANSITION_COUNT,
            ),
            flapping_window_minutes=_positive_int_from_payload(
                payload,
                "flapping_window_minutes",
                DEFAULT_FLAPPING_WINDOW_MINUTES,
            ),
        )

    def save(self, settings: NotificationSettings) -> None:
        """Criptografa e grava as configuracoes no disco."""

        payload = {
            "api_url": settings.api_url,
            "api_key": settings.api_key,
            "whatsapp_number": settings.whatsapp_number,
            "thresholds_seconds": list(normalize_thresholds_seconds(settings.thresholds_seconds)),
            "group_thresholds_seconds": {
                group: list(normalize_thresholds_seconds(thresholds))
                for group, thresholds in settings.group_thresholds_seconds.items()
                if group.strip()
            },
            "offline_failure_threshold": int(settings.offline_failure_threshold),
            "flapping_transition_count": int(settings.flapping_transition_count),
            "flapping_window_minutes": int(settings.flapping_window_minutes),
        }
        plain_data = json.dumps(payload, ensure_ascii=True).encode("utf-8")

        try:
            encrypted_data = _dpapi_encrypt(plain_data)
            self.file_path.write_bytes(encrypted_data)
        except OSError as exc:
            raise SettingsStorageError(
                "Nao foi possivel salvar as configuracoes criptografadas."
            ) from exc


class _DataBlob(ctypes.Structure):
    """Estrutura DATA_BLOB usada pelas funcoes da DPAPI."""

    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(wintypes.BYTE)),
    ]


def _dpapi_encrypt(data: bytes) -> bytes:
    """Criptografa bytes usando a DPAPI do Windows."""

    _ensure_windows()
    crypt32, kernel32 = _configure_dpapi()
    input_blob, input_buffer = _bytes_to_blob(data)
    output_blob = _DataBlob()

    try:
        success = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "pinga_ni_mim",
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        _ = input_buffer

        if not success:
            raise ctypes.WinError()

        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)


def _dpapi_decrypt(data: bytes) -> bytes:
    """Descriptografa bytes usando a DPAPI do Windows."""

    _ensure_windows()
    crypt32, kernel32 = _configure_dpapi()
    input_blob, input_buffer = _bytes_to_blob(data)
    output_blob = _DataBlob()

    try:
        success = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        _ = input_buffer

        if not success:
            raise ctypes.WinError()

        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)


def _bytes_to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    """Converte bytes em DATA_BLOB mantendo o buffer vivo durante a chamada."""

    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(
        cbData=len(data),
        pbData=ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE)),
    )
    return blob, buffer


def _configure_dpapi() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """Configura assinaturas basicas das funcoes usadas da DPAPI."""

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    return crypt32, kernel32


def _ensure_windows() -> None:
    """Garante que a criptografia esteja disponivel no sistema atual."""

    if os.name != "nt":
        raise SettingsStorageError("A criptografia DPAPI esta disponivel apenas no Windows.")


def _thresholds_from_payload(payload: dict[str, object]) -> tuple[int, ...]:
    """Carrega intervalos salvos ou usa o padrao quando nao existirem."""

    raw_seconds = payload.get("thresholds_seconds")
    if isinstance(raw_seconds, list):
        try:
            return normalize_thresholds_seconds(int(value) for value in raw_seconds)
        except (TypeError, ValueError):
            return DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS

    raw_values = payload.get("thresholds_minutes")
    if not isinstance(raw_values, list):
        return DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS

    try:
        return minutes_to_seconds(int(value) for value in raw_values)
    except (TypeError, ValueError):
        return DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS


def _group_thresholds_from_payload(payload: dict[str, object]) -> dict[str, tuple[int, ...]]:
    """Carrega intervalos especificos por grupo."""

    raw_groups = payload.get("group_thresholds_seconds")
    if not isinstance(raw_groups, dict):
        return {}

    group_thresholds: dict[str, tuple[int, ...]] = {}
    for raw_group, raw_thresholds in raw_groups.items():
        group = str(raw_group).strip()
        if not group or not isinstance(raw_thresholds, list):
            continue

        try:
            group_thresholds[group] = normalize_thresholds_seconds(
                int(value) for value in raw_thresholds
            )
        except (TypeError, ValueError):
            continue

    return group_thresholds


def _positive_int_from_payload(
    payload: dict[str, object],
    key: str,
    default: int,
) -> int:
    """Carrega um inteiro positivo da configuracao."""

    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError):
        return default

    return value if value > 0 else default
