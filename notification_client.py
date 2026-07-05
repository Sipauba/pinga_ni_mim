"""Cliente HTTP simples para envio de mensagens pela Evolution API."""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Callable
import urllib.error
import urllib.request


@dataclass(frozen=True)
class NotificationResponse:
    """Resultado resumido de uma tentativa de envio."""

    success: bool
    status_code: int | None
    message: str


class EvolutionApiClient:
    """Envia mensagens de texto para a Evolution API."""

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

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
        """Envia a mensagem em segundo plano para nao travar a interface."""

        def worker() -> None:
            response = self.send_text(number=number, text=text)
            if callback is not None:
                callback(response)

        threading.Thread(target=worker, daemon=True).start()
