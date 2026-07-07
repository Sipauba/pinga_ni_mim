"""Controle das notificacoes de queda dos equipamentos."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from notification_client import EvolutionApiClient, NotificationResponse
from notification_config import (
    DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS,
    minutes_to_seconds,
    normalize_thresholds_seconds,
)
from outage_logger import OutageLogger
from ping_monitor import PingResult
from secure_settings import NotificationSettings


@dataclass
class OutageState:
    """Estado de queda continua de um equipamento."""

    started_at: datetime
    notified_thresholds: set[int] = field(default_factory=set)


class OutageNotifier:
    """Dispara mensagens quando um equipamento fica offline por tempo definido."""

    def __init__(
        self,
        client: EvolutionApiClient | None = None,
        logger: OutageLogger | None = None,
        settings: NotificationSettings | None = None,
        thresholds_seconds: tuple[int, ...] | None = None,
        thresholds_minutes: tuple[int, ...] | None = None,
    ) -> None:
        self.settings = settings or NotificationSettings()
        self.client = client or EvolutionApiClient()
        self.logger = logger or OutageLogger()
        self.client.update_credentials(self.settings.api_url, self.settings.api_key)
        self.group_number = self.settings.whatsapp_number
        if thresholds_seconds is not None:
            thresholds = thresholds_seconds
        elif thresholds_minutes is not None:
            thresholds = minutes_to_seconds(thresholds_minutes)
        else:
            thresholds = self.settings.thresholds_seconds
        self.thresholds_seconds = normalize_thresholds_seconds(
            thresholds or DEFAULT_NOTIFICATION_THRESHOLDS_SECONDS
        )
        self.group_thresholds_seconds = self._normalize_group_thresholds(
            self.settings.group_thresholds_seconds
        )
        self._outages_by_ip: dict[str, OutageState] = {}

    def update_settings(self, settings: NotificationSettings) -> None:
        """Atualiza as configuracoes usadas nos proximos alertas."""

        self.settings = settings
        self.client.update_credentials(settings.api_url, settings.api_key)
        self.group_number = settings.whatsapp_number
        self.update_thresholds(settings.thresholds_seconds)
        self.update_group_thresholds(settings.group_thresholds_seconds)

    def update_thresholds(self, thresholds_seconds: tuple[int, ...]) -> None:
        """Atualiza os intervalos usados nos proximos alertas."""

        self.thresholds_seconds = normalize_thresholds_seconds(thresholds_seconds)

    def update_group_thresholds(self, group_thresholds_seconds: dict[str, tuple[int, ...]]) -> None:
        """Atualiza os intervalos especificos por grupo."""

        self.group_thresholds_seconds = self._normalize_group_thresholds(group_thresholds_seconds)

    def handle_ping_result(self, result: PingResult) -> None:
        """Atualiza o estado de queda e envia alertas quando necessario."""

        if result.is_online:
            self._handle_recovery(result)
            return

        outage = self._outages_by_ip.get(result.ip_address)
        if outage is None:
            outage = OutageState(started_at=result.outage_started_at or result.checked_at)
            self._outages_by_ip[result.ip_address] = outage
            self.logger.log_outage_started(result)

        elapsed_seconds = (result.checked_at - outage.started_at).total_seconds()
        for threshold in self._thresholds_for_group(result.group):
            if elapsed_seconds >= threshold and threshold not in outage.notified_thresholds:
                outage.notified_thresholds.add(threshold)
                self._send_outage_notification(result, outage.started_at, threshold)

    def clear(self, ip_address: str) -> None:
        """Remove o estado de queda quando o equipamento volta ou e removido."""

        self._outages_by_ip.pop(ip_address, None)

    def _handle_recovery(self, result: PingResult) -> None:
        """Notifica a recuperacao quando uma queda alertada volta ao normal."""

        outage = self._outages_by_ip.pop(result.ip_address, None)
        if outage is None:
            return

        self.logger.log_outage_finished(result, outage.started_at)
        self._send_reached_thresholds(result, outage)

        # Evita aviso de recuperacao para quedas muito curtas que nao chegaram
        # ao primeiro limiar de notificacao.
        if not outage.notified_thresholds:
            return

        self._send_recovery_notification(result, outage.started_at)

    def _send_reached_thresholds(self, result: PingResult, outage: OutageState) -> None:
        """Envia alertas que foram alcancados antes da recuperacao."""

        elapsed_seconds = (result.checked_at - outage.started_at).total_seconds()
        for threshold in self._thresholds_for_group(result.group):
            if elapsed_seconds >= threshold and threshold not in outage.notified_thresholds:
                outage.notified_thresholds.add(threshold)
                self._send_outage_notification(result, outage.started_at, threshold)

    def _thresholds_for_group(self, group: str) -> tuple[int, ...]:
        """Retorna intervalos do grupo ou o padrao global."""

        return self.group_thresholds_seconds.get(group.strip(), self.thresholds_seconds)

    @staticmethod
    def _normalize_group_thresholds(
        group_thresholds_seconds: dict[str, tuple[int, ...]],
    ) -> dict[str, tuple[int, ...]]:
        """Normaliza intervalos especificos por grupo."""

        normalized: dict[str, tuple[int, ...]] = {}
        for group, thresholds in group_thresholds_seconds.items():
            group_name = group.strip()
            if not group_name:
                continue

            normalized[group_name] = normalize_thresholds_seconds(thresholds)

        return normalized

    def _send_outage_notification(
        self,
        result: PingResult,
        outage_started_at: datetime,
        threshold_seconds: int,
    ) -> None:
        """Monta e envia a mensagem de alerta para o grupo do WhatsApp."""

        text = self._build_message(result, outage_started_at, threshold_seconds)
        self.client.send_text_async(
            number=self.group_number,
            text=text,
            callback=self._log_notification_result,
        )

    def _send_recovery_notification(
        self,
        result: PingResult,
        outage_started_at: datetime,
    ) -> None:
        """Monta e envia a mensagem de recuperacao para o grupo."""

        text = self._build_recovery_message(result, outage_started_at)
        self.client.send_text_async(
            number=self.group_number,
            text=text,
            callback=self._log_notification_result,
        )

    @staticmethod
    def _build_message(
        result: PingResult,
        outage_started_at: datetime,
        threshold_seconds: int,
    ) -> str:
        """Cria o texto enviado para o grupo."""

        duration = _format_duration(threshold_seconds)
        started_at = outage_started_at.strftime("%d/%m/%Y %H:%M:%S")
        checked_at = result.checked_at.strftime("%d/%m/%Y %H:%M:%S")
        error = result.error or "Sem resposta ao ping"

        return (
            "ALERTA DE DESCONEXAO\n"
            f"Equipamento: {result.name}\n"
            f"IP: {result.ip_address}\n"
            f"Grupo: {result.group}\n"
            f"Sem resposta ha: {duration}\n"
            f"Inicio da queda: {started_at}\n"
            f"Ultima verificacao: {checked_at}\n"
            f"Mensagem: {error}"
        )

    @staticmethod
    def _build_recovery_message(result: PingResult, outage_started_at: datetime) -> str:
        """Cria o texto enviado quando o equipamento volta a responder."""

        restored_at = result.checked_at
        elapsed_seconds = max(0, int((restored_at - outage_started_at).total_seconds()))
        started_at_text = outage_started_at.strftime("%d/%m/%Y %H:%M:%S")
        restored_at_text = restored_at.strftime("%d/%m/%Y %H:%M:%S")
        duration = _format_elapsed_duration(elapsed_seconds)
        latency = f"{result.latency_ms:.0f} ms" if result.latency_ms is not None else "-"

        return (
            "CONEXAO REESTABELECIDA\n"
            f"Equipamento: {result.name}\n"
            f"IP: {result.ip_address}\n"
            f"Grupo: {result.group}\n"
            f"Tempo fora: {duration}\n"
            f"Inicio da queda: {started_at_text}\n"
            f"Recuperado em: {restored_at_text}\n"
            f"Latencia atual: {latency}"
        )

    @staticmethod
    def _log_notification_result(response: NotificationResponse) -> None:
        """Registra no console quando a Evolution API nao aceitar o envio."""

        if response.success:
            return

        print(
            "Falha ao enviar notificacao pelo WhatsApp "
            f"(status={response.status_code}): {response.message}"
        )


def _format_duration(total_seconds: int) -> str:
    """Formata segundos em texto simples para a mensagem de alerta."""

    seconds = int(total_seconds)
    return _format_elapsed_duration(seconds)


def _format_elapsed_duration(total_seconds: int) -> str:
    """Formata uma duracao real em horas, minutos e segundos."""

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
