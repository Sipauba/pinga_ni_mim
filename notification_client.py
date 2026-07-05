"""Cliente HTTP simples para envio de mensagens pela Evolution API."""

from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import threading
import time
from typing import Callable
import urllib.error
import urllib.request


@dataclass(frozen=True)
class NotificationResponse:
    """Resultado resumido de uma tentativa de envio."""

    success: bool
    status_code: int | None
    message: str


@dataclass(frozen=True)
class _NotificationTask:
    """Mensagem pendente para envio em segundo plano."""

    number: str
    text: str
    callback: Callable[[NotificationResponse], None] | None


class EvolutionApiClient:
    """Envia mensagens de texto para a Evolution API."""

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        timeout_seconds: float = 10.0,
        send_delay_seconds: float = 0.4,
        max_retries: int = 2,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.send_delay_seconds = send_delay_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self._pending_messages: queue.Queue[_NotificationTask] = queue.Queue()
        self._worker_lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None

    def update_credentials(self, api_url: str, api_key: str) -> None:
        """Atualiza endpoint e chave usados nos proximos envios."""

        self.api_url = api_url
        self.api_key = api_key

    def send_text(self, number: str, text: str) -> NotificationResponse:
        """Envia uma mensagem de texto e retorna o resultado da requisicao."""

        if not self.api_url:
            return NotificationResponse(
                success=False,
                status_code=None,
                message="URL do endpoint nao configurada",
            )

        if not self.api_key:
            return NotificationResponse(
                success=False,
                status_code=None,
                message="Chave da API nao configurada",
            )

        if not number:
            return NotificationResponse(
                success=False,
                status_code=None,
                message="Numero ou grupo nao configurado",
            )

        payload = json.dumps({"number": number, "text": text}).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=payload,
            method="POST",
            headers={
                "apikey": self.api_key,
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                return NotificationResponse(
                    success=True,
                    status_code=response.status,
                    message=body[:250],
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return NotificationResponse(
                success=False,
                status_code=exc.code,
                message=body[:250] or str(exc),
            )
        except urllib.error.URLError as exc:
            return NotificationResponse(
                success=False,
                status_code=None,
                message=str(exc.reason),
            )
        except OSError as exc:
            return NotificationResponse(
                success=False,
                status_code=None,
                message=str(exc),
            )

    def send_text_async(
        self,
        number: str,
        text: str,
        callback: Callable[[NotificationResponse], None] | None = None,
    ) -> None:
        """Enfileira a mensagem em segundo plano para nao travar a interface."""

        self._pending_messages.put(
            _NotificationTask(number=number, text=text, callback=callback)
        )
        self._ensure_worker_running()

    def _ensure_worker_running(self) -> None:
        """Inicia o trabalhador da fila quando ainda nao existir um ativo."""

        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return

            self._worker_thread = threading.Thread(target=self._run_worker, daemon=True)
            self._worker_thread.start()

    def _run_worker(self) -> None:
        """Processa alertas em ordem para evitar disputas na API."""

        while True:
            task = self._pending_messages.get()
            try:
                try:
                    response = self._send_text_with_retries(task)
                except Exception as exc:  # pragma: no cover - protecao da thread
                    response = NotificationResponse(
                        success=False,
                        status_code=None,
                        message=str(exc),
                    )
                    print(f"Falha inesperada ao enviar notificacao: {exc}")

                if task.callback is not None:
                    try:
                        task.callback(response)
                    except Exception as exc:  # pragma: no cover - protecao da thread
                        print(f"Falha no callback da notificacao: {exc}")
            finally:
                self._pending_messages.task_done()

            if self.send_delay_seconds > 0:
                time.sleep(self.send_delay_seconds)

    def _send_text_with_retries(self, task: _NotificationTask) -> NotificationResponse:
        """Tenta reenviar falhas transitarias antes de devolver o resultado."""

        last_response = NotificationResponse(
            success=False,
            status_code=None,
            message="Envio nao realizado",
        )

        for attempt in range(self.max_retries + 1):
            last_response = self.send_text(number=task.number, text=task.text)
            if (
                last_response.success
                or not self._should_retry(last_response)
                or attempt >= self.max_retries
            ):
                return last_response

            time.sleep(self.retry_delay_seconds)

        return last_response

    @staticmethod
    def _should_retry(response: NotificationResponse) -> bool:
        """Indica se a falha parece temporaria."""

        normalized_message = response.message.lower()
        if "nao configurad" in normalized_message:
            return False

        if response.status_code is None:
            return True

        return response.status_code in {408, 429} or response.status_code >= 500
