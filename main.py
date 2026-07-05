"""Ponto de entrada da aplicacao de monitoramento de rede."""

from monitor_app import NetworkMonitorApp


def main() -> None:
    """Cria e executa a janela principal."""
    app = NetworkMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
