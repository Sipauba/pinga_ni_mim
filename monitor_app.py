"""Interface Tkinter para monitorar equipamentos de rede por ping."""

from __future__ import annotations

import ipaddress
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlparse

from equipment_store import EquipmentRecord, EquipmentStore
from outage_notifier import OutageNotifier
from ping_monitor import EquipmentMonitor, PingResult
from secure_settings import NotificationSettings, SecureSettingsStore, SettingsStorageError


class NetworkMonitorApp(tk.Tk):
    """Janela principal do monitor de equipamentos."""

    def __init__(self) -> None:
        super().__init__()

        self.title("Monitor de Equipamentos na Rede")
        self.geometry("850x500")
        self.minsize(720, 420)

        # A fila recebe resultados vindos das threads de ping. O Tkinter so deve
        # ser atualizado pela thread principal, por isso a UI consulta essa fila.
        self._result_queue: queue.Queue[PingResult] = queue.Queue()

        self._monitors: dict[str, EquipmentMonitor] = {}
        self._items_by_ip: dict[str, str] = {}
        self._ip_by_item: dict[str, str] = {}
        self._last_status: dict[str, bool] = {}
        self._store = EquipmentStore()
        self._settings_store = SecureSettingsStore()
        self._settings_load_error: str | None = None
        self._notification_settings = self._load_notification_settings()
        self._outage_notifier = OutageNotifier(settings=self._notification_settings)

        self.name_var = tk.StringVar()
        self.ip_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="Nenhum equipamento monitorado")
        self.api_url_var = tk.StringVar(value=self._notification_settings.api_url)
        self.whatsapp_number_var = tk.StringVar(value=self._notification_settings.whatsapp_number)
        self.api_key_var = tk.StringVar(value=self._notification_settings.api_key)
        self.show_api_key_var = tk.BooleanVar(value=False)
        self.settings_status_var = tk.StringVar(value=self._build_settings_status())

        self._configure_style()
        self._build_layout()
        self._load_saved_equipment()
        self._schedule_queue_processing()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        """Configura estilos basicos dos widgets ttk."""

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("TFrame", background="#f7f8fa")
        style.configure("TLabel", background="#f7f8fa", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Summary.TLabel", foreground="#4b5563")
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    def _build_layout(self) -> None:
        """Cria os campos, botoes e tabela da aplicacao."""

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(container)
        notebook.grid(row=0, column=0, sticky="nsew")

        root = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(root, text="Monitoramento")
        notebook.add(settings_tab, text="Configuracoes")

        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Monitor de Equipamentos", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.summary_var, style="Summary.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )

        form = ttk.LabelFrame(root, text="Novo equipamento", padding=12)
        form.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="Nome").grid(row=0, column=0, sticky="w", padx=(0, 8))
        name_entry = ttk.Entry(form, textvariable=self.name_var)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))

        ttk.Label(form, text="IP").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ip_entry = ttk.Entry(form, textvariable=self.ip_var)
        ip_entry.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ip_entry.bind("<Return>", lambda _event: self._add_equipment())

        ttk.Button(form, text="Adicionar", command=self._add_equipment).grid(
            row=0, column=4, sticky="ew"
        )

        table_frame = ttk.Frame(root)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("name", "ip", "status", "latency", "checked_at", "error")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Equipamento")
        self.tree.heading("ip", text="IP")
        self.tree.heading("status", text="Status")
        self.tree.heading("latency", text="Latencia")
        self.tree.heading("checked_at", text="Ultima leitura")
        self.tree.heading("error", text="Mensagem")

        self.tree.column("name", width=180, minwidth=120)
        self.tree.column("ip", width=130, minwidth=110)
        self.tree.column("status", width=100, minwidth=90, anchor="center")
        self.tree.column("latency", width=90, minwidth=80, anchor="center")
        self.tree.column("checked_at", width=110, minwidth=100, anchor="center")
        self.tree.column("error", width=260, minwidth=160)

        self.tree.tag_configure("online", foreground="#137333")
        self.tree.tag_configure("offline", foreground="#b3261e")
        self.tree.tag_configure("waiting", foreground="#5f6368")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(root)
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)

        ttk.Button(actions, text="Remover selecionado", command=self._remove_selected).grid(
            row=0, column=1, sticky="e"
        )

        self._build_settings_tab(settings_tab)
        name_entry.focus_set()

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        """Cria a aba com as configuracoes da Evolution API."""

        parent.columnconfigure(0, weight=1)

        form = ttk.LabelFrame(parent, text="WhatsApp / Evolution API", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="URL do endpoint").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(form, textvariable=self.api_url_var).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=(0, 10),
        )

        ttk.Label(form, text="Numero ou grupo").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(form, textvariable=self.whatsapp_number_var).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(0, 10),
        )

        ttk.Label(form, text="Chave da API").grid(row=2, column=0, sticky="w", padx=(0, 8))
        self.api_key_entry = ttk.Entry(form, textvariable=self.api_key_var, show="*")
        self.api_key_entry.grid(row=2, column=1, sticky="ew", pady=(0, 10))

        ttk.Checkbutton(
            form,
            text="Mostrar chave",
            variable=self.show_api_key_var,
            command=self._toggle_api_key_visibility,
        ).grid(row=3, column=1, sticky="w", pady=(0, 10))

        actions = ttk.Frame(form)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure(0, weight=1)

        ttk.Label(actions, textvariable=self.settings_status_var, style="Summary.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Button(
            actions,
            text="Salvar configuracoes",
            command=self._save_notification_settings,
        ).grid(row=0, column=1, sticky="e")

        help_frame = ttk.LabelFrame(parent, text="Onde encontrar essas informacoes", padding=12)
        help_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        help_frame.columnconfigure(0, weight=1)

        help_text = (
            "URL do endpoint: use o endpoint de envio de texto da Evolution API. "
            "O formato costuma ser https://SEU_SERVIDOR/message/sendText/NOME_DA_INSTANCIA.\n\n"
            "Chave da API: use a apikey/chave da instancia no painel da Evolution API.\n\n"
            "Numero ou grupo: para telefone, informe o numero com DDI e DDD. Para grupo, use o JID "
            "terminado em @g.us. Uma forma pratica de descobrir o grupo e chamar na Evolution API: "
            "GET /group/fetchAllGroups/NOME_DA_INSTANCIA?getParticipants=false com o header apikey. "
            "Copie o campo id do grupo desejado."
        )
        ttk.Label(help_frame, text=help_text, justify="left", wraplength=760).grid(
            row=0,
            column=0,
            sticky="ew",
        )

    def _load_notification_settings(self) -> NotificationSettings:
        """Carrega as configuracoes criptografadas sem interromper a abertura."""

        try:
            return self._settings_store.load()
        except SettingsStorageError as exc:
            self._settings_load_error = str(exc)
            return NotificationSettings()

    def _build_settings_status(self) -> str:
        """Monta o texto de status exibido na aba de configuracoes."""

        if self._settings_load_error:
            return "Falha ao carregar configuracoes salvas."

        if self._notification_settings.is_complete():
            return "Configuracoes carregadas."

        return "Configuracoes de notificacao nao preenchidas."

    def _toggle_api_key_visibility(self) -> None:
        """Alterna entre mostrar e ocultar a chave da API."""

        self.api_key_entry.configure(show="" if self.show_api_key_var.get() else "*")

    def _save_notification_settings(self) -> None:
        """Valida, criptografa e salva as configuracoes de notificacao."""

        settings = NotificationSettings(
            api_url=self.api_url_var.get().strip(),
            api_key=self.api_key_var.get().strip(),
            whatsapp_number=self.whatsapp_number_var.get().strip(),
        )

        if not settings.is_complete():
            messagebox.showwarning(
                "Configuracoes incompletas",
                "Preencha a URL do endpoint, o numero/grupo e a chave da API.",
            )
            return

        if not self._is_valid_url(settings.api_url):
            messagebox.showwarning(
                "URL invalida",
                "Informe uma URL completa, com http:// ou https://.",
            )
            return

        try:
            self._settings_store.save(settings)
        except SettingsStorageError as exc:
            messagebox.showerror("Erro ao salvar", str(exc))
            return

        self._notification_settings = settings
        self._settings_load_error = None
        self._outage_notifier.update_settings(settings)
        self.settings_status_var.set("Configuracoes salvas com criptografia local.")
        messagebox.showinfo("Configuracoes salvas", "As notificacoes foram configuradas.")

    def _add_equipment(self) -> None:
        """Valida os campos e inicia o monitoramento do equipamento."""

        name = self.name_var.get().strip()
        ip_address = self.ip_var.get().strip()

        if not name or not ip_address:
            messagebox.showwarning("Campos obrigatorios", "Informe o nome e o IP do equipamento.")
            return

        if not self._is_valid_ip(ip_address):
            messagebox.showwarning("IP invalido", "Informe um endereco IPv4 ou IPv6 valido.")
            return

        if ip_address in self._monitors:
            messagebox.showwarning("IP duplicado", "Esse IP ja esta sendo monitorado.")
            return

        self._start_monitoring(name, ip_address)
        self._save_equipment_list()

        self.name_var.set("")
        self.ip_var.set("")
        self._update_summary()

    def _remove_selected(self) -> None:
        """Para o monitoramento e remove a linha selecionada."""

        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Remover equipamento", "Selecione um equipamento na tabela.")
            return

        item_id = selection[0]
        ip_address = self._ip_by_item.pop(item_id)

        monitor = self._monitors.pop(ip_address)
        monitor.stop(wait=False)

        self._items_by_ip.pop(ip_address, None)
        self._last_status.pop(ip_address, None)
        self._outage_notifier.clear(ip_address)
        self.tree.delete(item_id)
        self._save_equipment_list()
        self._update_summary()

    def _load_saved_equipment(self) -> None:
        """Carrega do arquivo os equipamentos salvos anteriormente."""

        invalid_records: list[EquipmentRecord] = []
        duplicated_ips: list[str] = []

        for record in self._store.load():
            if not self._is_valid_ip(record.ip_address):
                invalid_records.append(record)
                continue

            if record.ip_address in self._monitors:
                duplicated_ips.append(record.ip_address)
                continue

            self._start_monitoring(record.name, record.ip_address)

        self._update_summary()

        if invalid_records or duplicated_ips:
            messagebox.showwarning(
                "Equipamentos ignorados",
                "Algumas linhas do arquivo equipamentos.txt foram ignoradas "
                "por IP invalido ou duplicado.",
            )

    def _start_monitoring(self, name: str, ip_address: str) -> None:
        """Cria a linha na tabela e inicia o ping periodico."""

        monitor = EquipmentMonitor(
            name=name,
            ip_address=ip_address,
            result_callback=self._result_queue.put,
        )
        monitor.start()

        item_id = self.tree.insert(
            "",
            "end",
            values=(name, ip_address, "Aguardando", "-", "-", ""),
            tags=("waiting",),
        )

        self._monitors[ip_address] = monitor
        self._items_by_ip[ip_address] = item_id
        self._ip_by_item[item_id] = ip_address

    def _save_equipment_list(self) -> None:
        """Salva no arquivo texto os equipamentos monitorados atualmente."""

        records = [
            EquipmentRecord(name=monitor.name, ip_address=monitor.ip_address)
            for monitor in self._monitors.values()
        ]
        self._store.save(records)

    def _schedule_queue_processing(self) -> None:
        """Agenda a leitura periodica dos resultados vindos das threads."""

        self._process_result_queue()
        self.after(200, self._schedule_queue_processing)

    def _process_result_queue(self) -> None:
        """Aplica na tabela todos os resultados pendentes."""

        while True:
            try:
                result = self._result_queue.get_nowait()
            except queue.Empty:
                break

            self._apply_ping_result(result)

    def _apply_ping_result(self, result: PingResult) -> None:
        """Atualiza a linha de um equipamento com o resultado mais recente."""

        item_id = self._items_by_ip.get(result.ip_address)
        if item_id is None:
            return

        status = "Online" if result.is_online else "Offline"
        tag = "online" if result.is_online else "offline"
        latency = f"{result.latency_ms:.0f} ms" if result.latency_ms is not None else "-"
        checked_at = result.checked_at.strftime("%H:%M:%S")
        message = "" if result.is_online else (result.error or "Sem resposta")

        self.tree.item(
            item_id,
            values=(result.name, result.ip_address, status, latency, checked_at, message),
            tags=(tag,),
        )

        self._last_status[result.ip_address] = result.is_online
        self._outage_notifier.handle_ping_result(result)
        self._update_summary()

    def _update_summary(self) -> None:
        """Atualiza o texto de resumo no topo da janela."""

        total = len(self._monitors)
        if total == 0:
            self.summary_var.set("Nenhum equipamento monitorado")
            return

        online = sum(1 for status in self._last_status.values() if status)
        offline = sum(1 for status in self._last_status.values() if status is False)
        waiting = total - online - offline

        self.summary_var.set(
            f"Monitorando {total} | Online: {online} | Offline: {offline} | Aguardando: {waiting}"
        )

    def _on_close(self) -> None:
        """Para os monitores ativos antes de fechar a janela."""

        for monitor in self._monitors.values():
            monitor.stop(wait=False)

        self.destroy()

    @staticmethod
    def _is_valid_ip(value: str) -> bool:
        """Confere se o valor digitado e um IPv4 ou IPv6 valido."""

        try:
            ipaddress.ip_address(value)
        except ValueError:
            return False

        return True

    @staticmethod
    def _is_valid_url(value: str) -> bool:
        """Confere se a URL informada tem protocolo e endereco."""

        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
