"""Leitura e gravacao dos equipamentos monitorados em arquivo texto."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import math
from pathlib import Path

from app_paths import APP_DIR


EQUIPMENT_FILE = APP_DIR / "equipamentos.txt"
DEFAULT_EQUIPMENT_GROUP = "Sem grupo"
DEFAULT_PING_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class EquipmentRecord:
    """Registro salvo no arquivo de equipamentos."""

    name: str
    ip_address: str
    group: str = DEFAULT_EQUIPMENT_GROUP
    ping_interval_seconds: float = DEFAULT_PING_INTERVAL_SECONDS


class EquipmentStore:
    """Persistencia simples em arquivo texto editavel pelo Bloco de Notas."""

    def __init__(self, file_path: Path = EQUIPMENT_FILE) -> None:
        self.file_path = file_path
        self.ensure_file_exists()

    def ensure_file_exists(self) -> None:
        """Cria o arquivo com um pequeno cabecalho caso ele ainda nao exista."""

        if self.file_path.exists():
            return

        self.file_path.write_text(
            "# Equipamentos monitorados\n"
            "# Formato: nome;endereco;grupo;intervalo_monitoramento_segundos\n",
            encoding="utf-8",
        )

    def load(self) -> list[EquipmentRecord]:
        """Carrega os equipamentos salvos no arquivo texto."""

        self.ensure_file_exists()
        records: list[EquipmentRecord] = []

        with self.file_path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.reader(file, delimiter=";")
            for row in reader:
                if not row:
                    continue

                first_column = row[0].strip()
                if not first_column or first_column.startswith("#"):
                    continue

                if len(row) < 2:
                    continue

                name = first_column
                ip_address = row[1].strip()
                group = row[2].strip() if len(row) >= 3 else DEFAULT_EQUIPMENT_GROUP
                group = group or DEFAULT_EQUIPMENT_GROUP
                ping_interval_seconds = (
                    _parse_ping_interval(row[3])
                    if len(row) >= 4
                    else DEFAULT_PING_INTERVAL_SECONDS
                )
                if name and ip_address:
                    records.append(
                        EquipmentRecord(
                            name=name,
                            ip_address=ip_address,
                            group=group,
                            ping_interval_seconds=ping_interval_seconds,
                        )
                    )

        return records

    def save(self, records: list[EquipmentRecord]) -> None:
        """Regrava o arquivo com a lista atual de equipamentos."""

        self.ensure_file_exists()

        with self.file_path.open("w", encoding="utf-8", newline="") as file:
            file.write("# Equipamentos monitorados\n")
            file.write("# Formato: nome;endereco;grupo;intervalo_monitoramento_segundos\n")

            writer = csv.writer(file, delimiter=";", lineterminator="\n")
            for record in records:
                group = record.group.strip() or DEFAULT_EQUIPMENT_GROUP
                writer.writerow(
                    [
                        record.name,
                        record.ip_address,
                        group,
                        _format_ping_interval(record.ping_interval_seconds),
                    ]
                )


def _parse_ping_interval(value: str) -> float:
    """Le o intervalo salvo, caindo no padrao se estiver invalido."""

    try:
        interval = float(value.strip().replace(",", "."))
    except ValueError:
        return DEFAULT_PING_INTERVAL_SECONDS

    if not math.isfinite(interval) or interval <= 0:
        return DEFAULT_PING_INTERVAL_SECONDS

    return interval


def _format_ping_interval(value: float) -> str:
    """Formata o intervalo sem casas decimais desnecessarias."""

    interval = float(value)
    if interval.is_integer():
        return str(int(interval))

    return f"{interval:.2f}".rstrip("0").rstrip(".")
