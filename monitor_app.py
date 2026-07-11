"""Interface Tkinter para monitorar equipamentos, hosts e servicos web."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import ipaddress
import math
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlparse

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import seaborn as sns

from equipment_store import (
    DEFAULT_EQUIPMENT_GROUP,
    DEFAULT_PING_INTERVAL_SECONDS,
    EquipmentRecord,
    EquipmentStore,
)
from notification_config import format_thresholds_text, parse_thresholds_text
from outage_notifier import OutageNotifier
from ping_monitor import EquipmentMonitor, PingResult
from secure_settings import NotificationSettings, SecureSettingsStore, SettingsStorageError


GROUP_FILTER_ALL = "Todos os grupos"
STATUS_FILTER_ALL = "Todos"
STATUS_WAITING = "Aguardando"
STATUS_ONLINE = "Online"
STATUS_OFFLINE = "Offline"
STATUS_UNSTABLE = "Instavel"
STATUS_FLAPPING = "Oscilando"
STATUS_MAINTENANCE = "Manutencao"
STATUS_FILTER_OPTIONS = (
    STATUS_FILTER_ALL,
    STATUS_ONLINE,
    STATUS_OFFLINE,
    STATUS_UNSTABLE,
    STATUS_FLAPPING,
    STATUS_WAITING,
    STATUS_MAINTENANCE,
)
ANALYTICS_HISTORY_LIMIT = 1000
WEEKDAY_OPTIONS = (
    ("Seg", 0),
    ("Ter", 1),
    ("Qua", 2),
    ("Qui", 3),
    ("Sex", 4),
    ("Sab", 5),
    ("Dom", 6),
)
WEEKDAY_LABELS = {0: "Segunda", 1: "Terca", 2: "Quarta", 3: "Quinta", 4: "Sexta", 5: "Sabado", 6: "Domingo"}


@dataclass
class EquipmentRuntimeState:
    """Estado operacional derivado dos resultados de monitoramento."""

    failure_streak: int = 0
    confirmed_status: bool | None = None
    display_status: str = STATUS_WAITING
    offline_since: datetime | None = None
    first_failure_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_latency_ms: float | None = None
    last_error: str = ""
    last_event: str = "Aguardando primeira leitura"
    transition_times: list[datetime] = field(default_factory=list)
    is_flapping: bool = False
    maintenance_until: datetime | None = None


class NetworkMonitorApp(tk.Tk):
    """Janela principal do monitor de equipamentos."""

    def __init__(self) -> None:
        super().__init__()

        self.title("Monitor de Equipamentos e Servicos")
        self.geometry("1180x760")
        self.minsize(980, 620)

        # A fila recebe resultados vindos das threads de monitoramento. O Tkinter so deve
        # ser atualizado pela thread principal, por isso a UI consulta essa fila.
        self._result_queue: queue.Queue[PingResult] = queue.Queue()

        self._monitors: dict[str, EquipmentMonitor] = {}
        self._items_by_ip: dict[str, str] = {}
        self._ip_by_item: dict[str, str] = {}
        self._group_by_ip: dict[str, str] = {}
        self._visible_ips: set[str] = set()
        self._last_status: dict[str, bool] = {}
        self._runtime_by_ip: dict[str, EquipmentRuntimeState] = {}
        self._event_history: list[tuple[datetime, str, str, str]] = []
        self._editing_ip: str | None = None
        self._store = EquipmentStore()
        self._settings_store = SecureSettingsStore()
        self._settings_load_error: str | None = None
        self._notification_settings = self._load_notification_settings()
        self._outage_notifier = OutageNotifier(settings=self._notification_settings)

        self.name_var = tk.StringVar()
        self.ip_var = tk.StringVar()
        self.group_var = tk.StringVar(value=DEFAULT_EQUIPMENT_GROUP)
        self.ping_interval_var = tk.StringVar(
            value=self._format_seconds(DEFAULT_PING_INTERVAL_SECONDS)
        )
        self.group_filter_var = tk.StringVar(value=GROUP_FILTER_ALL)
        self.status_filter_var = tk.StringVar(value=STATUS_FILTER_ALL)
        self.search_var = tk.StringVar()
        self.maintenance_minutes_var = tk.StringVar(value="60")
        self.maintenance_group_var = tk.StringVar(value=DEFAULT_EQUIPMENT_GROUP)
        self.summary_var = tk.StringVar(value="Nenhum alvo monitorado")
        self.dashboard_vars = {
            "total": tk.StringVar(value="0"),
            "online": tk.StringVar(value="0"),
            "offline": tk.StringVar(value="0"),
            "unstable": tk.StringVar(value="0"),
            "flapping": tk.StringVar(value="0"),
            "waiting": tk.StringVar(value="0"),
            "maintenance": tk.StringVar(value="0"),
        }
        self.api_url_var = tk.StringVar(value=self._notification_settings.api_url)
        self.whatsapp_number_var = tk.StringVar(value=self._notification_settings.whatsapp_number)
        self.api_key_var = tk.StringVar(value=self._notification_settings.api_key)
        self.notification_intervals_var = tk.StringVar(
            value=format_thresholds_text(self._notification_settings.thresholds_seconds)
        )
        self.group_alert_group_var = tk.StringVar()
        self.group_alert_intervals_var = tk.StringVar()
        self.group_alert_window_start_var = tk.StringVar()
        self.group_alert_window_end_var = tk.StringVar()
        self.group_alert_weekday_vars = {
            weekday: tk.BooleanVar(value=True) for _label, weekday in WEEKDAY_OPTIONS
        }
        self.group_alert_status_var = tk.StringVar(
            value="Grupos sem regra propria usam o intervalo global e notificam 24h."
        )
        self.offline_failure_threshold_var = tk.StringVar(
            value=str(self._notification_settings.offline_failure_threshold)
        )
        self.flapping_transition_count_var = tk.StringVar(
            value=str(self._notification_settings.flapping_transition_count)
        )
        self.flapping_window_minutes_var = tk.StringVar(
            value=str(self._notification_settings.flapping_window_minutes)
        )
        self.analytics_group_var = tk.StringVar(value=GROUP_FILTER_ALL)
        self.analytics_range_var = tk.StringVar(value="24h")
        self.analytics_total_events_var = tk.StringVar(value="0")
        self.analytics_group_events_var = tk.StringVar(value="0")
        self.analytics_last_event_var = tk.StringVar(value="Sem eventos recentes")
        self.analytics_top_group_var = tk.StringVar(value="-")
        self.show_api_key_var = tk.BooleanVar(value=False)
        self.settings_status_var = tk.StringVar(value=self._build_settings_status())
        self._chart_palette = sns.color_palette("Blues", 6)
        self._analytics_dirty = True
        self._analytics_last_signature: tuple | None = None

        self._configure_style()
        self._build_layout()
        self._load_saved_equipment()
        self._schedule_queue_processing()
        self._schedule_analytics_refresh()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        """Configura estilos basicos dos widgets ttk."""

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        bg = "#eef2f7"
        panel = "#ffffff"
        text = "#102033"
        muted = "#546579"
        accent = "#2563eb"
        accent_hover = "#1d4ed8"
        success = "#137333"
        warning = "#b45309"
        danger = "#b3261e"

        self.configure(background=bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=bg, font=("Segoe UI Semibold", 16))
        style.configure("Subtitle.TLabel", background=bg, foreground=muted, font=("Segoe UI", 10))
        style.configure("Summary.TLabel", background=bg, foreground=muted)
        style.configure("Section.TLabelframe", background=bg)
        style.configure("Section.TLabelframe.Label", background=bg, foreground=text, font=("Segoe UI Semibold", 10))
        style.configure("Panel.TFrame", background=panel)
        style.configure("Card.TFrame", background=panel, relief="solid", borderwidth=1)
        style.configure("CardTitle.TLabel", background=panel, foreground=muted)
        style.configure("CardValue.TLabel", background=panel, foreground=text, font=("Segoe UI Semibold", 17))
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(10, 6))
        style.configure("Primary.TButton", background=accent, foreground="#ffffff")
        style.map("Primary.TButton", background=[("active", accent_hover), ("pressed", accent_hover)])
        style.configure("Secondary.TButton", background="#dbe4f0", foreground=text)
        style.map("Secondary.TButton", background=[("active", "#cbd5e1"), ("pressed", "#cbd5e1")])
        style.configure("Danger.TButton", background="#f4d7d5", foreground=danger)
        style.map("Danger.TButton", background=[("active", "#efc4c0"), ("pressed", "#efc4c0")])
        style.configure("Treeview", rowheight=32, font=("Segoe UI", 10), fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI Semibold", 10))
        style.map("TNotebook.Tab", padding=[("selected", (14, 8))])

        style.configure("online.Treeview", foreground=success)
        style.configure("offline.Treeview", foreground=danger)
        style.configure("unstable.Treeview", foreground=warning)
        style.configure("flapping.Treeview", foreground="#8a4b00")
        style.configure("maintenance.Treeview", foreground="#596579")
        style.configure("waiting.Treeview", foreground="#5f6368")

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
        analytics_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(analytics_tab, text="Painel")
        notebook.add(root, text="Monitoramento")
        notebook.add(settings_tab, text="Configuracoes")

        self._build_analytics_tab(analytics_tab)
        self._build_monitoring_tab(root)
        self._build_settings_tab(settings_tab)

    def _build_monitoring_tab(self, parent: ttk.Frame) -> None:
        """Cria a aba principal de operacao."""

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)

        header = ttk.Frame(parent, style="Panel.TFrame", padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)

        ttk.Label(header, text="Monitor de Equipamentos e Servicos", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            textvariable=self.summary_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            header,
            text="Ping, status e alertas em uma unica tela.",
            style="Subtitle.TLabel",
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        self._build_dashboard_cards(parent)

        self.equipment_form = ttk.LabelFrame(parent, text="Novo alvo", padding=12, style="Section.TLabelframe")
        self.equipment_form.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.equipment_form.columnconfigure(1, weight=1)
        self.equipment_form.columnconfigure(3, weight=1)

        form = self.equipment_form
        ttk.Label(form, text="Nome").grid(row=0, column=0, sticky="w", padx=(0, 8))
        name_entry = ttk.Entry(form, textvariable=self.name_var)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))

        ttk.Label(form, text="IP/Host/URL").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ip_entry = ttk.Entry(form, textvariable=self.ip_var)
        ip_entry.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ip_entry.bind("<Return>", lambda _event: self._save_equipment_form())

        ttk.Label(form, text="Grupo").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.group_entry = ttk.Combobox(form, textvariable=self.group_var)
        self.group_entry.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(10, 0))
        self.group_entry.bind("<Return>", lambda _event: self._save_equipment_form())

        ttk.Label(form, text="Intervalo (s)").grid(row=1, column=2, sticky="w", padx=(0, 8))
        ping_entry = ttk.Entry(form, textvariable=self.ping_interval_var, width=10)
        ping_entry.grid(row=1, column=3, sticky="w", pady=(10, 0))
        ping_entry.bind("<Return>", lambda _event: self._save_equipment_form())

        self.save_equipment_button = ttk.Button(
            form,
            text="Adicionar",
            style="Primary.TButton",
            command=self._save_equipment_form,
        )
        self.save_equipment_button.grid(
            row=0, column=4, sticky="nsew", padx=(0, 8)
        )
        self.cancel_edit_button = ttk.Button(
            form,
            text="Cancelar",
            style="Secondary.TButton",
            command=self._cancel_equipment_edit,
            state="disabled",
        )
        self.cancel_edit_button.grid(
            row=1, column=4, sticky="nsew", padx=(0, 8), pady=(10, 0)
        )

        filters = ttk.LabelFrame(parent, text="Filtros", padding=10, style="Section.TLabelframe")
        filters.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        filters.columnconfigure(1, weight=1)
        filters.columnconfigure(3, weight=1)
        filters.columnconfigure(5, weight=2)

        ttk.Label(filters, text="Grupo").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.group_filter_combo = ttk.Combobox(
            filters,
            textvariable=self.group_filter_var,
            state="readonly",
            values=(GROUP_FILTER_ALL,),
        )
        self.group_filter_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.group_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_filters())

        ttk.Label(filters, text="Status").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.status_filter_combo = ttk.Combobox(
            filters,
            textvariable=self.status_filter_var,
            state="readonly",
            values=STATUS_FILTER_OPTIONS,
        )
        self.status_filter_combo.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        self.status_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_filters())

        ttk.Label(filters, text="Busca").grid(row=0, column=4, sticky="w", padx=(0, 8))
        search_entry = ttk.Entry(filters, textvariable=self.search_var)
        search_entry.grid(row=0, column=5, sticky="ew", padx=(0, 12))
        search_entry.bind("<KeyRelease>", lambda _event: self._apply_filters())

        ttk.Button(filters, text="Limpar", command=self._clear_filters).grid(
            row=0, column=6, sticky="e"
        )

        content = ttk.Frame(parent)
        content.grid(row=4, column=0, sticky="nsew")
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        self._build_group_summary(content)
        self._build_equipment_table(content)
        self._build_recent_events(parent)
        self._build_monitoring_actions(parent)

        name_entry.focus_set()

    def _build_dashboard_cards(self, parent: ttk.Frame) -> None:
        """Monta os cards de totais operacionais."""

        dashboard = ttk.Frame(parent)
        dashboard.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        cards = (
            ("Total", "total"),
            ("Online", "online"),
            ("Offline", "offline"),
            ("Instavel", "unstable"),
            ("Oscilando", "flapping"),
            ("Aguardando", "waiting"),
            ("Manutencao", "maintenance"),
        )

        for column, (title, key) in enumerate(cards):
            dashboard.columnconfigure(column, weight=1)
            card = ttk.Frame(dashboard, style="Card.TFrame", padding=(10, 8))
            card.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(card, textvariable=self.dashboard_vars[key], style="CardValue.TLabel").grid(
                row=1,
                column=0,
                sticky="w",
            )

    def _build_analytics_tab(self, parent: ttk.Frame) -> None:
        """Cria a aba com graficos de operacao."""

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=0)
        parent.rowconfigure(1, weight=0)
        parent.rowconfigure(2, weight=0)
        parent.rowconfigure(3, weight=1)

        header = ttk.Frame(parent, style="Panel.TFrame", padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)
        ttk.Label(header, text="Painel de Operacao", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Resumo visual do estado da rede e dos eventos recentes.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        controls = ttk.Frame(parent)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)

        ttk.Label(controls, text="Grupo").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.analytics_group_combo = ttk.Combobox(
            controls,
            textvariable=self.analytics_group_var,
            state="readonly",
            values=(GROUP_FILTER_ALL,),
            width=24,
        )
        self.analytics_group_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.analytics_group_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_analytics())

        ttk.Label(controls, text="Periodo").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.analytics_range_combo = ttk.Combobox(
            controls,
            textvariable=self.analytics_range_var,
            state="readonly",
            values=("24h", "7d", "30d", "todos"),
            width=10,
        )
        self.analytics_range_combo.grid(row=0, column=3, sticky="w")
        self.analytics_range_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_analytics())

        ttk.Label(controls, text="Sem acumulo: os graficos usam eventos do painel atual e do historico recente.", style="Subtitle.TLabel").grid(
            row=1,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )

        summary = ttk.Frame(parent, style="Panel.TFrame")
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for column in range(4):
            summary.columnconfigure(column, weight=1)
        self._build_analytics_cards(summary)

        content = ttk.Notebook(parent)
        content.grid(row=3, column=0, sticky="nsew")

        overview = ttk.Frame(content, padding=(0, 4))
        details = ttk.Frame(content, padding=(0, 4))
        indicators = ttk.Frame(content, padding=(0, 4))
        content.add(overview, text="Visao geral")
        content.add(details, text="Detalhes")
        content.add(indicators, text="Indicadores")

        overview.columnconfigure(0, weight=3)
        overview.columnconfigure(1, weight=2)
        overview.rowconfigure(0, weight=1)
        overview.rowconfigure(1, weight=1)

        self.status_chart = self._build_chart_panel(
            overview,
            row=0,
            column=0,
            title="Estado atual",
            figsize=(7.0, 3.3),
        )
        self.group_chart = self._build_chart_panel(
            overview,
            row=0,
            column=1,
            title="Eventos por grupo",
            figsize=(5.0, 3.3),
        )

        details.columnconfigure(0, weight=1)
        details.rowconfigure(0, weight=1)
        details.rowconfigure(1, weight=0)
        self.hour_chart = self._build_chart_panel(
            details,
            row=0,
            column=0,
            title="Eventos por hora",
            figsize=(12.0, 3.2),
        )

        events_frame = ttk.LabelFrame(details, text="Eventos filtrados", padding=10, style="Section.TLabelframe")
        events_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        events_frame.columnconfigure(0, weight=1)
        self.analytics_events_tree = ttk.Treeview(
            events_frame,
            columns=("time", "group", "name", "event"),
            show="headings",
            height=6,
        )
        self.analytics_events_tree.heading("time", text="Hora")
        self.analytics_events_tree.heading("group", text="Grupo")
        self.analytics_events_tree.heading("name", text="Alvo")
        self.analytics_events_tree.heading("event", text="Evento")
        self.analytics_events_tree.column("time", width=88, minwidth=88, anchor="center", stretch=False)
        self.analytics_events_tree.column("group", width=170, minwidth=150, anchor="w", stretch=False)
        self.analytics_events_tree.column("name", width=220, minwidth=180, anchor="w", stretch=True)
        self.analytics_events_tree.column("event", width=520, minwidth=260, anchor="w", stretch=True)
        self.analytics_events_tree.grid(row=0, column=0, sticky="ew")

        indicators.columnconfigure(0, weight=1)
        indicators.columnconfigure(1, weight=1)
        indicators.rowconfigure(0, weight=1)
        indicators.rowconfigure(1, weight=0)

        self.offline_chart = self._build_chart_panel(
            indicators,
            row=0,
            column=0,
            title="Tempo offline por grupo",
            figsize=(6.2, 3.2),
        )
        self.event_type_chart = self._build_chart_panel(
            indicators,
            row=0,
            column=1,
            title="Tipos de evento",
            figsize=(6.2, 3.2),
        )

        top_frame = ttk.LabelFrame(indicators, text="Top equipamentos no historico", padding=10, style="Section.TLabelframe")
        top_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        top_frame.columnconfigure(0, weight=1)
        self.top_equipment_tree = ttk.Treeview(
            top_frame,
            columns=("name", "group", "events"),
            show="headings",
            height=6,
        )
        self.top_equipment_tree.heading("name", text="Alvo")
        self.top_equipment_tree.heading("group", text="Grupo")
        self.top_equipment_tree.heading("events", text="Ocorrencias")
        self.top_equipment_tree.column("name", width=280, minwidth=200, anchor="w", stretch=True)
        self.top_equipment_tree.column("group", width=220, minwidth=160, anchor="w", stretch=True)
        self.top_equipment_tree.column("events", width=100, minwidth=100, anchor="center", stretch=False)
        self.top_equipment_tree.grid(row=0, column=0, sticky="ew")

    def _build_chart_panel(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        title: str,
        figsize: tuple[float, float] = (4.2, 3.1),
    ) -> dict[str, object]:
        """Cria um painel grafico com figura do matplotlib."""

        frame = ttk.LabelFrame(parent, text=title, padding=10, style="Section.TLabelframe")
        frame.grid(
            row=row,
            column=column,
            sticky="nsew",
            padx=(0, 8) if column == 0 else (4, 0) if column == 1 else (0, 0),
            pady=(0, 0),
        )
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        figure = Figure(figsize=figsize, dpi=100)
        figure.patch.set_facecolor("#ffffff")
        axis = figure.add_subplot(111)
        axis.set_facecolor("#ffffff")
        canvas = FigureCanvasTkAgg(figure, master=frame)
        widget = canvas.get_tk_widget()
        widget.grid(row=0, column=0, sticky="nsew")
        return {"frame": frame, "figure": figure, "axis": axis, "canvas": canvas}

    def _build_analytics_cards(self, parent: ttk.Frame) -> None:
        """Monta cards-resumo da aba de painel."""

        cards = (
            ("Eventos", self.analytics_total_events_var),
            ("Filtrados", self.analytics_group_events_var),
            ("Grupo topo", self.analytics_top_group_var),
            ("Ultimo evento", self.analytics_last_event_var),
        )
        for column, (title, variable) in enumerate(cards):
            parent.columnconfigure(column, weight=1, uniform="analytics_cards")
            card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 8), pady=(0, 0))
            card.rowconfigure(0, weight=0)
            card.rowconfigure(1, weight=1)
            ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
            card.columnconfigure(0, weight=1)
            wrap = 260 if title == "Ultimo evento" else 190
            ttk.Label(
                card,
                textvariable=variable,
                style="CardValue.TLabel",
                wraplength=wrap,
                justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _build_group_summary(self, parent: ttk.Frame) -> None:
        """Cria o resumo por grupo."""

        group_frame = ttk.LabelFrame(
            parent,
            text="Resumo por grupo",
            padding=8,
            style="Section.TLabelframe",
        )
        group_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        group_frame.rowconfigure(0, weight=1)

        columns = ("group", "total", "online", "offline", "flapping", "waiting")
        self.group_summary_tree = ttk.Treeview(
            group_frame,
            columns=columns,
            show="headings",
            height=8,
            selectmode="browse",
        )
        headings = {
            "group": "Grupo",
            "total": "Total",
            "online": "On",
            "offline": "Off",
            "flapping": "Osc.",
            "waiting": "Ag.",
        }
        widths = {
            "group": 170,
            "total": 64,
            "online": 58,
            "offline": 58,
            "flapping": 58,
            "waiting": 58,
        }
        for column in columns:
            self.group_summary_tree.heading(column, text=headings[column])
            self.group_summary_tree.column(
                column,
                width=widths[column],
                minwidth=48,
                anchor="center" if column != "group" else "w",
                stretch=column == "group",
            )

        self.group_summary_tree.grid(row=0, column=0, sticky="nsew")
        self.group_summary_tree.bind("<<TreeviewSelect>>", self._select_group_from_summary)

    def _build_equipment_table(self, parent: ttk.Frame) -> None:
        """Cria a tabela principal de equipamentos."""

        table_frame = ttk.Frame(parent)
        table_frame.grid(row=0, column=1, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = (
            "group",
            "name",
            "ip",
            "status",
            "latency",
            "checked_at",
            "offline_for",
            "last_event",
            "error",
        )
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "group": "Grupo",
            "name": "Alvo",
            "ip": "Endereco",
            "status": "Status",
            "latency": "Latencia",
            "checked_at": "Ultima leitura",
            "offline_for": "Tempo offline",
            "last_event": "Ultimo evento",
            "error": "Mensagem",
        }
        widths = {
            "group": 160,
            "name": 190,
            "ip": 190,
            "status": 110,
            "latency": 88,
            "checked_at": 112,
            "offline_for": 112,
            "last_event": 180,
            "error": 260,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                minwidth=80,
                anchor="center"
                if column in {"status", "latency", "checked_at", "offline_for"}
                else "w",
                stretch=column in {"group", "name", "ip", "last_event", "error"},
            )

        self.tree.tag_configure("online", foreground="#137333")
        self.tree.tag_configure("offline", foreground="#b3261e")
        self.tree.tag_configure("unstable", foreground="#b06000")
        self.tree.tag_configure("flapping", foreground="#8a4b00")
        self.tree.tag_configure("maintenance", foreground="#4b5563")
        self.tree.tag_configure("waiting", foreground="#5f6368")

        y_scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        x_scrollbar = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        x_scrollbar.grid(row=1, column=0, sticky="ew")

    def _build_recent_events(self, parent: ttk.Frame) -> None:
        """Cria a lista curta de eventos recentes."""

        events_frame = ttk.LabelFrame(
            parent,
            text="Eventos recentes",
            padding=8,
            style="Section.TLabelframe",
        )
        events_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        events_frame.columnconfigure(0, weight=1)

        columns = ("time", "equipment", "event", "group")
        self.events_tree = ttk.Treeview(
            events_frame,
            columns=columns,
            show="headings",
            height=4,
            selectmode="none",
        )
        self.events_tree.heading("time", text="Hora")
        self.events_tree.heading("equipment", text="Alvo")
        self.events_tree.heading("event", text="Evento")
        self.events_tree.heading("group", text="Grupo")
        self.events_tree.column("time", width=88, minwidth=88, anchor="center", stretch=False)
        self.events_tree.column("equipment", width=210, minwidth=170, anchor="w", stretch=True)
        self.events_tree.column("event", width=520, minwidth=260, anchor="w", stretch=True)
        self.events_tree.column("group", width=160, minwidth=140, anchor="w", stretch=False)
        self.events_tree.grid(row=0, column=0, sticky="ew")

    def _build_monitoring_actions(self, parent: ttk.Frame) -> None:
        """Cria a faixa de acoes da aba principal."""

        actions = ttk.Frame(parent)
        actions.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure(7, weight=1)

        ttk.Label(actions, text="Manutencao (min)").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Entry(actions, textvariable=self.maintenance_minutes_var, width=8).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Label(actions, text="Grupo").grid(
            row=0,
            column=2,
            sticky="w",
            padx=(0, 8),
        )
        self.maintenance_group_combo = ttk.Combobox(
            actions,
            textvariable=self.maintenance_group_var,
            state="readonly",
            values=(DEFAULT_EQUIPMENT_GROUP,),
            width=18,
        )
        self.maintenance_group_combo.grid(
            row=0,
            column=3,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="Silenciar selecionado",
            style="Primary.TButton",
            command=self._start_maintenance,
        ).grid(
            row=0,
            column=4,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="Silenciar grupo",
            style="Secondary.TButton",
            command=self._start_group_maintenance,
        ).grid(
            row=0,
            column=5,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="Silenciar todos",
            style="Secondary.TButton",
            command=self._start_all_maintenance,
        ).grid(
            row=0,
            column=6,
            sticky="w",
        )
        ttk.Button(
            actions,
            text="Encerrar selecionado",
            style="Secondary.TButton",
            command=self._end_maintenance,
        ).grid(
            row=1,
            column=4,
            sticky="w",
            padx=(0, 8),
            pady=(8, 0),
        )
        ttk.Button(
            actions,
            text="Encerrar grupo",
            style="Secondary.TButton",
            command=self._end_group_maintenance,
        ).grid(
            row=1,
            column=5,
            sticky="w",
            padx=(0, 8),
            pady=(8, 0),
        )
        ttk.Button(
            actions,
            text="Encerrar todos",
            style="Secondary.TButton",
            command=self._end_all_maintenance,
        ).grid(
            row=1,
            column=6,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Button(
            actions,
            text="Editar selecionado",
            style="Secondary.TButton",
            command=self._edit_selected,
        ).grid(
            row=1,
            column=8,
            sticky="w",
            padx=(0, 8),
            pady=(8, 0),
        )
        ttk.Button(
            actions,
            text="Remover selecionado",
            style="Danger.TButton",
            command=self._remove_selected,
        ).grid(
            row=1,
            column=9,
            sticky="e",
            pady=(8, 0),
        )

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        """Cria a aba com as configuracoes da Evolution API."""

        parent.columnconfigure(0, weight=1)

        form = ttk.LabelFrame(
            parent,
            text="WhatsApp / Evolution API",
            padding=12,
            style="Section.TLabelframe",
        )
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

        ttk.Label(form, text="Alertar apos").grid(
            row=3, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(form, textvariable=self.notification_intervals_var).grid(
            row=3,
            column=1,
            sticky="ew",
            pady=(0, 10),
        )

        ttk.Label(form, text="Falhas p/ offline").grid(
            row=4, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(form, textvariable=self.offline_failure_threshold_var, width=12).grid(
            row=4,
            column=1,
            sticky="w",
            pady=(0, 10),
        )

        ttk.Label(form, text="Oscilar apos mudancas").grid(
            row=5, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(form, textvariable=self.flapping_transition_count_var, width=12).grid(
            row=5,
            column=1,
            sticky="w",
            pady=(0, 10),
        )

        ttk.Label(form, text="Janela oscilacao (min)").grid(
            row=6, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(form, textvariable=self.flapping_window_minutes_var, width=12).grid(
            row=6,
            column=1,
            sticky="w",
            pady=(0, 10),
        )

        ttk.Checkbutton(
            form,
            text="Mostrar chave",
            variable=self.show_api_key_var,
            command=self._toggle_api_key_visibility,
        ).grid(row=7, column=1, sticky="w", pady=(0, 10))

        actions = ttk.Frame(form)
        actions.grid(row=8, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure(0, weight=1)

        ttk.Label(actions, textvariable=self.settings_status_var, style="Summary.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Button(
            actions,
            text="Salvar configuracoes",
            style="Primary.TButton",
            command=self._save_notification_settings,
        ).grid(row=0, column=1, sticky="e")

        self._build_group_alert_settings(parent)

        help_frame = ttk.LabelFrame(
            parent,
            text="Onde encontrar essas informacoes",
            padding=12,
            style="Section.TLabelframe",
        )
        help_frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        help_frame.columnconfigure(0, weight=1)

        help_text = (
            "URL do endpoint: use o endpoint de envio de texto da Evolution API. "
            "O formato costuma ser https://SEU_SERVIDOR/message/sendText/NOME_DA_INSTANCIA.\n\n"
            "Chave da API: use a apikey/chave da instancia no painel da Evolution API.\n\n"
            "Alertar apos: informe os tempos de queda que devem gerar notificacao. "
            "Use s para segundos, m para minutos ou h para horas. "
            "Exemplo: 5s, 30s, 1m, 5m.\n\n"
            "Alertas por grupo: cadastre intervalos e horarios diferentes para grupos especificos. "
            "Quando nao houver regra para o grupo, vale o intervalo global acima e notificacao 24h. "
            "Fora do horario configurado, a queda nao fica acumulada para envio posterior.\n\n"
            "Falhas p/ offline: quantidade de verificacoes seguidas sem resposta antes de "
            "confirmar queda. Oscilacao: quantidade de mudancas online/offline dentro "
            "da janela em minutos.\n\n"
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

    def _build_group_alert_settings(self, parent: ttk.Frame) -> None:
        """Cria a configuracao de intervalos de alerta por grupo."""

        frame = ttk.LabelFrame(parent, text="Alertas por grupo", padding=12)
        frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        ttk.Label(frame, text="Grupo").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.group_alert_combo = ttk.Combobox(
            frame,
            textvariable=self.group_alert_group_var,
            state="readonly",
        )
        self.group_alert_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.group_alert_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._load_group_alert_selection(),
        )

        ttk.Label(frame, text="Alertar apos").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Entry(frame, textvariable=self.group_alert_intervals_var).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 12),
        )

        self.group_alert_save_button = ttk.Button(
            frame,
            text="Salvar grupo",
            style="Primary.TButton",
            command=self._save_group_alert_settings,
        )
        self.group_alert_save_button.grid(
            row=0,
            column=4,
            sticky="ew",
            padx=(0, 8),
        )
        ttk.Button(
            frame,
            text="Editar selecionado",
            style="Secondary.TButton",
            command=self._edit_selected_group_alert_rule,
        ).grid(
            row=0,
            column=5,
            sticky="ew",
            padx=(0, 8),
        )
        ttk.Button(
            frame,
            text="Usar global",
            style="Secondary.TButton",
            command=self._remove_group_alert_settings,
        ).grid(
            row=0,
            column=6,
            sticky="ew",
        )

        ttk.Label(frame, text="Notificar de").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frame, textvariable=self.group_alert_window_start_var, width=8).grid(
            row=1,
            column=1,
            sticky="w",
            padx=(0, 12),
            pady=(8, 0),
        )
        ttk.Label(frame, text="Ate").grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(frame, textvariable=self.group_alert_window_end_var, width=8).grid(
            row=1,
            column=3,
            sticky="w",
            padx=(0, 12),
            pady=(8, 0),
        )
        ttk.Label(frame, text="Em branco: 24h").grid(
            row=1,
            column=4,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        weekday_frame = ttk.Frame(frame)
        weekday_frame.grid(row=2, column=0, columnspan=7, sticky="ew", pady=(8, 0))
        ttk.Label(weekday_frame, text="Dias da semana").grid(row=0, column=0, sticky="w", padx=(0, 10))
        for index, (label, weekday) in enumerate(WEEKDAY_OPTIONS, start=1):
            ttk.Checkbutton(
                weekday_frame,
                text=label,
                variable=self.group_alert_weekday_vars[weekday],
                command=self._update_group_alert_weekday_status,
            ).grid(row=0, column=index, sticky="w", padx=(0, 8))

        ttk.Label(frame, textvariable=self.group_alert_status_var, style="Summary.TLabel").grid(
            row=3,
            column=0,
            columnspan=7,
            sticky="w",
            pady=(8, 8),
        )

        columns = ("group", "intervals", "window")
        self.group_alert_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=4,
            selectmode="browse",
        )
        self.group_alert_tree.heading("group", text="Grupo")
        self.group_alert_tree.heading("intervals", text="Intervalos")
        self.group_alert_tree.heading("window", text="Horario / dias")
        self.group_alert_tree.column("group", width=200, minwidth=160, anchor="w", stretch=True)
        self.group_alert_tree.column("intervals", width=280, minwidth=200, anchor="w", stretch=True)
        self.group_alert_tree.column("window", width=170, minwidth=150, anchor="center", stretch=False)
        self.group_alert_tree.grid(row=3, column=0, columnspan=7, sticky="ew")
        self.group_alert_tree.bind("<<TreeviewSelect>>", self._select_group_alert_rule)
        self._refresh_group_alert_options()

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

        try:
            thresholds_seconds = parse_thresholds_text(self.notification_intervals_var.get())
            offline_failure_threshold = self._parse_positive_int(
                self.offline_failure_threshold_var.get(),
                "Falhas p/ offline",
            )
            flapping_transition_count = self._parse_positive_int(
                self.flapping_transition_count_var.get(),
                "Oscilar apos mudancas",
            )
            flapping_window_minutes = self._parse_positive_int(
                self.flapping_window_minutes_var.get(),
                "Janela oscilacao",
            )
        except ValueError as exc:
            messagebox.showwarning("Configuracao invalida", str(exc))
            return

        settings = NotificationSettings(
            api_url=self.api_url_var.get().strip(),
            api_key=self.api_key_var.get().strip(),
            whatsapp_number=self.whatsapp_number_var.get().strip(),
            thresholds_seconds=thresholds_seconds,
            group_thresholds_seconds=self._notification_settings.group_thresholds_seconds,
            group_notification_windows=self._notification_settings.group_notification_windows,
            offline_failure_threshold=offline_failure_threshold,
            flapping_transition_count=flapping_transition_count,
            flapping_window_minutes=flapping_window_minutes,
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
        self.notification_intervals_var.set(format_thresholds_text(thresholds_seconds))
        self.offline_failure_threshold_var.set(str(offline_failure_threshold))
        self.flapping_transition_count_var.set(str(flapping_transition_count))
        self.flapping_window_minutes_var.set(str(flapping_window_minutes))
        self._refresh_group_alert_options()
        self._refresh_live_rows()
        self.settings_status_var.set("Configuracoes salvas com criptografia local.")
        messagebox.showinfo("Configuracoes salvas", "As notificacoes foram configuradas.")

    def _save_group_alert_settings(self) -> None:
        """Salva intervalos de notificacao especificos para um grupo."""

        group = self._normalize_group(self.group_alert_group_var.get())
        try:
            thresholds = parse_thresholds_text(self.group_alert_intervals_var.get())
            notification_window = self._parse_group_notification_window()
        except ValueError as exc:
            messagebox.showwarning("Regra invalida", str(exc))
            return

        weekdays = self._selected_group_weekdays()
        if weekdays is None:
            messagebox.showwarning(
                "Regra invalida",
                "Selecione pelo menos um dia da semana para este grupo.",
            )
            return

        group_thresholds = dict(self._notification_settings.group_thresholds_seconds)
        group_thresholds[group] = thresholds
        group_windows = dict(self._notification_settings.group_notification_windows)
        group_weekdays = dict(self._notification_settings.group_notification_weekdays)
        if notification_window is None:
            group_windows.pop(group, None)
        else:
            group_windows[group] = notification_window
        group_weekdays[group] = weekdays

        settings = self._copy_notification_settings(
            group_thresholds_seconds=group_thresholds,
            group_notification_windows=group_windows,
            group_notification_weekdays=group_weekdays,
        )
        if not self._persist_notification_settings(settings):
            return

        self.group_alert_intervals_var.set(format_thresholds_text(thresholds))
        self._load_group_alert_selection()
        self.group_alert_status_var.set(f"Regras salvas para o grupo {group}.")
        self._refresh_group_alert_options()

    def _remove_group_alert_settings(self) -> None:
        """Remove a regra especifica do grupo, voltando ao padrao global."""

        group = self._normalize_group(self.group_alert_group_var.get())
        group_thresholds = dict(self._notification_settings.group_thresholds_seconds)
        group_windows = dict(self._notification_settings.group_notification_windows)
        group_weekdays = dict(self._notification_settings.group_notification_weekdays)
        if group not in group_thresholds and group not in group_windows:
            self.group_alert_status_var.set(f"O grupo {group} ja usa as regras globais.")
            self._load_group_alert_selection()
            return

        group_thresholds.pop(group, None)
        group_windows.pop(group, None)
        group_weekdays.pop(group, None)
        settings = self._copy_notification_settings(
            group_thresholds_seconds=group_thresholds,
            group_notification_windows=group_windows,
            group_notification_weekdays=group_weekdays,
        )
        if not self._persist_notification_settings(settings):
            return

        self.group_alert_status_var.set(f"O grupo {group} voltou a usar as regras globais.")
        self._refresh_group_alert_options()
        self._load_group_alert_selection()

    def _copy_notification_settings(
        self,
        group_thresholds_seconds: dict[str, tuple[int, ...]] | None = None,
        group_notification_windows: dict[str, tuple[str, str]] | None = None,
        group_notification_weekdays: dict[str, tuple[int, ...]] | None = None,
    ) -> NotificationSettings:
        """Cria uma copia das configuracoes atuais com grupos atualizados."""

        return NotificationSettings(
            api_url=self._notification_settings.api_url,
            api_key=self._notification_settings.api_key,
            whatsapp_number=self._notification_settings.whatsapp_number,
            thresholds_seconds=self._notification_settings.thresholds_seconds,
            group_thresholds_seconds=(
                group_thresholds_seconds
                if group_thresholds_seconds is not None
                else self._notification_settings.group_thresholds_seconds
            ),
            group_notification_windows=(
                group_notification_windows
                if group_notification_windows is not None
                else self._notification_settings.group_notification_windows
            ),
            group_notification_weekdays=(
                group_notification_weekdays
                if group_notification_weekdays is not None
                else self._notification_settings.group_notification_weekdays
            ),
            offline_failure_threshold=self._notification_settings.offline_failure_threshold,
            flapping_transition_count=self._notification_settings.flapping_transition_count,
            flapping_window_minutes=self._notification_settings.flapping_window_minutes,
        )

    def _persist_notification_settings(self, settings: NotificationSettings) -> bool:
        """Salva configuracoes e atualiza os componentes em memoria."""

        try:
            self._settings_store.save(settings)
        except SettingsStorageError as exc:
            messagebox.showerror("Erro ao salvar", str(exc))
            return False

        self._notification_settings = settings
        self._settings_load_error = None
        self._outage_notifier.update_settings(settings)
        return True

    def _load_group_alert_selection(self) -> None:
        """Carrega os intervalos exibidos para o grupo selecionado."""

        group = self._normalize_group(self.group_alert_group_var.get())
        thresholds = self._notification_settings.group_thresholds_seconds.get(
            group,
            self._notification_settings.thresholds_seconds,
        )
        notification_window = self._notification_settings.group_notification_windows.get(group)
        weekdays = self._notification_settings.group_notification_weekdays.get(group)
        self.group_alert_intervals_var.set(format_thresholds_text(thresholds))
        if notification_window is None:
            self.group_alert_window_start_var.set("")
            self.group_alert_window_end_var.set("")
        else:
            self.group_alert_window_start_var.set(notification_window[0])
            self.group_alert_window_end_var.set(notification_window[1])
        self._set_group_alert_weekdays(weekdays)
        self._update_group_alert_weekday_status()

    def _select_group_alert_rule(self, _event: tk.Event) -> None:
        """Seleciona uma regra de grupo a partir da tabela."""

        selection = self.group_alert_tree.selection()
        if not selection:
            return

        values = self.group_alert_tree.item(selection[0], "values")
        if not values:
            return

        self.group_alert_group_var.set(str(values[0]))
        self._load_group_alert_selection()

    def _edit_selected_group_alert_rule(self) -> None:
        """Carrega a regra do grupo selecionado para edicao."""

        selection = self.group_alert_tree.selection()
        if not selection:
            messagebox.showinfo("Editar grupo", "Selecione um grupo na tabela abaixo.")
            return

        self._select_group_alert_rule(None)
        self.group_alert_status_var.set(
            f"Editando o grupo {self.group_alert_group_var.get()}. Ajuste os campos e clique em salvar."
        )

    def _refresh_group_alert_options(self) -> None:
        """Atualiza grupos disponiveis na configuracao de alertas por grupo."""

        if not hasattr(self, "group_alert_combo"):
            return

        groups = {
            self._normalize_group(group)
            for group in self._group_by_ip.values()
            if self._normalize_group(group)
        }
        groups.update(self._notification_settings.group_thresholds_seconds)
        groups.update(self._notification_settings.group_notification_windows)
        groups.add(self._normalize_group(self.group_var.get()))
        groups.add(DEFAULT_EQUIPMENT_GROUP)

        ordered_groups = [DEFAULT_EQUIPMENT_GROUP]
        ordered_groups.extend(
            group for group in sorted(groups) if group != DEFAULT_EQUIPMENT_GROUP
        )
        self.group_alert_combo.configure(values=tuple(ordered_groups))

        if self.group_alert_group_var.get() not in ordered_groups:
            self.group_alert_group_var.set(ordered_groups[0])

        self._refresh_group_alert_tree()
        self._load_group_alert_selection()

    def _refresh_group_alert_tree(self) -> None:
        """Renderiza as regras de alerta por grupo."""

        if not hasattr(self, "group_alert_tree"):
            return

        for item_id in self.group_alert_tree.get_children():
            self.group_alert_tree.delete(item_id)

        groups = set(self._notification_settings.group_thresholds_seconds)
        groups.update(self._notification_settings.group_notification_windows)
        groups.update(self._notification_settings.group_notification_weekdays)
        for group in sorted(groups):
            thresholds = self._notification_settings.group_thresholds_seconds.get(group)
            intervals_text = (
                format_thresholds_text(thresholds)
                if thresholds is not None
                else f"Global ({format_thresholds_text(self._notification_settings.thresholds_seconds)})"
            )
            notification_window = self._notification_settings.group_notification_windows.get(group)
            weekdays = self._notification_settings.group_notification_weekdays.get(group)
            self.group_alert_tree.insert(
                "",
                "end",
                values=(
                    group,
                    intervals_text,
                    self._format_notification_window(notification_window, weekdays),
                ),
            )

    def _parse_group_notification_window(self) -> tuple[str, str] | None:
        """Le a janela de notificacao do formulario de grupo."""

        start_text = self.group_alert_window_start_var.get().strip()
        end_text = self.group_alert_window_end_var.get().strip()
        if not start_text and not end_text:
            return None

        if not start_text or not end_text:
            raise ValueError("Preencha inicio e fim do horario, ou deixe ambos em branco.")

        return (
            self._normalize_time_text(start_text, "Notificar de"),
            self._normalize_time_text(end_text, "Ate"),
        )

    def _set_group_alert_weekdays(self, weekdays: tuple[int, ...] | None) -> None:
        """Marca os dias da semana carregados para o grupo selecionado."""

        selected = set(weekdays) if weekdays is not None else {weekday for _label, weekday in WEEKDAY_OPTIONS}
        for _label, weekday in WEEKDAY_OPTIONS:
            self.group_alert_weekday_vars[weekday].set(weekday in selected)

    def _selected_group_weekdays(self) -> tuple[int, ...] | None:
        """Retorna os dias marcados no formulario do grupo."""

        selected = tuple(
            weekday for _label, weekday in WEEKDAY_OPTIONS if self.group_alert_weekday_vars[weekday].get()
        )
        return selected if selected else None

    def _update_group_alert_weekday_status(self) -> None:
        """Atualiza o texto informativo conforme os dias selecionados."""

        selected = self._selected_group_weekdays()
        if selected is None:
            self.group_alert_status_var.set("Selecione pelo menos um dia da semana para o grupo.")
            return

        labels = ", ".join(WEEKDAY_LABELS[weekday][:3] for weekday in selected)
        self.group_alert_status_var.set(f"Dias ativos: {labels}.")

    @staticmethod
    def _format_notification_window(
        window: tuple[str, str] | None,
        weekdays: tuple[int, ...] | None = None,
    ) -> str:
        """Formata a janela de notificacao para a tabela."""

        if window is None and weekdays is None:
            return "24h / todos os dias"

        parts: list[str] = []
        if window is None:
            parts.append("24h")
        else:
            parts.append(f"{window[0]} ate {window[1]}")

        if weekdays is None:
            parts.append("todos os dias")
        else:
            labels = ", ".join(WEEKDAY_LABELS[weekday][:3] for weekday in weekdays)
            parts.append(labels)

        return " / ".join(parts)

    @staticmethod
    def _normalize_time_text(value: str, field_name: str) -> str:
        """Normaliza um horario HH:MM informado na interface."""

        parts = value.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"{field_name} deve estar no formato HH:MM.")

        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"{field_name} deve estar no formato HH:MM.") from exc

        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError(f"{field_name} deve estar entre 00:00 e 23:59.")

        return f"{hour:02d}:{minute:02d}"

    def _save_equipment_form(self) -> None:
        """Valida o formulario e adiciona ou edita o equipamento."""

        name = self.name_var.get().strip()
        ip_address = self.ip_var.get().strip()
        group = self._normalize_group(self.group_var.get())
        try:
            ping_interval_seconds = self._parse_ping_interval(self.ping_interval_var.get())
        except ValueError as exc:
            messagebox.showwarning("Intervalo invalido", str(exc))
            return

        if not name or not ip_address:
            messagebox.showwarning("Campos obrigatorios", "Informe o nome e o endereco do alvo.")
            return

        if not self._is_valid_monitoring_target(ip_address):
            messagebox.showwarning(
                "Endereco invalido",
                "Informe um IP, nome de host ou URL completa com http:// ou https://.",
            )
            return

        editing_ip = self._editing_ip
        if ip_address in self._monitors and ip_address != editing_ip:
            messagebox.showwarning(
                "Endereco duplicado",
                "Esse endereco ja esta sendo monitorado.",
            )
            return

        if editing_ip is not None:
            self._remove_equipment_by_ip(editing_ip, save=False)

        self._start_monitoring(name, ip_address, group, ping_interval_seconds)
        self._save_equipment_list()

        if editing_ip is not None:
            self._record_event(ip_address, "Cadastro editado", datetime.now())
            self._cancel_equipment_edit()
        else:
            self.name_var.set("")
            self.ip_var.set("")
            self.group_var.set(group)
            self.ping_interval_var.set(self._format_seconds(ping_interval_seconds))

        self._update_summary()

    def _edit_selected(self) -> None:
        """Carrega o alvo selecionado no formulario para edicao."""

        ip_address = self._get_selected_ip()
        if ip_address is None:
            return

        monitor = self._monitors.get(ip_address)
        if monitor is None:
            return

        self._editing_ip = ip_address
        self.name_var.set(monitor.name)
        self.ip_var.set(monitor.ip_address)
        self.group_var.set(monitor.group)
        self.ping_interval_var.set(self._format_seconds(monitor.interval_seconds))
        self.equipment_form.configure(text="Editar alvo")
        self.save_equipment_button.configure(text="Salvar edicao")
        self.cancel_edit_button.configure(state="normal")

    def _cancel_equipment_edit(self) -> None:
        """Sai do modo de edicao e limpa o formulario."""

        self._editing_ip = None
        self.name_var.set("")
        self.ip_var.set("")
        self.group_var.set(DEFAULT_EQUIPMENT_GROUP)
        self.ping_interval_var.set(self._format_seconds(DEFAULT_PING_INTERVAL_SECONDS))
        self.equipment_form.configure(text="Novo alvo")
        self.save_equipment_button.configure(text="Adicionar")
        self.cancel_edit_button.configure(state="disabled")

    def _remove_selected(self) -> None:
        """Para o monitoramento e remove a linha selecionada."""

        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Remover alvo", "Selecione um alvo na tabela.")
            return

        ip_address = self._ip_by_item.get(selection[0])
        if ip_address is None:
            return

        if self._editing_ip == ip_address:
            self._cancel_equipment_edit()

        self._remove_equipment_by_ip(ip_address, save=False)
        self._save_equipment_list()
        self._refresh_group_options()
        self._apply_filters()
        self._refresh_dashboard()
        self._refresh_group_summary()
        self._update_summary()

    def _remove_equipment_by_ip(self, ip_address: str, save: bool = True) -> None:
        """Remove um alvo pelo endereco, parando seu monitor."""

        item_id = self._items_by_ip.pop(ip_address, None)
        if item_id is not None:
            self._ip_by_item.pop(item_id, None)

        monitor = self._monitors.pop(ip_address, None)
        if monitor is not None:
            monitor.stop(wait=False)

        self._last_status.pop(ip_address, None)
        self._runtime_by_ip.pop(ip_address, None)
        self._group_by_ip.pop(ip_address, None)
        self._visible_ips.discard(ip_address)
        self._outage_notifier.clear(ip_address, forget_suppression=True)

        if item_id is not None:
            self.tree.delete(item_id)

        if save:
            self._save_equipment_list()

    def _load_saved_equipment(self) -> None:
        """Carrega do arquivo os equipamentos salvos anteriormente."""

        invalid_records: list[EquipmentRecord] = []
        duplicated_ips: list[str] = []

        for record in self._store.load():
            if not self._is_valid_monitoring_target(record.ip_address):
                invalid_records.append(record)
                continue

            if record.ip_address in self._monitors:
                duplicated_ips.append(record.ip_address)
                continue

            self._start_monitoring(
                record.name,
                record.ip_address,
                record.group,
                record.ping_interval_seconds,
            )

        self._update_summary()

        if invalid_records or duplicated_ips:
            messagebox.showwarning(
                "Alvos ignorados",
                "Algumas linhas do arquivo equipamentos.txt foram ignoradas "
                "por endereco invalido ou duplicado.",
            )

    def _start_monitoring(
        self,
        name: str,
        ip_address: str,
        group: str,
        ping_interval_seconds: float,
    ) -> None:
        """Cria a linha na tabela e inicia o monitoramento periodico."""

        group = self._normalize_group(group)
        monitor = EquipmentMonitor(
            name=name,
            ip_address=ip_address,
            group=group,
            result_callback=self._result_queue.put,
            interval_seconds=ping_interval_seconds,
        )
        monitor.start()

        item_id = self.tree.insert(
            "",
            "end",
            values=(
                group,
                name,
                ip_address,
                STATUS_WAITING,
                "-",
                "-",
                "-",
                "Aguardando primeira leitura",
                "",
            ),
            tags=("waiting",),
        )

        self._monitors[ip_address] = monitor
        self._items_by_ip[ip_address] = item_id
        self._ip_by_item[item_id] = ip_address
        self._group_by_ip[ip_address] = group
        self._runtime_by_ip[ip_address] = EquipmentRuntimeState()
        self._visible_ips.add(ip_address)
        self._refresh_group_options()
        self._apply_filters()
        self._refresh_dashboard()
        self._refresh_group_summary()

    def _save_equipment_list(self) -> None:
        """Salva no arquivo texto os equipamentos monitorados atualmente."""

        records = [
            EquipmentRecord(
                name=monitor.name,
                ip_address=monitor.ip_address,
                group=monitor.group,
                ping_interval_seconds=monitor.interval_seconds,
            )
            for monitor in self._monitors.values()
        ]
        self._store.save(records)

    def _schedule_queue_processing(self) -> None:
        """Agenda a leitura periodica dos resultados vindos das threads."""

        self._process_result_queue()
        self._refresh_live_rows()
        self.after(200, self._schedule_queue_processing)

    def _schedule_analytics_refresh(self) -> None:
        """Atualiza o painel analitico em um ritmo mais leve."""

        if self._analytics_dirty:
            self._refresh_analytics()
            self._analytics_dirty = False

        self.after(10000, self._schedule_analytics_refresh)

    def _process_result_queue(self) -> None:
        """Aplica na tabela todos os resultados pendentes."""

        while True:
            try:
                result = self._result_queue.get_nowait()
            except queue.Empty:
                break

            self._apply_ping_result(result)

    def _apply_ping_result(self, result: PingResult) -> None:
        """Atualiza o estado operacional com o resultado mais recente."""

        item_id = self._items_by_ip.get(result.ip_address)
        if item_id is None:
            return

        state = self._runtime_by_ip.setdefault(result.ip_address, EquipmentRuntimeState())
        state.last_checked_at = result.checked_at
        state.last_latency_ms = result.latency_ms
        state.last_error = "" if result.is_online else (result.error or "Sem resposta")

        maintenance_active = self._is_in_maintenance(state, result.checked_at)
        failure_threshold = self._notification_settings.offline_failure_threshold

        if result.is_online:
            state.failure_streak = 0
            state.first_failure_at = None
            if state.confirmed_status is not True:
                previous_status = state.confirmed_status
                state.confirmed_status = True
                state.offline_since = None
                event = STATUS_ONLINE if previous_status is None else "Conexao restabelecida"
                self._register_transition(
                    result,
                    state,
                    event,
                    count_for_flapping=previous_status is not None,
                )
                if maintenance_active:
                    self._outage_notifier.clear(result.ip_address)
                else:
                    self._outage_notifier.handle_ping_result(result)
        else:
            state.failure_streak += 1
            if state.first_failure_at is None:
                state.first_failure_at = result.checked_at

            if state.failure_streak < failure_threshold:
                state.last_event = (
                    f"Falha {state.failure_streak}/{failure_threshold} "
                    f"em {result.checked_at:%H:%M:%S}"
                )
            else:
                if state.confirmed_status is not False:
                    previous_status = state.confirmed_status
                    state.confirmed_status = False
                    state.offline_since = state.first_failure_at or result.checked_at
                    self._register_transition(
                        result,
                        state,
                        "Offline confirmado",
                        count_for_flapping=previous_status is not None,
                    )

                if maintenance_active:
                    self._outage_notifier.clear(result.ip_address)
                else:
                    notification_result = PingResult(
                        name=result.name,
                        ip_address=result.ip_address,
                        group=result.group,
                        is_online=result.is_online,
                        latency_ms=result.latency_ms,
                        checked_at=result.checked_at,
                        error=result.error,
                        outage_started_at=state.offline_since,
                    )
                    self._outage_notifier.handle_ping_result(notification_result)

        if state.confirmed_status is not None:
            self._last_status[result.ip_address] = state.confirmed_status

        self._update_display_status(result.ip_address)
        self._render_equipment_row(result.ip_address)
        self._apply_filters()
        self._update_summary()

    def _register_transition(
        self,
        result: PingResult,
        state: EquipmentRuntimeState,
        event: str,
        count_for_flapping: bool = True,
    ) -> None:
        """Registra uma mudanca confirmada de estado."""

        if count_for_flapping:
            state.transition_times.append(result.checked_at)
            self._prune_transitions(state, result.checked_at)

        was_flapping = state.is_flapping
        state.is_flapping = len(state.transition_times) >= (
            self._notification_settings.flapping_transition_count
        )
        state.last_event = f"{event} as {result.checked_at:%H:%M:%S}"
        self._record_event(result.ip_address, event, result.checked_at)

        if state.is_flapping and not was_flapping:
            event_text = "Oscilacao detectada"
            state.last_event = f"{event_text} as {result.checked_at:%H:%M:%S}"
            self._record_event(result.ip_address, event_text, result.checked_at)

    def _prune_transitions(self, state: EquipmentRuntimeState, now: datetime) -> None:
        """Remove mudancas fora da janela de oscilacao."""

        window = timedelta(minutes=self._notification_settings.flapping_window_minutes)
        state.transition_times = [
            changed_at for changed_at in state.transition_times if now - changed_at <= window
        ]

    def _update_display_status(self, ip_address: str) -> None:
        """Atualiza o status visual derivado do estado confirmado."""

        state = self._runtime_by_ip.get(ip_address)
        if state is None:
            return

        now = datetime.now()
        self._prune_transitions(state, now)
        state.is_flapping = len(state.transition_times) >= (
            self._notification_settings.flapping_transition_count
        )

        if self._is_in_maintenance(state, now):
            state.display_status = STATUS_MAINTENANCE
        elif state.is_flapping:
            state.display_status = STATUS_FLAPPING
        elif state.confirmed_status is True:
            state.display_status = STATUS_UNSTABLE if state.failure_streak else STATUS_ONLINE
        elif state.confirmed_status is False:
            state.display_status = STATUS_OFFLINE
        else:
            state.display_status = STATUS_WAITING

    def _operational_status_for_counts(self, state: EquipmentRuntimeState) -> str:
        """Retorna o status real usado nos contadores, ignorando manutencao."""

        if state.is_flapping:
            return STATUS_FLAPPING
        if state.confirmed_status is True:
            return STATUS_UNSTABLE if state.failure_streak else STATUS_ONLINE
        if state.confirmed_status is False:
            return STATUS_OFFLINE

        return STATUS_WAITING

    def _render_equipment_row(self, ip_address: str) -> None:
        """Renderiza uma linha da tabela com o estado operacional atual."""

        item_id = self._items_by_ip.get(ip_address)
        monitor = self._monitors.get(ip_address)
        state = self._runtime_by_ip.get(ip_address)
        if item_id is None or monitor is None or state is None:
            return

        latency = f"{state.last_latency_ms:.0f} ms" if state.last_latency_ms is not None else "-"
        checked_at = state.last_checked_at.strftime("%H:%M:%S") if state.last_checked_at else "-"
        offline_for = (
            self._format_elapsed(datetime.now() - state.offline_since)
            if state.offline_since and state.confirmed_status is False
            else "-"
        )

        self.tree.item(
            item_id,
            values=(
                monitor.group,
                monitor.name,
                monitor.ip_address,
                state.display_status,
                latency,
                checked_at,
                offline_for,
                state.last_event,
                self._build_row_message(state),
            ),
            tags=(self._status_tag(state.display_status),),
        )

    def _build_row_message(self, state: EquipmentRuntimeState) -> str:
        """Monta a mensagem curta exibida na tabela."""

        if self._is_in_maintenance(state, datetime.now()):
            remaining = state.maintenance_until - datetime.now() if state.maintenance_until else None
            remaining_text = self._format_elapsed(remaining) if remaining else "-"
            return f"Alertas silenciados por {remaining_text}"

        if state.display_status == STATUS_UNSTABLE:
            threshold = self._notification_settings.offline_failure_threshold
            return f"Falha {state.failure_streak}/{threshold}: {state.last_error}"

        if state.display_status in {STATUS_OFFLINE, STATUS_FLAPPING}:
            return state.last_error or "Sem resposta"

        return ""

    def _refresh_live_rows(self) -> None:
        """Atualiza duracoes e paineis mesmo quando nenhuma leitura nova chega."""

        for ip_address in list(self._runtime_by_ip):
            self._update_display_status(ip_address)
            self._render_equipment_row(ip_address)

        self._refresh_dashboard()
        self._refresh_group_summary()
        self._update_summary()

    def _refresh_dashboard(self) -> None:
        """Atualiza os cards de status."""

        counts = self._count_statuses(list(self._monitors))
        self.dashboard_vars["total"].set(str(len(self._monitors)))
        self.dashboard_vars["online"].set(str(counts[STATUS_ONLINE]))
        self.dashboard_vars["offline"].set(str(counts[STATUS_OFFLINE]))
        self.dashboard_vars["unstable"].set(str(counts[STATUS_UNSTABLE]))
        self.dashboard_vars["flapping"].set(str(counts[STATUS_FLAPPING]))
        self.dashboard_vars["waiting"].set(str(counts[STATUS_WAITING]))
        self.dashboard_vars["maintenance"].set(str(counts[STATUS_MAINTENANCE]))

    def _refresh_analytics(self) -> None:
        """Atualiza os graficos da aba de painel."""

        events = self._filtered_analytics_events()
        signature = self._analytics_signature(events)
        if signature == self._analytics_last_signature:
            return

        self._analytics_last_signature = signature
        self.analytics_total_events_var.set(str(len(self._event_history)))
        self.analytics_group_events_var.set(str(len(events)))
        self.analytics_last_event_var.set(self._format_last_event_summary(events))
        self.analytics_top_group_var.set(self._top_group_label(events))
        if hasattr(self, "status_chart"):
            counts = self._count_statuses(list(self._monitors))
            self._draw_status_chart(self.status_chart, counts)
        if hasattr(self, "group_chart"):
            self._draw_group_chart(self.group_chart, events)
        if hasattr(self, "hour_chart"):
            self._draw_hour_chart(self.hour_chart, events)
        if hasattr(self, "analytics_events_tree"):
            self._refresh_analytics_events_tree(events)
        if hasattr(self, "offline_chart"):
            self._draw_offline_chart(self.offline_chart, events)
        if hasattr(self, "event_type_chart"):
            self._draw_event_type_chart(self.event_type_chart, events)
        if hasattr(self, "top_equipment_tree"):
            self._refresh_top_equipment_tree(events)
        self._analytics_dirty = False

    def _analytics_signature(self, events: list[tuple[datetime, str, str, str]]) -> tuple:
        """Cria uma assinatura leve do estado analitico atual."""

        history = self._event_history
        return (
            self.analytics_group_var.get().strip().lower(),
            self.analytics_range_var.get().strip().lower(),
            len(history),
            history[0] if history else None,
            history[-1] if history else None,
            len(events),
            events[0] if events else None,
            events[-1] if events else None,
        )

    def _filtered_analytics_events(self) -> list[tuple[datetime, str, str, str]]:
        """Filtra o historico recente por grupo e periodo."""

        now = datetime.now()
        range_name = self.analytics_range_var.get().strip().lower()
        cutoff: datetime | None
        if range_name == "7d":
            cutoff = now - timedelta(days=7)
        elif range_name == "30d":
            cutoff = now - timedelta(days=30)
        elif range_name == "24h":
            cutoff = now - timedelta(days=1)
        else:
            cutoff = None

        selected_group = self.analytics_group_var.get().strip()
        events: list[tuple[datetime, str, str, str]] = []
        for event in self._event_history:
            happened_at, _name, _event, group = event
            if selected_group != GROUP_FILTER_ALL and group != selected_group:
                continue
            if cutoff is not None and happened_at < cutoff:
                continue
            events.append(event)

        return events

    def _format_last_event_summary(self, events: list[tuple[datetime, str, str, str]]) -> str:
        """Resume o evento mais recente filtrado."""

        if not events:
            return "Sem eventos no filtro"

        happened_at, name, event, group = events[0]
        return f"{happened_at:%H:%M} | {group} | {name} | {event}"

    def _top_group_label(self, events: list[tuple[datetime, str, str, str]]) -> str:
        """Retorna o grupo com mais eventos no filtro."""

        if not events:
            return "-"

        counts: dict[str, int] = {}
        for _happened_at, _name, _event, group in events:
            counts[group] = counts.get(group, 0) + 1

        top_group, total = max(counts.items(), key=lambda item: item[1])
        return f"{top_group} ({total})"

    def _refresh_analytics_events_tree(
        self,
        events: list[tuple[datetime, str, str, str]],
    ) -> None:
        """Atualiza a lista de eventos filtrados."""

        for item_id in self.analytics_events_tree.get_children():
            self.analytics_events_tree.delete(item_id)

        for happened_at, name, event, group in events[:15]:
            self.analytics_events_tree.insert(
                "",
                "end",
                values=(happened_at.strftime("%H:%M:%S"), group, name, event),
            )

    def _draw_offline_chart(
        self,
        chart: dict[str, object],
        events: list[tuple[datetime, str, str, str]],
    ) -> None:
        """Desenha o tempo offline acumulado por grupo."""

        axis = chart["axis"]
        canvas = chart["canvas"]
        axis.clear()

        totals: dict[str, float] = {}
        now = datetime.now()
        for ip_address, state in self._runtime_by_ip.items():
            monitor_group = self._group_by_ip.get(ip_address, DEFAULT_EQUIPMENT_GROUP)
            if state.confirmed_status is False and state.offline_since is not None:
                totals[monitor_group] = totals.get(monitor_group, 0.0) + max(
                    0.0, (now - state.offline_since).total_seconds() / 60.0
                )

        items = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:8]
        if not items:
            items = [("Sem offline", 0.0)]

        labels = [group if len(group) <= 18 else f"{group[:15]}..." for group, _ in items]
        values = [value for _group, value in items]
        colors = sns.color_palette("rocket", len(items))
        axis.barh(labels[::-1], values[::-1], color=colors[::-1], edgecolor="#102033", linewidth=0.3)
        axis.set_title("Tempo offline por grupo", loc="left", fontsize=12, fontweight="bold", color="#102033", pad=10)
        axis.set_xlabel("Minutos offline", fontsize=9, labelpad=8)
        axis.grid(axis="x", linestyle="--", alpha=0.18)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.spines["left"].set_color("#d1d5db")
        axis.spines["bottom"].set_color("#d1d5db")
        axis.tick_params(axis="x", labelsize=9)
        axis.tick_params(axis="y", labelsize=9)
        canvas.draw_idle()

    def _draw_event_type_chart(
        self,
        chart: dict[str, object],
        events: list[tuple[datetime, str, str, str]],
    ) -> None:
        """Desenha a distribuicao de tipos de evento."""

        axis = chart["axis"]
        canvas = chart["canvas"]
        axis.clear()

        counts: dict[str, int] = {}
        for _happened_at, _name, event, _group in events:
            event_name = event.split(" as ", 1)[0].strip()
            counts[event_name] = counts.get(event_name, 0) + 1

        items = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]
        if not items:
            items = [("Sem eventos", 0)]

        labels = [label if len(label) <= 18 else f"{label[:15]}..." for label, _ in items]
        values = [value for _label, value in items]
        if sum(values) <= 0:
            axis.text(0.5, 0.5, "Sem dados para este filtro", ha="center", va="center", fontsize=10, color="#6b7280", transform=axis.transAxes)
            axis.set_title("Tipos de evento", loc="left", fontsize=12, fontweight="bold", color="#102033", pad=10)
            axis.axis("off")
            canvas.draw_idle()
            return
        colors = sns.color_palette("viridis", len(items))
        axis.pie(
            values,
            labels=labels,
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 8 else "",
            startangle=90,
            colors=colors,
            textprops={"fontsize": 8, "color": "#102033"},
        )
        axis.set_title("Tipos de evento", loc="left", fontsize=12, fontweight="bold", color="#102033", pad=10)
        axis.axis("equal")
        canvas.draw_idle()

    def _refresh_top_equipment_tree(
        self,
        events: list[tuple[datetime, str, str, str]],
    ) -> None:
        """Atualiza a lista dos equipamentos mais recorrentes no historico."""

        for item_id in self.top_equipment_tree.get_children():
            self.top_equipment_tree.delete(item_id)

        counts: dict[tuple[str, str], int] = {}
        for _happened_at, name, _event, group in events:
            key = (name, group)
            counts[key] = counts.get(key, 0) + 1

        for (name, group), total in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10]:
            self.top_equipment_tree.insert("", "end", values=(name, group, total))

    def _draw_status_chart(self, chart: dict[str, object], counts: dict[str, int]) -> None:
        """Desenha um grafico de barras com o estado atual."""

        axis = chart["axis"]
        canvas = chart["canvas"]
        axis.clear()

        labels = ["Online", "Offline", "Instavel", "Oscilando", "Manutencao", "Aguardando"]
        values = [
            counts[STATUS_ONLINE],
            counts[STATUS_OFFLINE],
            counts[STATUS_UNSTABLE],
            counts[STATUS_FLAPPING],
            counts[STATUS_MAINTENANCE],
            counts[STATUS_WAITING],
        ]
        colors = ["#137333", "#b3261e", "#b45309", "#8a4b00", "#596579", "#5f6368"]
        bars = axis.bar(labels, values, color=colors, edgecolor="#102033", linewidth=0.3)
        axis.set_title("Estado atual", loc="left", fontsize=12, fontweight="bold", color="#102033", pad=10)
        axis.set_ylabel("Alvos", fontsize=9, labelpad=8)
        axis.grid(axis="y", linestyle="--", alpha=0.18)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.spines["left"].set_color("#d1d5db")
        axis.spines["bottom"].set_color("#d1d5db")
        axis.tick_params(axis="x", labelrotation=15, labelsize=9, pad=2)
        axis.tick_params(axis="y", labelsize=9)
        axis.set_ylim(0, max(values + [1]) * 1.2)
        for bar, value in zip(bars, values):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.05,
                str(value),
                ha="center",
                va="bottom",
                fontsize=9,
                color="#102033",
            )
        canvas.draw_idle()

    def _draw_group_chart(self, chart: dict[str, object], events: list[tuple[datetime, str, str, str]]) -> None:
        """Desenha um ranking dos grupos com mais eventos recentes."""

        axis = chart["axis"]
        canvas = chart["canvas"]
        axis.clear()

        group_counts: dict[str, int] = {}
        for _happened_at, _name, _event, group in events:
            group_counts[group] = group_counts.get(group, 0) + 1

        items = sorted(group_counts.items(), key=lambda item: item[1], reverse=True)[:8]
        if not items:
            items = [("Sem eventos", 0)]

        labels = [group if len(group) <= 18 else f"{group[:15]}..." for group, _count in items]
        values = [count for _group, count in items]
        colors = sns.color_palette("Blues", len(items))
        axis.barh(labels[::-1], values[::-1], color=colors[::-1], edgecolor="#102033", linewidth=0.3)
        axis.set_title("Eventos por grupo", loc="left", fontsize=12, fontweight="bold", color="#102033", pad=10)
        axis.set_xlabel("Eventos", fontsize=9, labelpad=8)
        axis.grid(axis="x", linestyle="--", alpha=0.18)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.spines["left"].set_color("#d1d5db")
        axis.spines["bottom"].set_color("#d1d5db")
        axis.tick_params(axis="x", labelsize=9)
        axis.tick_params(axis="y", labelsize=9)
        canvas.draw_idle()

    def _draw_hour_chart(self, chart: dict[str, object], events: list[tuple[datetime, str, str, str]]) -> None:
        """Desenha um grafico simples por faixa horaria."""

        axis = chart["axis"]
        canvas = chart["canvas"]
        axis.clear()

        hour_counts = [0] * 24
        for happened_at, _name, _event, _group in events:
            hour_counts[happened_at.hour] += 1

        bucket_labels = []
        bucket_values = []
        for start_hour in range(0, 24, 2):
            bucket_labels.append(f"{start_hour:02d}-{start_hour + 1:02d}")
            bucket_values.append(hour_counts[start_hour] + hour_counts[start_hour + 1])

        colors = sns.color_palette("crest", len(bucket_values))
        axis.bar(bucket_labels, bucket_values, color=colors, edgecolor="#102033", linewidth=0.3)
        axis.set_title("Eventos por hora", loc="left", fontsize=12, fontweight="bold", color="#102033", pad=10)
        axis.set_ylabel("Eventos", fontsize=9, labelpad=8)
        axis.grid(axis="y", linestyle="--", alpha=0.18)
        axis.tick_params(axis="x", labelrotation=0, labelsize=8, pad=2)
        axis.tick_params(axis="y", labelsize=9)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.spines["left"].set_color("#d1d5db")
        axis.spines["bottom"].set_color("#d1d5db")
        canvas.draw_idle()

    def _refresh_group_summary(self) -> None:
        """Recalcula a tabela de grupos."""

        if not hasattr(self, "group_summary_tree"):
            return

        for item_id in self.group_summary_tree.get_children():
            self.group_summary_tree.delete(item_id)

        groups = sorted(set(self._group_by_ip.values()) or {DEFAULT_EQUIPMENT_GROUP})
        for group in groups:
            ips = [
                ip_address
                for ip_address, equipment_group in self._group_by_ip.items()
                if equipment_group == group
            ]
            counts = self._count_statuses(ips)
            self.group_summary_tree.insert(
                "",
                "end",
                iid=f"group::{group}",
                values=(
                    group,
                    len(ips),
                    counts[STATUS_ONLINE],
                    counts[STATUS_OFFLINE],
                    counts[STATUS_FLAPPING],
                    counts[STATUS_WAITING],
                ),
            )

    def _count_statuses(self, ip_addresses: list[str]) -> dict[str, int]:
        """Conta status operacionais e alvos em manutencao."""

        counts = {
            STATUS_ONLINE: 0,
            STATUS_OFFLINE: 0,
            STATUS_UNSTABLE: 0,
            STATUS_FLAPPING: 0,
            STATUS_WAITING: 0,
            STATUS_MAINTENANCE: 0,
        }
        for ip_address in ip_addresses:
            state = self._runtime_by_ip.get(ip_address)
            status = self._operational_status_for_counts(state) if state else STATUS_WAITING
            counts[status] = counts.get(status, 0) + 1
            if state is not None and self._is_in_maintenance(state, datetime.now()):
                counts[STATUS_MAINTENANCE] += 1

        return counts

    def _record_event(self, ip_address: str, event: str, happened_at: datetime) -> None:
        """Adiciona um evento ao historico recente."""

        monitor = self._monitors.get(ip_address)
        if monitor is None:
            return

        self._event_history.insert(0, (happened_at, monitor.name, event, monitor.group))
        del self._event_history[ANALYTICS_HISTORY_LIMIT:]
        self._analytics_dirty = True
        self._refresh_event_history()

    def _refresh_event_history(self) -> None:
        """Renderiza os eventos recentes."""

        if not hasattr(self, "events_tree"):
            return

        for item_id in self.events_tree.get_children():
            self.events_tree.delete(item_id)

        for happened_at, name, event, group in self._event_history[:8]:
            self.events_tree.insert(
                "",
                "end",
                values=(happened_at.strftime("%H:%M:%S"), name, event, group),
            )

    def _status_tag(self, status: str) -> str:
        """Converte status visual em tag da tabela."""

        return {
            STATUS_ONLINE: "online",
            STATUS_OFFLINE: "offline",
            STATUS_UNSTABLE: "unstable",
            STATUS_FLAPPING: "flapping",
            STATUS_MAINTENANCE: "maintenance",
            STATUS_WAITING: "waiting",
        }.get(status, "waiting")

    def _select_group_from_summary(self, _event: tk.Event) -> None:
        """Filtra a tabela pelo grupo selecionado no resumo."""

        selection = self.group_summary_tree.selection()
        if not selection:
            return

        values = self.group_summary_tree.item(selection[0], "values")
        if not values:
            return

        group = str(values[0])
        self.group_filter_var.set(group)
        self.maintenance_group_var.set(group)
        self._apply_filters()

    def _start_maintenance(self) -> None:
        """Silencia alertas do alvo selecionado por alguns minutos."""

        ip_address = self._get_selected_ip()
        if ip_address is None:
            return

        minutes = self._get_maintenance_minutes()
        if minutes is None:
            return

        self._apply_maintenance_to_ips(
            [ip_address],
            minutes,
            event_text=f"Manutencao por {minutes} min",
        )

    def _start_group_maintenance(self) -> None:
        """Silencia alertas dos alvos do grupo escolhido."""

        minutes = self._get_maintenance_minutes()
        if minutes is None:
            return

        group = self._normalize_group(self.maintenance_group_var.get())
        ip_addresses = self._ips_for_group(group)
        if not ip_addresses:
            messagebox.showinfo("Manutencao", "Nenhum alvo encontrado nesse grupo.")
            return

        self._apply_maintenance_to_ips(
            ip_addresses,
            minutes,
            event_text=f"Manutencao do grupo {group} por {minutes} min",
        )

    def _start_all_maintenance(self) -> None:
        """Silencia alertas de todos os alvos monitorados."""

        minutes = self._get_maintenance_minutes()
        if minutes is None:
            return

        ip_addresses = list(self._monitors)
        if not ip_addresses:
            messagebox.showinfo("Manutencao", "Nenhum alvo monitorado.")
            return

        self._apply_maintenance_to_ips(
            ip_addresses,
            minutes,
            event_text=f"Manutencao geral por {minutes} min",
        )

    def _get_maintenance_minutes(self) -> int | None:
        """Le e valida os minutos de manutencao informados."""

        try:
            return self._parse_positive_int(
                self.maintenance_minutes_var.get(),
                "Manutencao",
            )
        except ValueError as exc:
            messagebox.showwarning("Manutencao invalida", str(exc))
            return None

    def _apply_maintenance_to_ips(
        self,
        ip_addresses: list[str],
        minutes: int,
        event_text: str,
    ) -> None:
        """Aplica uma janela de manutencao para varios alvos."""

        now = datetime.now()
        maintenance_until = now + timedelta(minutes=minutes)

        for ip_address in ip_addresses:
            state = self._runtime_by_ip.setdefault(ip_address, EquipmentRuntimeState())
            state.maintenance_until = maintenance_until
            state.last_event = f"Manutencao ate {maintenance_until:%H:%M:%S}"
            self._outage_notifier.clear(ip_address, reset_at=maintenance_until)
            self._record_event(ip_address, event_text, now)
            self._update_display_status(ip_address)
            self._render_equipment_row(ip_address)

        self._refresh_after_maintenance_change()

    def _end_maintenance(self) -> None:
        """Encerra a janela de manutencao do alvo selecionado."""

        ip_address = self._get_selected_ip()
        if ip_address is None:
            return

        self._clear_maintenance_for_ips([ip_address], "Manutencao encerrada")

    def _end_group_maintenance(self) -> None:
        """Encerra a manutencao dos alvos do grupo escolhido."""

        group = self._normalize_group(self.maintenance_group_var.get())
        ip_addresses = self._ips_for_group(group)
        if not ip_addresses:
            messagebox.showinfo("Manutencao", "Nenhum alvo encontrado nesse grupo.")
            return

        self._clear_maintenance_for_ips(
            ip_addresses,
            f"Manutencao do grupo {group} encerrada",
        )

    def _end_all_maintenance(self) -> None:
        """Encerra a manutencao de todos os alvos."""

        ip_addresses = list(self._monitors)
        if not ip_addresses:
            messagebox.showinfo("Manutencao", "Nenhum alvo monitorado.")
            return

        self._clear_maintenance_for_ips(ip_addresses, "Manutencao geral encerrada")

    def _clear_maintenance_for_ips(self, ip_addresses: list[str], event_text: str) -> None:
        """Remove a janela de manutencao de varios alvos."""

        now = datetime.now()
        changed = False

        for ip_address in ip_addresses:
            state = self._runtime_by_ip.get(ip_address)
            if state is None or state.maintenance_until is None:
                continue

            state.maintenance_until = None
            state.last_event = f"{event_text} as {now:%H:%M:%S}"
            self._outage_notifier.clear(ip_address, reset_at=now)
            self._record_event(ip_address, event_text, now)
            self._update_display_status(ip_address)
            self._render_equipment_row(ip_address)
            changed = True

        if not changed:
            messagebox.showinfo("Manutencao", "Nenhum alvo estava em manutencao.")

        self._refresh_after_maintenance_change()

    def _ips_for_group(self, group: str) -> list[str]:
        """Retorna os enderecos dos alvos de um grupo."""

        return [
            ip_address
            for ip_address, equipment_group in self._group_by_ip.items()
            if equipment_group == group
        ]

    def _refresh_after_maintenance_change(self) -> None:
        """Atualiza os paineis depois de aplicar ou encerrar manutencao."""

        self._refresh_dashboard()
        self._refresh_group_summary()
        self._apply_filters()

    def _get_selected_ip(self) -> str | None:
        """Retorna o endereco selecionado na tabela principal."""

        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Alvo", "Selecione um alvo na tabela.")
            return None

        return self._ip_by_item.get(selection[0])

    def _update_summary(self) -> None:
        """Atualiza o texto de resumo no topo da janela."""

        total = len(self._monitors)
        if total == 0:
            self.summary_var.set("Nenhum alvo monitorado")
            return

        filtered_ips = self._get_filtered_ips()
        visible_total = len(filtered_ips)
        counts = self._count_statuses(filtered_ips)
        filters: list[str] = []
        if self.group_filter_var.get() != GROUP_FILTER_ALL:
            filters.append(f"Grupo: {self.group_filter_var.get()}")
        if self.status_filter_var.get() != STATUS_FILTER_ALL:
            filters.append(f"Status: {self.status_filter_var.get()}")
        if self.search_var.get().strip():
            filters.append(f"Busca: {self.search_var.get().strip()}")

        filter_text = " | ".join(filters) if filters else "Todos os alvos"

        self.summary_var.set(
            f"{filter_text} | Exibindo {visible_total} de {total} | "
            f"Online: {counts[STATUS_ONLINE]} | Offline: {counts[STATUS_OFFLINE]} | "
            f"Instavel: {counts[STATUS_UNSTABLE]} | Oscilando: {counts[STATUS_FLAPPING]}"
        )

    def _normalize_group(self, value: str) -> str:
        """Padroniza o grupo informado pelo usuario."""

        return value.strip() or DEFAULT_EQUIPMENT_GROUP

    def _refresh_group_options(self) -> None:
        """Atualiza as listas de grupos usadas no cadastro e no filtro."""

        groups = {
            self._normalize_group(group)
            for group in self._group_by_ip.values()
            if self._normalize_group(group)
        }
        groups.add(self._normalize_group(self.group_var.get()))
        groups.add(DEFAULT_EQUIPMENT_GROUP)

        ordered_groups = [DEFAULT_EQUIPMENT_GROUP]
        ordered_groups.extend(
            group for group in sorted(groups) if group != DEFAULT_EQUIPMENT_GROUP
        )

        self.group_entry.configure(values=tuple(ordered_groups))
        if hasattr(self, "maintenance_group_combo"):
            self.maintenance_group_combo.configure(values=tuple(ordered_groups))
        if hasattr(self, "analytics_group_combo"):
            analytics_groups = (GROUP_FILTER_ALL, *ordered_groups)
            self.analytics_group_combo.configure(values=analytics_groups)
            if self.analytics_group_var.get() not in analytics_groups:
                self.analytics_group_var.set(GROUP_FILTER_ALL)

        if self.maintenance_group_var.get() not in ordered_groups:
            self.maintenance_group_var.set(ordered_groups[0])

        filter_values = (GROUP_FILTER_ALL, *ordered_groups)
        self.group_filter_combo.configure(values=filter_values)

        if self.group_filter_var.get() not in filter_values:
            self.group_filter_var.set(GROUP_FILTER_ALL)

        self._refresh_group_alert_options()

    def _clear_filters(self) -> None:
        """Limpa filtros de grupo, status e busca."""

        self.group_filter_var.set(GROUP_FILTER_ALL)
        self.status_filter_var.set(STATUS_FILTER_ALL)
        self.search_var.set("")
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Aplica filtros de grupo, status e busca."""

        for ip_address, item_id in self._items_by_ip.items():
            should_show = self._matches_filters(ip_address)

            if should_show and ip_address not in self._visible_ips:
                self.tree.reattach(item_id, "", "end")
                self._visible_ips.add(ip_address)
            elif not should_show and ip_address in self._visible_ips:
                self.tree.detach(item_id)
                self._visible_ips.discard(ip_address)

        self._update_summary()

    def _matches_filters(self, ip_address: str) -> bool:
        """Indica se um alvo passa pelos filtros atuais."""

        monitor = self._monitors.get(ip_address)
        state = self._runtime_by_ip.get(ip_address)
        if monitor is None:
            return False

        selected_group = self.group_filter_var.get()
        if selected_group != GROUP_FILTER_ALL and monitor.group != selected_group:
            return False

        selected_status = self.status_filter_var.get()
        status = state.display_status if state else STATUS_WAITING
        if selected_status != STATUS_FILTER_ALL and status != selected_status:
            return False

        query = self.search_var.get().strip().lower()
        if query:
            searchable_text = f"{monitor.name} {monitor.ip_address} {monitor.group}".lower()
            if query not in searchable_text:
                return False

        return True

    def _get_filtered_ips(self) -> list[str]:
        """Retorna os enderecos considerados pelo resumo atual."""

        return [ip_address for ip_address in self._monitors if self._matches_filters(ip_address)]

    @staticmethod
    def _parse_positive_int(value: str, field_name: str) -> int:
        """Valida um inteiro positivo informado pelo usuario."""

        try:
            parsed = int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{field_name}: use um numero inteiro maior que zero.") from exc

        if parsed <= 0:
            raise ValueError(f"{field_name}: use um numero inteiro maior que zero.")

        return parsed

    @staticmethod
    def _parse_ping_interval(value: str) -> float:
        """Valida o intervalo de monitoramento informado no cadastro."""

        try:
            parsed = float(value.strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError("Informe o intervalo de monitoramento em segundos.") from exc

        if not math.isfinite(parsed):
            raise ValueError("Informe um intervalo de monitoramento valido.")
        if parsed < 1:
            raise ValueError("O intervalo de monitoramento deve ser de pelo menos 1 segundo.")
        if parsed > 3600:
            raise ValueError(
                "O intervalo de monitoramento deve ser menor ou igual a 3600 segundos."
            )

        return parsed

    @staticmethod
    def _format_seconds(value: float) -> str:
        """Formata segundos para exibicao."""

        seconds = float(value)
        if seconds.is_integer():
            return str(int(seconds))

        return f"{seconds:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _format_elapsed(delta: timedelta) -> str:
        """Formata uma duracao curta para a interface."""

        total_seconds = max(0, int(delta.total_seconds()))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {seconds:02d}s"

        return f"{seconds}s"

    @staticmethod
    def _is_in_maintenance(state: EquipmentRuntimeState, now: datetime) -> bool:
        """Confere se uma janela de manutencao ainda esta ativa."""

        if state.maintenance_until is None:
            return False

        if now >= state.maintenance_until:
            state.maintenance_until = None
            return False

        return True

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

    @classmethod
    def _is_valid_monitoring_target(cls, value: str) -> bool:
        """Confere se o alvo pode ser monitorado por ping ou HTTP."""

        target = value.strip()
        if not target:
            return False

        if cls._is_valid_ip(target) or cls._is_valid_url(target):
            return True

        if "://" in target or "/" in target:
            return False

        return cls._is_valid_hostname(target)

    @staticmethod
    def _is_valid_hostname(value: str) -> bool:
        """Confere nomes de host simples para monitoramento via ping."""

        hostname = value.strip().rstrip(".")
        if not hostname or len(hostname) > 253:
            return False

        labels = hostname.split(".")
        for label in labels:
            if not label or len(label) > 63:
                return False
            if label.startswith("-") or label.endswith("-"):
                return False
            if not all(character.isalnum() or character in {"-", "_"} for character in label):
                return False

        return True

    @staticmethod
    def _is_valid_url(value: str) -> bool:
        """Confere se a URL informada tem protocolo e endereco."""

        parsed = urlparse(value)
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
