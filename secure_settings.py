"""Armazenamento criptografado das configuracoes sensiveis.

No Windows, este modulo usa DPAPI. O arquivo gerado so pode ser descriptografado
pelo mesmo usuario do Windows que salvou as configuracoes.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path

from app_paths import APP_DIR


SETTINGS_FILE = APP_DIR / "configuracoes_sensiveis.dat"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


@dataclass(frozen=True)
class NotificationSettings:
    """Configuracoes usadas para enviar alertas pelo WhatsApp."""

    api_url: str = ""
    api_key: str = ""
    whatsapp_number: str = ""

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
        )

    def save(self, settings: NotificationSettings) -> None:
        """Criptografa e grava as configuracoes no disco."""

        payload = {
            "api_url": settings.api_url,
            "api_key": settings.api_key,
            "whatsapp_number": settings.whatsapp_number,
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
