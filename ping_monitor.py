"""Logica de monitoramento usada pela interface grafica.

Este modulo nao conhece Tkinter. Ele apenas executa verificacoes em segundo
plano e entrega os resultados para uma funcao de callback. Enderecos IP/hosts
sao monitorados por ping ICMP; URLs http/https sao monitoradas por requisicao
HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import platform
import re
import subprocess
import threading
import time
from typing import Callable
import urllib.error
import urllib.request
from urllib.parse import urlparse


@dataclass(frozen=True)
class PingResult:
    """Resultado de uma tentativa de monitoramento."""

    name: str
    ip_address: str
    group: str
    is_online: bool
    latency_ms: float | None
    checked_at: datetime
    error: str | None = None
    outage_started_at: datetime | None = None


class EquipmentMonitor:
    """Executa monitoramento periodico para um alvo especifico."""

    def __init__(
        self,
        name: str,
        ip_address: str,
        group: str,
        result_callback: Callable[[PingResult], None],
        interval_seconds: float = 1.0,
        timeout_ms: int = 1000,
    ) -> None:
        self.name = name
        self.ip_address = ip_address
        self.group = group
        self.result_callback = result_callback
        self.interval_seconds = interval_seconds
        self.timeout_ms = timeout_ms

        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Inicia o monitoramento em uma thread separada."""
        self._thread.start()

    def stop(self, wait: bool = False) -> None:
        """Solicita a parada do monitoramento.

        O parametro ``wait`` permite aguardar a thread encerrar. A interface usa
        ``wait=False`` para nao travar a janela enquanto uma verificacao esta em curso.
        """

        self._stop_event.set()
        if wait and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        """Loop principal: verifica, envia resultado e aguarda o proximo ciclo."""

        while not self._stop_event.is_set():
            started_at = time.monotonic()
            is_online, latency_ms, error = check_once(self.ip_address, self.timeout_ms)

            self.result_callback(
                PingResult(
                    name=self.name,
                    ip_address=self.ip_address,
                    group=self.group,
                    is_online=is_online,
                    latency_ms=latency_ms,
                    checked_at=datetime.now(),
                    error=error,
                )
            )

            elapsed = time.monotonic() - started_at
            wait_time = max(0.0, self.interval_seconds - elapsed)
            self._stop_event.wait(wait_time)


def check_once(target: str, timeout_ms: int = 1000) -> tuple[bool, float | None, str | None]:
    """Executa uma verificacao unica para IP/host ou URL."""

    if is_http_url(target):
        return http_check_once(target, timeout_ms)

    return ping_once(target, timeout_ms)


