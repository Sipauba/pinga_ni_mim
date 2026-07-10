"""Registro em arquivo texto das quedas de conexao."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app_paths import APP_DIR
from ping_monitor import PingResult, target_label


OUTAGE_LOG_FILE = APP_DIR / "quedas_log.txt"


class OutageLogger:
    """Grava inicio e fim de quedas em um arquivo texto."""

    def __init__(self, file_path: Path = OUTAGE_LOG_FILE) -> None:
        self.file_path = file_path

    def log_outage_started(self, result: PingResult) -> None:
        """Registra o momento em que o equipamento parou de responder."""

        try:
            self._ensure_header()
            started_at = result.outage_started_at or result.checked_at
            checked_at = started_at.strftime("%Y-%m-%d %H:%M:%S")
            label = target_label(result.ip_address)
            error = result.error or f"Sem resposta de {label.lower()}"

            self._append_line(
                f"{checked_at} | INICIO_QUEDA | Alvo: {result.name} | "
                f"{label}: {result.ip_address} | Grupo: {result.group} | Mensagem: {error}"
            )
        except OSError as exc:
            print(f"Falha ao registrar inicio de queda: {exc}")

    def log_outage_finished(self, result: PingResult, outage_started_at: datetime) -> None:
        """Registra a recuperacao e a duracao total da queda."""

        try:
            self._ensure_header()
            finished_at = result.checked_at
            duration_seconds = max(0, int((finished_at - outage_started_at).total_seconds()))
            started_at_text = outage_started_at.strftime("%Y-%m-%d %H:%M:%S")
            finished_at_text = finished_at.strftime("%Y-%m-%d %H:%M:%S")
            duration = _format_elapsed_duration(duration_seconds)
            latency = f"{result.latency_ms:.0f} ms" if result.latency_ms is not None else "-"
            label = target_label(result.ip_address)

            self._append_line(
                f"{finished_at_text} | FIM_QUEDA | Alvo: {result.name} | "
                f"{label}: {result.ip_address} | Grupo: {result.group} | Inicio: {started_at_text} | "
                f"Duracao: {duration} | Latencia atual: {latency}"
            )
        except OSError as exc:
            print(f"Falha ao registrar fim de queda: {exc}")

    def _ensure_header(self) -> None:
        """Cria o arquivo com cabecalho caso ele ainda nao exista."""

        if self.file_path.exists():
            return

        self.file_path.write_text(
            "Log de quedas de conexao\n"
            "Formato: data/hora | evento | detalhes\n",
            encoding="utf-8",
        )

    def _append_line(self, line: str) -> None:
        """Adiciona uma linha ao arquivo de log."""

        with self.file_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")


def _format_elapsed_duration(total_seconds: int) -> str:
    """Formata uma duracao em horas, minutos e segundos."""

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []

    if hours:
        parts.append(f"{hours} hora" if hours == 1 else f"{hours} horas")
    if minutes:
        parts.append(f"{minutes} minuto" if minutes == 1 else f"{minutes} minutos")
    if seconds or not parts:
        parts.append(f"{seconds} segundo" if seconds == 1 else f"{seconds} segundos")

    return " e ".join(parts)
