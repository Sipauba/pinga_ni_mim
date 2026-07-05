"""Caminhos usados pela aplicacao.

Quando o programa roda como executavel PyInstaller, os arquivos locais devem
ficar ao lado do .exe. Quando roda como script, ficam na pasta do projeto.
"""

from __future__ import annotations

from pathlib import Path
import sys


def get_app_directory() -> Path:
    """Retorna a pasta onde os arquivos locais devem ser gravados."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


APP_DIR = get_app_directory()