def ping_once(ip_address: str, timeout_ms: int = 1000) -> tuple[bool, float | None, str | None]:
    """Executa um unico ping e retorna status, latencia e erro resumido."""

    system_name = platform.system().lower()

    if "windows" in system_name:
        command = ["ping", "-n", "1", "-w", str(timeout_ms), ip_address]
    else:
        timeout_seconds = max(1, round(timeout_ms / 1000))
        command = ["ping", "-c", "1", "-W", str(timeout_seconds), ip_address]

    run_options = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": (timeout_ms / 1000) + 1,
    }

    if "windows" in system_name and hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_options["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        completed = subprocess.run(command, **run_options)
    except subprocess.TimeoutExpired:
        return False, None, "Tempo limite excedido"
    except OSError as exc:
        return False, None, str(exc)

    output = f"{completed.stdout}\n{completed.stderr}"
    latency_ms = _extract_latency_ms(output)
    is_online = _is_successful_ping(output, latency_ms, completed.returncode)
    error = None if is_online else _summarize_error(output)

    return is_online, latency_ms, error


def http_check_once(url: str, timeout_ms: int = 1000) -> tuple[bool, float | None, str | None]:
    """Verifica uma URL HTTP/HTTPS e retorna status, latencia e erro resumido."""

    timeout_seconds = max(0.1, timeout_ms / 1000)
    started_at = time.monotonic()

    try:
        status_code, reason = _request_http_status(url, timeout_seconds, method="HEAD")
        if status_code in _HTTP_METHOD_NOT_ALLOWED_STATUSES:
            status_code, reason = _request_http_status(url, timeout_seconds, method="GET")
    except TimeoutError:
        return False, None, "Tempo limite excedido"
    except urllib.error.URLError as exc:
        return False, None, _summarize_url_error(exc.reason)
    except OSError as exc:
        return False, None, str(exc)[:120]

    latency_ms = (time.monotonic() - started_at) * 1000
    is_online = _is_successful_http_status(status_code)
    error = None if is_online else _summarize_http_status(status_code, reason)

    return is_online, latency_ms, error


def is_http_url(value: str) -> bool:
    """Indica se o alvo e uma URL http/https completa."""

    parsed = urlparse(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def target_label(value: str) -> str:
    """Retorna o rotulo usado em logs e alertas para o alvo monitorado."""

    return "URL" if is_http_url(value) else "IP/Host"


def _request_http_status(url: str, timeout_seconds: float, method: str) -> tuple[int, str]:
    """Executa a requisicao HTTP e devolve status mesmo em respostas 4xx/5xx."""

    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "PingaNiMim/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.reason
    except urllib.error.HTTPError as exc:
        reason = getattr(exc, "reason", "") or getattr(exc, "msg", "") or "Erro HTTP"
        exc.close()
        return exc.code, str(reason)


def _is_successful_http_status(status_code: int) -> bool:
    """Define quais respostas HTTP indicam servico disponivel.

    401/403 costumam aparecer em servicos protegidos por login ou firewall de
    aplicacao. 429 tambem indica que o servico respondeu, apesar do limite.
    """

    return 200 <= status_code < 400 or status_code in _HTTP_AUTH_OR_LIMIT_STATUSES


def _summarize_http_status(status_code: int, reason: str) -> str:
    """Resume uma resposta HTTP considerada indisponivel."""

    reason_text = str(reason).strip()
    if reason_text:
        return f"HTTP {status_code} {reason_text}"[:120]

    return f"HTTP {status_code}"


def _summarize_url_error(reason: object) -> str:
    """Resume erros de conexao, DNS, TLS e timeout."""

    message = str(reason).strip()
    return (message or "Falha ao acessar URL")[:120]


def _is_successful_ping(
    output: str,
    latency_ms: float | None,
    returncode: int,
) -> bool:
    """Confere se o ping teve uma resposta real do equipamento.

    Em algumas situacoes o ``ping.exe`` do Windows pode retornar sucesso mesmo
    quando o texto indica falha, por exemplo "host de destino inacessivel". Por
    isso a aplicacao so considera online quando existe latencia na resposta e
    nenhuma mensagem conhecida de erro aparece no texto.
    """

    if returncode != 0 or latency_ms is None:
        return False

    normalized_output = output.lower()

    return not any(marker in normalized_output for marker in _PING_FAILURE_MARKERS)


def _extract_latency_ms(output: str) -> float | None:
    """Extrai a latencia do texto retornado pelo ping.

    O Windows pode retornar ``time=`` em ingles ou ``tempo=`` em portugues,
    dependendo do idioma do sistema.
    """

    match = re.search(r"(?:time|tempo)[=<]\s*([\d,.]+)\s*ms", output, re.IGNORECASE)
    if not match:
        return None

    value = match.group(1).replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _summarize_error(output: str) -> str:
    """Retorna uma mensagem curta usando a ultima linha util do ping."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "Sem resposta"

    for line in lines:
        normalized_line = line.lower()
        if any(marker in normalized_line for marker in _PING_FAILURE_MARKERS):
            return line[:120]

    return lines[-1][:120]


_PING_FAILURE_MARKERS = (
    "destination host unreachable",
    "destination net unreachable",
    "general failure",
    "request timed out",
    "transmit failed",
    "100% loss",
    "host de destino inacess",
    "rede de destino inacess",
    "falha geral",
    "tempo esgotado",
    "esgotado",
    "100% de perda",
)

_HTTP_AUTH_OR_LIMIT_STATUSES = {401, 403, 429}
_HTTP_METHOD_NOT_ALLOWED_STATUSES = {405, 501}
