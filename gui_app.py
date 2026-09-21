from __future__ import annotations

import ctypes
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
EVENT_BATCH = 25
GUI_ROW_LIMIT = 1200
LOG_LINE_LIMIT = 400
FILTER_BATCH = 80
LIVE_TALLY_MS = 80
PENDING_TALLY_STATUSES = frozenset({"Pending", "Gathered", "Preview", "Copying"})
SENT_TALLY_STATUSES = frozenset({"Copied", "Skipped"})

ALL_COLUMNS = (
    "time",
    "id",
    "username",
    "name",
    "mobile",
    "amount",
    "type",
    "bank",
    "acc_name",
    "acc_no",
    "bsb",
    "pay_id",
    "bank_lock",
    "method",
    "brand",
    "created",
    "processed",
    "status",
    "detail",
)
DEPOSIT_CORE_COLUMNS = (
    "time",
    "id",
    "username",
    "name",
    "amount",
    "type",
    "bank",
    "method",
    "brand",
    "created",
    "processed",
    "status",
)
COLUMN_HEADINGS = {
    "time": ("Time", 130),
    "id": ("ID", 120),
    "username": ("Username", 100),
    "name": ("Name", 160),
    "mobile": ("Mobile", 110),
    "amount": ("Amount", 80),
    "type": ("Type", 80),
    "bank": ("Bank", 90),
    "acc_name": ("Acc Name", 150),
    "acc_no": ("Acc No", 110),
    "bsb": ("BSB", 70),
    "pay_id": ("PayID", 160),
    "bank_lock": ("BankLock", 70),
    "method": ("Method", 70),
    "brand": ("Brand", 120),
    "created": ("Created", 120),
    "processed": ("Processed", 120),
    "status": ("Sheet", 80),
    "detail": ("Detail", 220),
}
TREE_TAGS_LIGHT = (
    ("Copied", "#15803d"),
    ("Preview", "#1d4ed8"),
    ("Gathered", "#0f766e"),
    ("Pending", "#0f766e"),
    ("Copying", "#a16207"),
    ("Failed", "#b91c1c"),
    ("Skipped", "#6b7280"),
)
TREE_TAGS_DARK = (
    ("Copied", "#34d399"),
    ("Preview", "#67e8f9"),
    ("Gathered", "#22d3ee"),
    ("Pending", "#22d3ee"),
    ("Copying", "#fbbf24"),
    ("Failed", "#fb7185"),
    ("Skipped", "#71717a"),
)
TREE_TAGS = TREE_TAGS_LIGHT
THEMES = {
    "light": {
        "root": "#eef2f7",
        "card": "#ffffff",
        "header": "#0f2744",
        "header_fg": "#ffffff",
        "header_sub": "#c5d4e8",
        "title": "#0f2744",
        "muted": "#5b6b7c",
        "tally_bg": "#f4f7fb",
        "tally_fg": "#0f2744",
        "input_bg": "#ffffff",
        "input_fg": "#0f172a",
        "button_bg": "#e2e8f0",
        "button_fg": "#0f172a",
        "run_bg": "#0f766e",
        "run_fg": "#f0fdfa",
        "tree_bg": "#ffffff",
        "tree_fg": "#0f172a",
        "tree_head_bg": "#e2e8f0",
        "tree_head_fg": "#0f2744",
        "tree_select": "#bfdbfe",
        "log_bg": "#0f2744",
        "log_fg": "#e6eef8",
        "tab": "#dbe4ee",
        "tab_sel": "#ffffff",
        "tab_fg": "#334155",
        "tab_sel_fg": "#0f2744",
        "switch_on": "#38bdf8",
        "switch_off": "#94a3b8",
        "border": "#d4dbe6",
        "accent": "#0891b2",
    },
    "dark": {
        "root": "#050505",
        "card": "#0c0c0c",
        "header": "#050505",
        "header_fg": "#f4f4f5",
        "header_sub": "#a1a1aa",
        "title": "#f4f4f5",
        "muted": "#a1a1aa",
        "tally_bg": "#141414",
        "tally_fg": "#f4f4f5",
        "input_bg": "#141414",
        "input_fg": "#f4f4f5",
        "button_bg": "#141414",
        "button_fg": "#f4f4f5",
        "run_bg": "#0891b2",
        "run_fg": "#ffffff",
        "tree_bg": "#0c0c0c",
        "tree_fg": "#f4f4f5",
        "tree_head_bg": "#141414",
        "tree_head_fg": "#a1a1aa",
        "tree_select": "#164e63",
        "log_bg": "#0c0c0c",
        "log_fg": "#a1a1aa",
        "tab": "#141414",
        "tab_sel": "#0c0c0c",
        "tab_fg": "#a1a1aa",
        "tab_sel_fg": "#67e8f9",
        "switch_on": "#67e8f9",
        "switch_off": "#3f3f46",
        "border": "#262626",
        "accent": "#67e8f9",
    },
}
STATUS_RANK = {
    "Failed": 1,
    "Gathered": 2,
    "Pending": 2,
    "Preview": 2,
    "Copying": 3,
    "Skipped": 4,
    "Copied": 4,
}

from src.config import (
    GOOGLE_SHEET_SLOTS,
    Settings,
    active_login_slot,
    google_sheet_env_key,
    google_sheet_url,
    clear_login_account,
    load_login_accounts,
    normalize_dashboard_url,
    normalize_google_sheet_id,
    persist_env_values,
    persist_gui_theme,
    persist_login_account,
    service_account_email,
    load_gui_theme,
)
from src.database import GatheringDB, _transaction_from_payload
from src.mapper import (
    MAX_SHEET_BRANDS,
    normalize_sheet_brands,
    record_local_datetime,
    sheet_tab_name,
)
from src.sheets import SheetClient, sheet_open_error
from src.pipeline import (
    delete_transactions_for_date,
    process_new_notifications_only,
    run_pipeline,
    sync_date_to_sheet,
    transactions_for_date,
    txn_row_event,
)
from src.tally import (
    COMPLETED_STATUS,
    STAFF_DEPOSIT_TYPE,
    STAFF_WITHDRAW_TYPE,
    format_amount,
    local_today,
    parse_amount,
    txn_kind,
)
from src.workspace import (
    apply_workspace_to_settings,
    load_workspace_state,
    migrate_legacy_workspace,
    save_workspace_state,
    seed_workspace_sheets,
    website_host,
    workspace_key,
)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


class FinanceAutomationApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Finance Automation")
        self.root.geometry("1560x980")
        self.root.minsize(1100, 760)
        self.settings = Settings.load()
        self.workspace_key = ""
        self._loaded_sheet_brands: list[str] = []
        self.events: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None
        self._scrape_thread: threading.Thread | None = None
        self._scrape_jobs: queue.Queue | None = None
        self._scrape_busy = False
        self.watcher = None
        self.row_items: dict[str, tuple[str, str]] = {}

        self.scrape_deposits = tk.BooleanVar(value=True)
        self.scrape_withdrawals = tk.BooleanVar(value=True)
        self.use_open_browser = tk.BooleanVar(value=False)
        self.headless = tk.BooleanVar(value=not self.settings.headed)
        self.capturing_latest = False
        self.bulk_loading = False
        self._filter_gen = 0
        self._reload_gen = 0
        self._tally_dirty = False
        self._tally_after_id = None
        self.latest_run_ids: set[str] = set()
        self.sheet_id_cache: set[str] = set()
        self.sheet_id_cache_date = ""
        self.login_accounts = load_login_accounts()
        self.active_account = tk.IntVar(value=active_login_slot())
        active = self.login_accounts[self.active_account.get() - 1]
        self.login_website = tk.StringVar(
            value=active.get("website") or self.settings.dashboard_url
        )
        self.login_username = tk.StringVar(
            value=active["username"] or self.settings.dashboard_username
        )
        self.login_password = tk.StringVar(
            value=active["password"] or self.settings.dashboard_password
        )
        self.login_2fa = tk.StringVar(value=active["twofa"] or self.settings.dashboard_2fa)
        self.saved_username = self.login_username.get()
        self.saved_password = self.login_password.get()
        self.saved_2fa = self.login_2fa.get()
        self.account_use_btns: list[ttk.Button] = []
        self.arm_watcher_after_run = False
        self.open_sent_after_send = False
        self._prefer_sent_tab = False
        self.row_store: dict[tuple[str, str], dict] = {}
        self.date_filter = tk.StringVar(value=local_today())
        self.filter_caption = tk.StringVar(value="Showing today's Completed records")
        self.match_caption = tk.StringVar(value="Website Completed count appears here after Run now.")
        self.workspace_caption = tk.StringVar(value="No website selected — records start empty.")
        self.website_records = 0
        self.website_total = ""
        self.website_date = ""
        self.latest_type_filter = tk.StringVar(value="All types")
        self.deposit_status_filter = tk.StringVar(value="All")
        self.withdraw_status_filter = tk.StringVar(value="All")
        self.sent_type_filter = tk.StringVar(value="All types")
        self.sent_date_filter = tk.StringVar(value=local_today())
        self.sheet_date_count = 0
        self.sheet_tally_date = ""
        self.deposit_extended = False
        self.extend_btn_text = tk.StringVar(value="Show hidden details")
        self.poll_interval = tk.IntVar(value=self.settings.poll_interval_seconds)
        self.auto_interval = tk.IntVar(value=int(self.settings.poll_interval_seconds or 60))
        self.google_sheet_vars = [
            tk.StringVar(value=google_sheet_url(self.settings.sheet_id_at(slot)))
            for slot in GOOGLE_SHEET_SLOTS
        ]
        self.google_sheet = self.google_sheet_vars[0]
        self.google_sheet_2 = self.google_sheet_vars[1]
        self.db = GatheringDB(self.settings.database_path)
        self.dark_mode = tk.BooleanVar(value=load_gui_theme() == "dark")
        self.auto_running = False
        self.auto_send_to_sheet = tk.BooleanVar(value=True)
        self._single_run_active = False
        self._auto_after_id: str | None = None
        self._auto_deadline = 0.0
        self._live_stop = threading.Event()
        self.status_text = tk.StringVar(value="Idle")
        self.stat_pending = tk.StringVar(value="0")
        self.stat_extracted = tk.StringVar(value="0")
        self.stat_copied = tk.StringVar(value="0")
        self.stat_failed = tk.StringVar(value="0")
        self.stat_skipped = tk.StringVar(value="0")
        self.latest_title = tk.StringVar(value="Latest scrape")
        self.deposit_title = tk.StringVar(value="Deposits")
        self.withdraw_title = tk.StringVar(value="Withdrawals")
        self.sent_title = tk.StringVar(value="Google Sheet sent data")
        self.latest_tally = tk.StringVar(value="")
        self.deposit_tally = tk.StringVar(value="")
        self.withdraw_tally = tk.StringVar(value="")
        self.sent_tally = tk.StringVar(value="")
        self._open_workspace(self._current_workspace_key(), initial=True)

        self._build_style()
        self._build_layout()
        self._apply_brands_to_field(self._loaded_sheet_brands)
        self._apply_theme()
        self._refresh_counts()
        self._load_recent_rows()
        self._append_log("GUI ready. Open a section on the right. Run now only scrapes.")
        self._append_log("Send deposits or withdrawals from those sections after you tally the rows.")
        self._append_log(
            "Send and Sync write each date to the Google Sheet tab with that day number "
            "(29th transactions go to tab 29)."
        )
        label = self._workspace_label(self.workspace_key)
        if label:
            self._append_log(
                f"Loaded saved records for {label} only. Click another account "
                "or enter a new website to switch."
            )
        self._bind_shortcuts()
        self.root.after(50, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _theme_name(self) -> str:
        return "dark" if self.dark_mode.get() else "light"

    def _colors(self) -> dict[str, str]:
        return THEMES[self._theme_name()]

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self._apply_theme_styles()

    def _flat(self, style: ttk.Style, name: str, fill: str, border: str, **extra) -> None:
        style.configure(
            name,
            background=fill,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            relief="flat",
            **extra,
        )

    def _apply_theme_styles(self) -> None:
        colors = self._colors()
        border = colors["border"]
        self.root.configure(bg=colors["root"])
        style = ttk.Style(self.root)
        self._flat(style, "Root.TFrame", colors["root"], colors["root"], borderwidth=0)
        self._flat(style, "Card.TFrame", colors["card"], colors["card"], borderwidth=0)
        self._flat(style, "Header.TFrame", colors["header"], colors["header"], borderwidth=0)
        style.configure(
            "Header.TLabel",
            background=colors["header"],
            foreground=colors["header_fg"],
            font=("Segoe UI", 16, "bold"),
        )
        style.configure(
            "HeaderSub.TLabel",
            background=colors["header"],
            foreground=colors["header_sub"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "CardTitle.TLabel",
            background=colors["card"],
            foreground=colors["title"],
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=colors["card"],
            foreground=colors["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Stat.TLabel",
            background=colors["card"],
            foreground=colors["title"],
            font=("Segoe UI", 20, "bold"),
        )
        style.configure(
            "Tally.TLabel",
            background=colors["tally_bg"],
            foreground=colors["tally_fg"],
            font=("Segoe UI", 10, "bold"),
        )
        self._flat(style, "TallyBox.TFrame", colors["tally_bg"], colors["tally_bg"], borderwidth=0)
        self._flat(style, "LoginBox.TFrame", colors["tally_bg"], colors["tally_bg"], borderwidth=0)
        self._flat(
            style,
            "TButton",
            colors["button_bg"],
            border,
            foreground=colors["button_fg"],
            font=("Segoe UI", 9),
            padding=6,
            borderwidth=1,
        )
        style.map(
            "TButton",
            background=[("active", colors["tally_bg"]), ("pressed", colors["tally_bg"])],
            foreground=[("active", colors["button_fg"])],
            bordercolor=[("active", colors["accent"]), ("pressed", colors["accent"])],
            lightcolor=[("active", colors["accent"]), ("pressed", colors["accent"])],
            darkcolor=[("active", colors["accent"]), ("pressed", colors["accent"])],
        )
        self._flat(
            style,
            "Run.TButton",
            colors["run_bg"],
            colors["run_bg"],
            foreground=colors["run_fg"],
            font=("Segoe UI", 10, "bold"),
            padding=8,
            borderwidth=0,
        )
        style.map(
            "Run.TButton",
            background=[("active", colors["accent"]), ("pressed", colors["run_bg"])],
            foreground=[("active", colors["run_fg"])],
            bordercolor=[("active", colors["accent"])],
            lightcolor=[("active", colors["accent"])],
            darkcolor=[("active", colors["accent"])],
        )
        self._flat(
            style,
            "Quick.TButton",
            colors["button_bg"],
            border,
            foreground=colors["button_fg"],
            font=("Segoe UI", 9),
            padding=6,
            borderwidth=1,
        )
        self._flat(
            style,
            "Danger.TButton",
            "#c62828",
            "#b71c1c",
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            padding=6,
            borderwidth=0,
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#e53935"), ("pressed", "#8e0000")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
            bordercolor=[("active", "#e53935"), ("pressed", "#8e0000")],
            lightcolor=[("active", "#e53935"), ("pressed", "#8e0000")],
            darkcolor=[("active", "#e53935"), ("pressed", "#8e0000")],
        )
        self._flat(
            style,
            "Cred.TButton",
            colors["tally_bg"],
            border,
            foreground=colors["button_fg"],
            font=("Consolas", 9),
            padding=5,
            borderwidth=1,
        )
        style.configure(
            "TCheckbutton",
            background=colors["card"],
            foreground=colors["title"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Check.TLabel",
            background=colors["card"],
            foreground=colors["title"],
            font=("Segoe UI", 9),
        )
        style.map("TCheckbutton", background=[("active", colors["card"])], foreground=[("active", colors["title"])])
        style.configure(
            "TEntry",
            fieldbackground=colors["input_bg"],
            foreground=colors["input_fg"],
            insertcolor=colors["input_fg"],
            background=colors["input_bg"],
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.configure(
            "TSpinbox",
            fieldbackground=colors["input_bg"],
            foreground=colors["input_fg"],
            insertcolor=colors["input_fg"],
            background=colors["card"],
            arrowcolor=colors["title"],
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.configure(
            "TCombobox",
            fieldbackground=colors["input_bg"],
            foreground=colors["input_fg"],
            background=colors["card"],
            arrowcolor=colors["title"],
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["input_bg"])],
            foreground=[("readonly", colors["input_fg"])],
            bordercolor=[("focus", colors["accent"])],
        )
        self._flat(style, "TNotebook", colors["root"], colors["root"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            font=("Segoe UI", 10, "bold"),
            padding=(16, 9),
            background=colors["tab"],
            foreground=colors["tab_fg"],
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", colors["tab_sel"])],
            foreground=[("selected", colors["tab_sel_fg"])],
            bordercolor=[("selected", colors["accent"])],
        )
        style.configure(
            "Treeview",
            font=("Segoe UI", 9),
            rowheight=28,
            background=colors["tree_bg"],
            fieldbackground=colors["tree_bg"],
            foreground=colors["tree_fg"],
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background=colors["tree_head_bg"],
            foreground=colors["tree_head_fg"],
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.map(
            "Treeview",
            background=[("selected", colors["tree_select"])],
            foreground=[("selected", colors["header_fg"])],
        )
        style.map(
            "Treeview.Heading",
            background=[("active", colors["tree_head_bg"])],
            foreground=[("active", colors["tree_head_fg"])],
        )
        style.configure(
            "TScrollbar",
            background=colors["tally_bg"],
            troughcolor=colors["root"],
            arrowcolor=colors["muted"],
            bordercolor=colors["root"],
            lightcolor=colors["tally_bg"],
            darkcolor=colors["tally_bg"],
        )
        self.root.option_add("*TCombobox*Listbox.background", colors["input_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", colors["input_fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["tree_select"])

    def _apply_theme(self) -> None:
        self._apply_theme_styles()
        colors = self._colors()
        if hasattr(self, "side_canvas"):
            self.side_canvas.configure(bg=colors["card"])
        if hasattr(self, "log"):
            self.log.configure(
                bg=colors["log_bg"],
                fg=colors["log_fg"],
                insertbackground=colors["accent"],
                highlightthickness=0,
                bd=0,
                relief="flat",
            )
        if hasattr(self, "brand_names_box"):
            self.brand_names_box.configure(
                bg=colors["log_bg"],
                fg=colors["log_fg"],
                insertbackground=colors["accent"],
                highlightthickness=1,
                highlightbackground=colors["border"],
                highlightcolor=colors["accent"],
                bd=0,
                relief="flat",
            )
        tags = TREE_TAGS_DARK if self.dark_mode.get() else TREE_TAGS_LIGHT
        for name in ("latest_tree", "deposits_tree", "withdrawals_tree", "sent_tree"):
            tree = getattr(self, name, None)
            if tree is None:
                continue
            for tag, color in tags:
                tree.tag_configure(tag, foreground=color)
        self._draw_theme_switch()

    def _toggle_theme(self, _event=None) -> None:
        self.dark_mode.set(not self.dark_mode.get())
        persist_gui_theme(self._theme_name())
        self._apply_theme()
        self._append_log(f"Theme set to {self._theme_name()} mode.")

    def _draw_theme_switch(self) -> None:
        if not hasattr(self, "theme_switch"):
            return
        colors = self._colors()
        canvas = self.theme_switch
        canvas.configure(bg=colors["header"])
        canvas.delete("all")
        on = self.dark_mode.get()
        fill = colors["switch_on"] if on else colors["switch_off"]
        canvas.create_oval(2, 3, 20, 21, fill=fill, outline=fill)
        canvas.create_oval(26, 3, 44, 21, fill=fill, outline=fill)
        canvas.create_rectangle(11, 3, 35, 21, fill=fill, outline=fill)
        knob_x = 28 if on else 4
        canvas.create_oval(knob_x, 5, knob_x + 14, 19, fill="#ffffff", outline="#ffffff")
        if hasattr(self, "theme_switch_label"):
            self.theme_switch_label.configure(
                text="Dark" if on else "Light",
                background=colors["header"],
                foreground=colors["header_sub"],
            )
        if hasattr(self, "theme_switch_wrap"):
            self.theme_switch_wrap.configure(background=colors["header"])

    def _build_theme_switch(self, parent: ttk.Frame) -> None:
        colors = self._colors()
        wrap = tk.Frame(parent, bg=colors["header"])
        wrap.pack(side="right", padx=(12, 0))
        self.theme_switch_wrap = wrap
        self.theme_switch_label = tk.Label(
            wrap,
            text="Dark" if self.dark_mode.get() else "Light",
            bg=colors["header"],
            fg=colors["header_sub"],
            font=("Segoe UI", 9, "bold"),
        )
        self.theme_switch_label.pack(side="left", padx=(0, 8))
        self.theme_switch = tk.Canvas(
            wrap,
            width=48,
            height=24,
            highlightthickness=0,
            bg=colors["header"],
            cursor="hand2",
        )
        self.theme_switch.pack(side="left")
        self.theme_switch.bind("<Button-1>", self._toggle_theme)
        self.theme_switch_label.bind("<Button-1>", self._toggle_theme)
        self._draw_theme_switch()

    def _build_layout(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(20, 14))
        header.pack(fill="x")
        titles = ttk.Frame(header, style="Header.TFrame")
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="Finance Automation", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            titles,
            text="Scrapes Completed STAFF DEPOSIT and STAFF WITHDRAW only. Pick a date so the GUI count matches the website Record count, then send once — no duplicate sheet rows.",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        self._build_theme_switch(header)

        body = ttk.Frame(self.root, style="Root.TFrame", padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_workspace(body)

    def _build_sidebar(self, body: ttk.Frame) -> None:
        side_wrap = ttk.Frame(body, style="Card.TFrame")
        side_wrap.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        side_canvas = tk.Canvas(side_wrap, bg=self._colors()["card"], highlightthickness=0, width=310, height=780)
        self.side_canvas = side_canvas
        side_scroll = ttk.Scrollbar(side_wrap, orient="vertical", command=side_canvas.yview)
        sidebar = ttk.Frame(side_canvas, style="Card.TFrame", padding=16)
        sidebar.bind(
            "<Configure>",
            lambda event: side_canvas.configure(scrollregion=side_canvas.bbox("all")),
        )
        side_canvas.create_window((0, 0), window=sidebar, anchor="nw", width=300)
        side_canvas.configure(yscrollcommand=side_scroll.set)
        side_canvas.pack(side="left", fill="both", expand=True)
        side_scroll.pack(side="right", fill="y")
        side_canvas.bind(
            "<Enter>",
            lambda _event: side_canvas.bind_all(
                "<MouseWheel>",
                lambda event: side_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"),
            ),
        )
        side_canvas.bind("<Leave>", lambda _event: side_canvas.unbind_all("<MouseWheel>"))

        ttk.Label(sidebar, text="Website login", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(sidebar, textvariable=self.status_text, style="Muted.TLabel").pack(anchor="w", pady=(2, 8))

        ttk.Label(sidebar, text="Website", style="Muted.TLabel").pack(anchor="w")
        website_entry = ttk.Entry(sidebar, textvariable=self.login_website, width=28)
        website_entry.pack(fill="x", pady=(2, 6))
        website_entry.bind("<FocusOut>", self._on_login_identity_changed)
        ttk.Label(sidebar, text="Username", style="Muted.TLabel").pack(anchor="w")
        username_entry = ttk.Entry(sidebar, textvariable=self.login_username, width=28)
        username_entry.pack(fill="x", pady=(2, 6))
        username_entry.bind("<FocusOut>", self._on_login_identity_changed)
        ttk.Label(sidebar, text="Password", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_password, width=28).pack(fill="x", pady=(2, 6))
        ttk.Label(sidebar, text="2FA code", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_2fa, width=28).pack(fill="x", pady=(2, 10))

        ttk.Label(sidebar, text="Sheet brands", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar,
            text=(
                f"Enter 1–{MAX_SHEET_BRANDS} Google Sheet brand names for this "
                "website, one per line. KABOOM77VIPA, KABOOMVIPA, and VIPA all "
                "write as KABOOM77 when that name is saved here."
            ),
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(2, 6))
        self.brand_names_box = tk.Text(
            sidebar,
            height=5,
            width=28,
            wrap="word",
            font=("Segoe UI", 9),
            bg=self._colors()["log_bg"],
            fg=self._colors()["log_fg"],
            insertbackground=self._colors()["accent"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=self._colors()["border"],
            padx=6,
            pady=6,
        )
        self.brand_names_box.pack(fill="x", pady=(0, 6))
        ttk.Button(
            sidebar,
            text="Save brands",
            style="Quick.TButton",
            command=self._save_sheet_brands,
        ).pack(fill="x", pady=(0, 10))

        ttk.Label(sidebar, text="Saved accounts (3)", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar,
            text="Each account stores website, username, password, 2FA, sheet brands, and that site's Google Sheets. Click an account to show only that website's Latest scrape, Deposits, Withdrawals, and sent rows. A new website starts empty.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(2, 6))
        accounts_box = ttk.Frame(sidebar, style="LoginBox.TFrame", padding=8)
        accounts_box.pack(fill="x", pady=(0, 10))
        self.account_use_btns = []
        for slot in (1, 2, 3):
            row = ttk.Frame(accounts_box, style="LoginBox.TFrame")
            row.pack(fill="x", pady=(0, 8))
            use_btn = ttk.Button(
                row,
                style="Cred.TButton",
                command=lambda number=slot: self._select_account(number),
            )
            use_btn.pack(fill="x")
            self.account_use_btns.append(use_btn)
            actions = ttk.Frame(row, style="LoginBox.TFrame")
            actions.pack(fill="x", pady=(4, 0))
            ttk.Button(
                actions,
                text="Clear",
                style="Quick.TButton",
                command=lambda number=slot: self._clear_account(number),
            ).pack(side="left")
            ttk.Button(
                actions,
                text="Save",
                style="Quick.TButton",
                command=lambda number=slot: self._save_account(number),
            ).pack(side="right")
        self._refresh_account_buttons()

        ttk.Label(sidebar, text="What to scrape", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Checkbutton(sidebar, text="Scrape STAFF DEPOSIT", variable=self.scrape_deposits).pack(anchor="w")
        ttk.Checkbutton(sidebar, text="Scrape STAFF WITHDRAW", variable=self.scrape_withdrawals).pack(anchor="w")
        ttk.Checkbutton(
            sidebar,
            text="Hide browser (only if 2FA is already saved)",
            variable=self.headless,
        ).pack(anchor="w", pady=(4, 10))

        ttk.Label(sidebar, text="Scrape", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Button(sidebar, text="Run now", style="Run.TButton", command=self._run_now).pack(fill="x", pady=3)
        ttk.Label(
            sidebar,
            text="Run now sets Type to STAFF DEPOSIT and STAFF WITHDRAW with Status COMPLETED, reads every page, and shows rows in the GUI. Automated Run does the same and sends new IDs to the Google Sheet.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(8, 8))
        timer_row = ttk.Frame(sidebar, style="Card.TFrame")
        timer_row.pack(fill="x", pady=(0, 4))
        ttk.Label(timer_row, text="Wait between scrapes (seconds)", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(
            timer_row,
            from_=5,
            to=3600,
            increment=5,
            textvariable=self.auto_interval,
            width=8,
        ).pack(side="right")
        auto_send_row = ttk.Frame(sidebar, style="Card.TFrame")
        auto_send_row.pack(fill="x", pady=(6, 4))
        ttk.Checkbutton(
            auto_send_row,
            variable=self.auto_send_to_sheet,
            command=self._on_auto_send_toggled,
        ).pack(side="left", anchor="n")
        auto_send_label = ttk.Label(
            auto_send_row,
            text="Send Extracted records to Google Sheet Automatically",
            style="Check.TLabel",
            wraplength=250,
        )
        auto_send_label.pack(side="left", fill="x", expand=True, padx=(4, 0))
        auto_send_label.bind("<Button-1>", self._toggle_auto_send)
        ttk.Button(
            sidebar,
            text="Automated Run",
            style="Run.TButton",
            command=self._start_automated_run,
        ).pack(fill="x", pady=3)
        ttk.Button(
            sidebar,
            text="Stop Automated Run",
            style="Quick.TButton",
            command=self._stop_automated_run,
        ).pack(fill="x", pady=3)
        ttk.Label(
            sidebar,
            text="Automated Run opens the dashboard, selects the date, sets Type to STAFF DEPOSIT and STAFF WITHDRAW together with Status COMPLETED, and scrapes those Completed rows into the GUI. Deposits start on Google Sheet row 105 of the day tab; withdrawals start on row 1024. Deposit ATTACHMENT screenshots fill BANK on deposit rows. Withdrawals stay blank for manual BANK entry. When Send Extracted records is checked, new IDs are written to the Google Sheet. After each scrape it waits the seconds you set, then starts the next. Stop Automated Run ends the loop.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(4, 10))

        ttk.Label(sidebar, text="Google Sheets", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Label(
            sidebar,
            text="These sheets belong to the current website only. Switching accounts loads that website's sheets. Share each sheet with the service account as Editor.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(0, 6))
        for slot in GOOGLE_SHEET_SLOTS:
            label = "Sheet 1" if slot == 1 else f"Sheet {slot} (optional copy)"
            ttk.Label(sidebar, text=label, style="Muted.TLabel").pack(anchor="w")
            ttk.Entry(
                sidebar,
                textvariable=self.google_sheet_vars[slot - 1],
                width=28,
            ).pack(fill="x", pady=(2, 6))
        share_email = service_account_email(self.settings.google_credentials_path)
        ttk.Label(
            sidebar,
            text=(
                f"Share with: {share_email}"
                if share_email
                else "Share with the service account email from credentials/service-account.json"
            ),
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(0, 6))
        sheet_btns = ttk.Frame(sidebar, style="Card.TFrame")
        sheet_btns.pack(fill="x", pady=(0, 10))
        ttk.Button(sheet_btns, text="Save sheets", style="Quick.TButton", command=self._save_google_sheets).pack(
            fill="x", pady=2
        )
        for slot in GOOGLE_SHEET_SLOTS:
            ttk.Button(
                sheet_btns,
                text=f"Open sheet {slot}",
                style="Quick.TButton",
                command=lambda number=slot: self._open_google_sheet(number),
            ).pack(fill="x", pady=2)

        ttk.Label(sidebar, text="Quick actions", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Button(
            sidebar, text="Send deposits to sheet", style="Quick.TButton",
            command=lambda: self._send_section_to_sheet("deposit"),
        ).pack(fill="x", pady=2)
        ttk.Button(
            sidebar, text="Send withdrawals to sheet", style="Quick.TButton",
            command=lambda: self._send_section_to_sheet("withdraw"),
        ).pack(fill="x", pady=2)
        ttk.Button(
            sidebar, text="Select all to-send here", style="Quick.TButton",
            command=self._select_all_to_send,
        ).pack(fill="x", pady=2)
        ttk.Button(
            sidebar, text="Copy selected IDs", style="Quick.TButton",
            command=self._copy_selected_ids,
        ).pack(fill="x", pady=2)
        ttk.Button(
            sidebar, text="Open Google Sheet", style="Quick.TButton",
            command=lambda: self._open_google_sheet(1),
        ).pack(fill="x", pady=2)
        ttk.Button(
            sidebar, text="Sync Google Sheet", style="Quick.TButton",
            command=self._sync_records_with_sheet,
        ).pack(fill="x", pady=2)
        ttk.Label(
            sidebar,
            text="Shortcuts: Ctrl+R run · Ctrl+1–4 tabs · Ctrl+D send deposits · Ctrl+W send withdrawals · Ctrl+T today",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(6, 10))

        ttk.Label(sidebar, text="Selected date tally", style="CardTitle.TLabel").pack(anchor="w", pady=(8, 8))
        stats = ttk.Frame(sidebar, style="Card.TFrame")
        stats.pack(fill="x")
        self._stat_block(stats, "Extracted Transactions", self.stat_extracted).grid(
            row=0, column=0, padx=(0, 12)
        )
        self._stat_block(stats, "Sent Count to Google Sheets", self.stat_copied).grid(
            row=0, column=1, padx=(0, 12)
        )
        self._stat_block(stats, "To send", self.stat_pending).grid(row=1, column=0, padx=(0, 12), pady=(10, 0))
        self._stat_block(stats, "Failed", self.stat_failed).grid(
            row=1, column=1, padx=(0, 12), pady=(10, 0)
        )
        ttk.Label(
            sidebar,
            text="Run now reads every Completed page for the selected date. Send writes new IDs only. Sync restores deleted sheet rows and never duplicates.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(16, 0))

    def _build_workspace(self, body: ttk.Frame) -> None:
        right = ttk.Frame(body, style="Root.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=5)
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        filter_card = ttk.Frame(right, style="Card.TFrame", padding=12)
        filter_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        filter_card.columnconfigure(0, weight=1)
        ttk.Label(filter_card, text="Tally date  ·  Completed STAFF DEPOSIT + STAFF WITHDRAW", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(filter_card, textvariable=self.workspace_caption, style="Tally.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        filter_box = ttk.Frame(filter_card, style="Card.TFrame")
        filter_box.grid(row=0, column=1, sticky="e")
        ttk.Label(filter_box, text="Date", style="Muted.TLabel").pack(side="left")
        self.date_combo = ttk.Combobox(
            filter_box,
            textvariable=self.date_filter,
            state="readonly",
            width=16,
            values=self._date_filter_options(),
        )
        self.date_combo.pack(side="left", padx=(6, 8))
        self.date_combo.bind("<<ComboboxSelected>>", self._on_filters_changed)
        ttk.Button(filter_box, text="Today", command=self._select_today).pack(side="left")
        ttk.Button(
            filter_box,
            text="Clear today's scrape",
            style="Danger.TButton",
            command=self._clear_today_scrape,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(filter_card, textvariable=self.filter_caption, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Label(filter_card, textvariable=self.match_caption, style="Tally.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        quick = ttk.Frame(filter_card, style="Card.TFrame")
        quick.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(quick, text="Deposits", style="Quick.TButton", command=lambda: self._show_tab(1)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(quick, text="Withdrawals", style="Quick.TButton", command=lambda: self._show_tab(2)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(quick, text="Sent", style="Quick.TButton", command=lambda: self._show_tab(3)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            quick, text="Send deposits", style="Quick.TButton",
            command=lambda: self._send_section_to_sheet("deposit"),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            quick, text="Send withdrawals", style="Quick.TButton",
            command=lambda: self._send_section_to_sheet("withdraw"),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(quick, text="Select to-send", style="Quick.TButton", command=self._select_all_to_send).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(quick, text="Copy IDs", style="Quick.TButton", command=self._copy_selected_ids).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(quick, text="Open sheet", style="Quick.TButton", command=self._open_google_sheet).pack(
            side="left"
        )

        self.pages = ttk.Notebook(right)
        self.pages.grid(row=1, column=0, sticky="nsew", pady=(0, 12))

        latest_page = self._section_page()
        deposit_page = self._section_page()
        withdraw_page = self._section_page()
        sent_page = self._section_page()
        self.pages.add(latest_page, text="Latest scrape")
        self.pages.add(deposit_page, text="Deposits")
        self.pages.add(withdraw_page, text="Withdrawals")
        self.pages.add(sent_page, text="Google Sheet sent")
        self.pages.bind("<<NotebookTabChanged>>", lambda _event: self._update_filter_caption())

        self._build_latest_section(latest_page)
        self._build_money_section(
            deposit_page,
            key="deposits",
            title_var=self.deposit_title,
            tally_var=self.deposit_tally,
            status_var=self.deposit_status_filter,
            send_label="Send to-send deposits to Google Sheet",
            send_command=lambda: self._send_section_to_sheet("deposit"),
            extra_button=(self.extend_btn_text, self._toggle_deposit_details),
            displaycolumns=DEPOSIT_CORE_COLUMNS,
            id_heading="ID Transaction",
        )
        self._build_money_section(
            withdraw_page,
            key="withdrawals",
            title_var=self.withdraw_title,
            tally_var=self.withdraw_tally,
            status_var=self.withdraw_status_filter,
            send_label="Send to-send withdrawals to Google Sheet",
            send_command=lambda: self._send_section_to_sheet("withdraw"),
            extra_button=None,
            displaycolumns=ALL_COLUMNS,
            id_heading="ID",
        )
        self._build_sent_section(sent_page)

        log_card = ttk.Frame(right, style="Card.TFrame", padding=12)
        log_card.grid(row=2, column=0, sticky="nsew")
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)
        ttk.Label(log_card, text="Activity", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.log = tk.Text(
            log_card,
            height=7,
            wrap="word",
            font=("Consolas", 9),
            bg=self._colors()["log_bg"],
            fg=self._colors()["log_fg"],
            insertbackground=self._colors()["accent"],
            relief="flat",
            highlightthickness=0,
            bd=0,
            padx=8,
            pady=8,
        )
        self.log.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.log.configure(state="disabled")

    def _section_page(self) -> ttk.Frame:
        page = ttk.Frame(self.pages, style="Card.TFrame", padding=12)
        page.rowconfigure(2, weight=1)
        page.columnconfigure(0, weight=1)
        return page

    def _build_latest_section(self, page: ttk.Frame) -> None:
        bar = ttk.Frame(page, style="Card.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.latest_title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        controls = ttk.Frame(bar, style="Card.TFrame")
        controls.grid(row=0, column=1, sticky="e")
        ttk.Label(controls, text="Type", style="Muted.TLabel").pack(side="left")
        self.latest_type_combo = ttk.Combobox(
            controls,
            textvariable=self.latest_type_filter,
            state="readonly",
            width=14,
            values=("All types", "DEPOSIT", "WITHDRAW"),
        )
        self.latest_type_combo.pack(side="left", padx=(6, 8))
        self.latest_type_combo.bind("<<ComboboxSelected>>", self._on_filters_changed)
        self.send_latest_btn = ttk.Button(
            controls,
            text="Send latest scrape to Google Sheet",
            command=self._send_latest_to_sheet,
        )
        self.send_latest_btn.pack(side="left", padx=(0, 8))
        self._on_auto_send_toggled()
        ttk.Button(controls, text="Clear this scrape", command=self._clear_latest).pack(side="left")
        ttk.Button(
            controls,
            text="Clear today's scrape",
            style="Danger.TButton",
            command=self._clear_today_scrape,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            page,
            text="Shows new and not-yet-sent Completed rows. Already exported IDs stay out of this list. Send writes them to the day tab and into Google Sheet sent.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.latest_tree = self._make_tree(page, "latest", ALL_COLUMNS, "ID Transaction")
        self.latest_tree.configure(selectmode="extended")
        self.latest_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_filter_caption())
        self.latest_tree.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self.latest_yscroll.grid(row=2, column=1, sticky="ns", pady=(8, 0))
        self.latest_xscroll.grid(row=3, column=0, sticky="ew")
        self._tally_bar(page, self.latest_tally).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_money_section(
        self,
        page: ttk.Frame,
        key: str,
        title_var: tk.StringVar,
        tally_var: tk.StringVar,
        status_var: tk.StringVar,
        send_label: str,
        send_command,
        extra_button: tuple[tk.StringVar, object] | None,
        displaycolumns: tuple[str, ...],
        id_heading: str,
    ) -> None:
        bar = ttk.Frame(page, style="Card.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=title_var, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        controls = ttk.Frame(bar, style="Card.TFrame")
        controls.grid(row=0, column=1, sticky="e")
        ttk.Label(controls, text="Sheet status", style="Muted.TLabel").pack(side="left")
        combo = ttk.Combobox(
            controls,
            textvariable=status_var,
            state="readonly",
            width=12,
            values=("All", "To send", "Sent", "Failed"),
        )
        combo.pack(side="left", padx=(6, 8))
        combo.bind("<<ComboboxSelected>>", self._on_filters_changed)
        setattr(self, f"{key}_status_combo", combo)
        if extra_button:
            text_var, command = extra_button
            ttk.Button(controls, textvariable=text_var, command=command).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text=send_label, command=send_command).pack(side="left")
        ttk.Label(
            page,
            text="Tally the visible rows, then send only this section. Select rows to send a subset.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tree = self._make_tree(page, key, displaycolumns, id_heading)
        tree.configure(selectmode="extended")
        tree.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        getattr(self, f"{key}_yscroll").grid(row=2, column=1, sticky="ns", pady=(8, 0))
        getattr(self, f"{key}_xscroll").grid(row=3, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", lambda _event: self._update_filter_caption())
        setattr(self, f"{key}_tree", tree)
        self._tally_bar(page, tally_var).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_sent_section(self, page: ttk.Frame) -> None:
        bar = ttk.Frame(page, style="Card.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.sent_title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        controls = ttk.Frame(bar, style="Card.TFrame")
        controls.grid(row=0, column=1, sticky="e")
        ttk.Label(controls, text="Date", style="Muted.TLabel").pack(side="left")
        self.sent_date_combo = ttk.Combobox(
            controls,
            textvariable=self.sent_date_filter,
            state="readonly",
            width=14,
            values=self._date_filter_options(),
        )
        self.sent_date_combo.pack(side="left", padx=(6, 8))
        self.sent_date_combo.bind("<<ComboboxSelected>>", self._on_filters_changed)
        ttk.Label(controls, text="Type", style="Muted.TLabel").pack(side="left")
        self.sent_type_combo = ttk.Combobox(
            controls,
            textvariable=self.sent_type_filter,
            state="readonly",
            width=14,
            values=("All types", "DEPOSIT", "WITHDRAW"),
        )
        self.sent_type_combo.pack(side="left", padx=(6, 8))
        self.sent_type_combo.bind("<<ComboboxSelected>>", self._on_filters_changed)
        ttk.Button(
            controls,
            text="Sync Records with Google Sheet",
            command=self._sync_records_with_sheet,
        ).pack(side="left")
        ttk.Label(
            page,
            text="Today's sent rows by default. Sync writes the selected date to the matching day tab (29 → sheet 29).",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.sent_tree = self._make_tree(page, "sent", ALL_COLUMNS, "ID Transaction")
        self.sent_tree.configure(selectmode="extended")
        self.sent_tree.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self.sent_yscroll.grid(row=2, column=1, sticky="ns", pady=(8, 0))
        self.sent_xscroll.grid(row=3, column=0, sticky="ew")
        self.sent_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_filter_caption())
        self._tally_bar(page, self.sent_tally).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _tally_bar(self, parent: ttk.Frame, variable: tk.StringVar) -> ttk.Frame:
        box = ttk.Frame(parent, style="TallyBox.TFrame", padding=10)
        ttk.Label(box, textvariable=variable, style="Tally.TLabel").pack(anchor="w")
        return box

    def _make_tree(
        self,
        parent: ttk.Frame,
        key: str,
        displaycolumns: tuple[str, ...],
        id_heading: str,
    ) -> ttk.Treeview:
        tree = ttk.Treeview(
            parent,
            columns=ALL_COLUMNS,
            displaycolumns=displaycolumns,
            show="headings",
            selectmode="browse",
        )
        for col, (label, width) in COLUMN_HEADINGS.items():
            heading = id_heading if col == "id" else label
            tree.heading(col, text=heading)
            tree.column(col, width=width, stretch=False, minwidth=60)
        for tag, color in TREE_TAGS:
            tree.tag_configure(tag, foreground=color)
        yscroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        setattr(self, f"{key}_yscroll", yscroll)
        setattr(self, f"{key}_xscroll", xscroll)
        return tree

    def _section_for_type(self, raw: object) -> str:
        return txn_kind(raw)

    def _tree_for(self, section: str) -> ttk.Treeview:
        trees = {
            "latest": self.latest_tree,
            "sent": self.sent_tree,
            "withdraw": self.withdrawals_tree,
            "deposit": self.deposits_tree,
        }
        return trees.get(section, self.deposits_tree)

    def _type_wanted(self, raw: object) -> bool:
        section = self._section_for_type(raw)
        if section == "withdraw":
            return bool(self.scrape_withdrawals.get())
        return bool(self.scrape_deposits.get())

    def _clear_bucket(self, section: str) -> None:
        tree = self._tree_for(section)
        for key in [item for item in self.row_store if item[0] == section]:
            _bucket, item_id = key
            if tree.exists(item_id):
                tree.delete(item_id)
            self.row_store.pop(key, None)
        for txn_key, (bucket, _item) in list(self.row_items.items()):
            if bucket == section:
                self.row_items.pop(txn_key, None)

    def _clear_latest(self) -> None:
        self._clear_bucket("latest")
        self.latest_run_ids = set()
        self._update_filter_caption()
        self._append_log(
            "Cleared the latest scrape table. Google Sheet sent rows and saved "
            "deposits/withdrawals are unchanged."
        )

    def _ids_for_date(self, day: str) -> set[str]:
        ids: set[str] = set()
        wanted = self._display_date(day)
        for rec in self.row_store.values():
            if self._display_date(rec.get("date")) != wanted:
                continue
            values = rec.get("values") or ()
            txn_id = str(values[1] if len(values) > 1 else "")
            if txn_id:
                ids.add(txn_id)
        return ids

    def _remove_rows_for_ids(self, txn_ids: set[str]) -> int:
        if not txn_ids:
            return 0
        removed = 0
        for key, rec in list(self.row_store.items()):
            values = rec.get("values") or ()
            txn_id = str(values[1] if len(values) > 1 else "")
            if txn_id not in txn_ids:
                continue
            section, item = key
            tree = self._tree_for(section)
            if tree.exists(item):
                tree.delete(item)
            self.row_store.pop(key, None)
            store_key = self._item_key(section, txn_id)
            if self.row_items.get(store_key) == (section, item):
                self.row_items.pop(store_key, None)
            self.latest_run_ids.discard(txn_id)
            removed += 1
        return removed

    def _clear_today_scrape(self) -> None:
        if self._busy() or self.auto_running:
            messagebox.showinfo(
                "Finish the current run first",
                "Stop Automated Run or wait for the current scrape/send to finish "
                "before erasing today's scrape.",
            )
            return
        today = local_today()
        ids = self._ids_for_date(today) | {
            txn.transaction_id for txn in transactions_for_date(self.db, today)
        }
        if not ids:
            messagebox.showinfo(
                "Nothing to erase",
                f"There is no scraped data for today ({today}) on this website.",
            )
            return
        if not messagebox.askyesno(
            "Erase today's scrape",
            f"Are you sure to erase all the data scraped today ({today}) "
            f"for this website?\n\n"
            f"Yes will remove {len(ids)} transaction(s) from Latest scrape, "
            "Deposits, Withdrawals, and Google Sheet sent.\n"
            "Records from other dates will not be erased.\n\n"
            "No keeps everything as it is.",
        ):
            self._append_log("Clear today's scrape cancelled.")
            return
        deleted = delete_transactions_for_date(self.db, today)
        self._remove_rows_for_ids(ids)
        if self.website_date == today:
            self.website_records = 0
            self.website_total = ""
        self.sheet_id_cache = set()
        self.sheet_id_cache_date = ""
        self._refresh_filter_options()
        self._apply_filters()
        self._refresh_counts()
        self._update_filter_caption()
        self._append_log(
            f"Erased {max(deleted, len(ids))} scraped record(s) for today ({today}) "
            "on this website only. Other dates were left unchanged."
        )

    def _toggle_deposit_details(self) -> None:
        self.deposit_extended = not self.deposit_extended
        columns = ALL_COLUMNS if self.deposit_extended else DEPOSIT_CORE_COLUMNS
        self.deposits_tree.configure(displaycolumns=columns)
        if self.deposit_extended:
            self.extend_btn_text.set("Hide extra details")
            self._append_log("Deposits table extended with all scraped fields.")
        else:
            self.extend_btn_text.set("Show hidden details")
            self._append_log("Deposits table showing core fields only.")

    def _stat_block(self, parent: ttk.Frame, label: str, variable: tk.StringVar) -> ttk.Frame:
        box = ttk.Frame(parent, style="Card.TFrame")
        ttk.Label(box, textvariable=variable, style="Stat.TLabel").pack(anchor="w")
        ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
        return box

    def _busy(self) -> bool:
        if self._scrape_thread is not None and not self._scrape_thread.is_alive():
            self._scrape_busy = False
            self._scrape_thread = None
        worker_alive = self.worker is not None and self.worker.is_alive()
        if self.worker is not None and not worker_alive:
            self.worker = None
        return bool(self._scrape_busy) or worker_alive

    def _ensure_scrape_worker(self) -> None:
        if self._scrape_thread is not None and self._scrape_thread.is_alive():
            return
        self._scrape_jobs = queue.Queue()

        def loop() -> None:
            from src.scraper import DashboardSession

            session = DashboardSession()
            try:
                while True:
                    job = self._scrape_jobs.get()
                    if job is None:
                        break
                    try:
                        job(session)
                    except Exception as exc:
                        self.events.put({"kind": "log", "message": f"Run failed: {exc}"})
                        self.events.put({"kind": "done", "message": str(exc)})
                    finally:
                        self._scrape_busy = False
            finally:
                session.close()

        self._scrape_thread = threading.Thread(target=loop, name="scrape-worker", daemon=True)
        self._scrape_thread.start()

    def _account_host(self, website: str) -> str:
        return website_host(website)

    def _current_workspace_key(self) -> str:
        return workspace_key(self.login_website.get(), self.login_username.get())

    def _workspace_label(self, key: str = "") -> str:
        host = self._account_host(self.login_website.get())
        username = self.login_username.get().strip()
        if host and username:
            return f"{host} · {username}"
        if host:
            return host
        if username:
            return username
        if key:
            return key.replace("__", " · ").replace("_", ".")
        return ""

    def _apply_sheet_ids_to_fields(self, sheet_ids: list[str]) -> None:
        padded = list(sheet_ids) + [""] * len(self.google_sheet_vars)
        for index, var in enumerate(self.google_sheet_vars):
            sheet_id = normalize_google_sheet_id(padded[index])
            var.set(google_sheet_url(sheet_id) or sheet_id)

    def _sheet_ids_from_fields(self) -> list[str]:
        return [self._sheet_id_from_field(slot) for slot in GOOGLE_SHEET_SLOTS]

    def _brands_from_field(self) -> list[str]:
        box = getattr(self, "brand_names_box", None)
        if box is None:
            return list(self._loaded_sheet_brands)
        return normalize_sheet_brands(box.get("1.0", "end"))

    def _apply_brands_to_field(self, brands: list[str] | tuple[str, ...] | None) -> None:
        names = normalize_sheet_brands(brands)
        self._loaded_sheet_brands = names
        box = getattr(self, "brand_names_box", None)
        if box is None:
            return
        box.delete("1.0", "end")
        if names:
            box.insert("1.0", "\n".join(names))

    def _save_sheet_brands(self) -> None:
        if not self._current_workspace_key() and not self.workspace_key:
            messagebox.showwarning(
                "Website required",
                "Enter the website and username first so these brands stay "
                "with that login.",
            )
            return
        if not self._switch_workspace_if_needed(log=False):
            return
        brands = self._brands_from_field()
        if not brands:
            messagebox.showwarning(
                "No brand names",
                f"Enter 1–{MAX_SHEET_BRANDS} brand names, one per line.",
            )
            return
        self._apply_brands_to_field(brands)
        self._save_current_workspace_state()
        self.settings.sheet_brands = tuple(brands)
        self._append_log(
            "Saved "
            + ", ".join(brands)
            + " as this website's Google Sheet brand "
            + ("name." if len(brands) == 1 else "names.")
        )

    def _save_current_workspace_state(self, *, sheets_only: bool = False) -> None:
        if not self.workspace_key:
            return
        if sheets_only:
            save_workspace_state(
                self.workspace_key,
                sheet_ids=self._sheet_ids_from_fields(),
                sheet_brands=self._brands_from_field(),
            )
            return
        save_workspace_state(
            self.workspace_key,
            website=self.login_website.get(),
            username=self.login_username.get(),
            sheet_ids=self._sheet_ids_from_fields(),
            sheet_brands=self._brands_from_field(),
        )

    def _reset_workspace_view(self) -> None:
        self.latest_run_ids = set()
        self.sheet_id_cache = set()
        self.sheet_id_cache_date = ""
        self.website_records = 0
        self.website_total = ""
        self.website_date = ""
        self.sheet_date_count = 0
        self.sheet_tally_date = ""
        if hasattr(self, "latest_tree"):
            self._clear_bucket("latest")
            self._clear_bucket("deposit")
            self._clear_bucket("withdraw")
            self._clear_bucket("sent")

    def _update_workspace_caption(self) -> None:
        label = self._workspace_label(self.workspace_key)
        if label:
            self.workspace_caption.set(f"Showing records for {label} only")
        else:
            self.workspace_caption.set("No website selected — records start empty.")

    def _open_workspace(self, key: str, initial: bool = False) -> bool:
        previous = self.workspace_key
        if not initial and key == previous:
            self._update_workspace_caption()
            return False
        if not initial:
            self._save_current_workspace_state(sheets_only=True)
        migrated = migrate_legacy_workspace(key) if key else False
        if key:
            seed_workspace_sheets(
                key,
                [self.settings.sheet_id_at(slot) for slot in GOOGLE_SHEET_SLOTS]
                if initial or migrated
                else self._sheet_ids_from_fields() if not previous else [],
            )
        self.workspace_key = key
        apply_workspace_to_settings(self.settings, key)
        self.db = GatheringDB(self.settings.database_path)
        state = load_workspace_state(key)
        if key and (any(state["sheet_ids"]) or not initial):
            self._apply_sheet_ids_to_fields(state["sheet_ids"])
        self._apply_brands_to_field(state.get("sheet_brands") or [])
        self.settings.sheet_brands = tuple(self._loaded_sheet_brands)
        self._persist_google_sheets(remember_workspace=False)
        self._reset_workspace_view()
        self._update_workspace_caption()
        return True

    def _switch_workspace_if_needed(self, log: bool = True) -> bool:
        key = self._current_workspace_key()
        if key == self.workspace_key:
            self._update_workspace_caption()
            return True
        if self._busy() or self.auto_running:
            messagebox.showinfo(
                "Finish the current run first",
                "Stop Automated Run or wait for the current scrape/send to finish "
                "before changing website or account.",
            )
            state = load_workspace_state(self.workspace_key)
            if state["website"] or state["username"]:
                if state["website"]:
                    self.login_website.set(state["website"])
                if state["username"]:
                    self.login_username.set(state["username"])
            return False
        changed = self._open_workspace(key)
        if changed and hasattr(self, "latest_tree"):
            self._reload_workspace()
            self._refresh_counts()
            self._queue_sheet_unsent_check()
        if changed and log:
            label = self._workspace_label(key)
            if label:
                self._append_log(
                    f"Switched to {label}. Latest scrape, Deposits, Withdrawals, "
                    "and Google Sheet sent now show only this website."
                )
            else:
                self._append_log(
                    "No website selected. Transaction lists are empty until you "
                    "enter a website or click a saved account."
                )
        return True

    def _on_login_identity_changed(self, _event=None) -> None:
        website = normalize_dashboard_url(self.login_website.get())
        if website:
            self.login_website.set(website)
        self._switch_workspace_if_needed()

    def _account_button_text(self, slot: int) -> str:
        account = self.login_accounts[slot - 1]
        username = account["username"] or "(empty)"
        host = self._account_host(account.get("website", ""))
        selected = " ●" if slot == self.active_account.get() else ""
        if host:
            return f"Account {slot}  {username}  ·  {host}{selected}"
        return f"Account {slot}  {username}{selected}"

    def _account_fields(self) -> tuple[str, str, str, str]:
        return (
            normalize_dashboard_url(self.login_website.get()),
            self.login_username.get().strip(),
            self.login_password.get(),
            self.login_2fa.get().strip(),
        )

    def _refresh_account_buttons(self) -> None:
        for slot, button in enumerate(self.account_use_btns, start=1):
            button.configure(text=self._account_button_text(slot))

    def _workspace_change_blocked(self) -> bool:
        if not (self._busy() or self.auto_running):
            return False
        messagebox.showinfo(
            "Finish the current run first",
            "Stop Automated Run or wait for the current scrape/send to finish "
            "before changing website or account.",
        )
        return True

    def _select_account(self, slot: int) -> None:
        account = self.login_accounts[slot - 1]
        if not account["username"] and not account["password"] and not account.get("website"):
            messagebox.showinfo(
                f"Account {slot} is empty",
                "Enter website, username, password, and 2FA above, then click Save on this account.",
            )
            return
        next_key = workspace_key(account.get("website", ""), account["username"])
        if next_key != self.workspace_key and self._workspace_change_blocked():
            return
        self.active_account.set(slot)
        self.login_website.set(account.get("website", ""))
        self.login_username.set(account["username"])
        self.login_password.set(account["password"])
        self.login_2fa.set(account["twofa"])
        self.saved_username = account["username"]
        self.saved_password = account["password"]
        self.saved_2fa = account["twofa"]
        persist_login_account(
            slot,
            account.get("website", ""),
            account["username"],
            account["password"],
            account["twofa"],
        )
        self._refresh_account_buttons()
        if not self._switch_workspace_if_needed(log=False):
            return
        host = self._account_host(account.get("website", ""))
        self._append_log(
            f"Using saved Account {slot} ({account['username'] or 'no username'}"
            + (f" on {host}" if host else "")
            + "). Showing only this website's transactions and Google Sheets."
        )

    def _save_account(self, slot: int) -> None:
        website, username, password, twofa = self._account_fields()
        if not website or not username or not password:
            messagebox.showwarning(
                "Nothing to save",
                "Enter a website, username, and password before saving this account.",
            )
            return
        self.login_website.set(website)
        self.active_account.set(slot)
        self.login_accounts[slot - 1] = {
            "slot": str(slot),
            "website": website,
            "username": username,
            "password": password,
            "twofa": twofa,
        }
        persist_login_account(slot, website, username, password, twofa)
        self.saved_username = username
        self.saved_password = password
        self.saved_2fa = twofa
        self._refresh_account_buttons()
        self._switch_workspace_if_needed(log=False)
        self._save_current_workspace_state()
        self._append_log(
            f"Saved current login fields and this website's records/sheets to "
            f"Account {slot} ({username} on {self._account_host(website)})."
        )

    def _clear_account(self, slot: int) -> None:
        account = self.login_accounts[slot - 1]
        if not account["username"] and not account["password"] and not account.get("website"):
            messagebox.showinfo(f"Account {slot}", "This saved account is already empty.")
            return
        if not messagebox.askyesno(
            f"Clear Account {slot}",
            f"Remove the saved website, username, password, and 2FA from Account {slot} only?",
        ):
            return
        self.login_accounts[slot - 1] = clear_login_account(slot)
        if self.active_account.get() == slot:
            self.login_website.set("")
            self.login_username.set("")
            self.login_password.set("")
            self.login_2fa.set("")
            self.saved_username = ""
            self.saved_password = ""
            self.saved_2fa = ""
        self._refresh_account_buttons()
        if self.active_account.get() == slot:
            self._switch_workspace_if_needed(log=False)
        self._append_log(f"Cleared saved Account {slot}. The other accounts were left as they are.")

    def _persist_login_fields(self) -> None:
        website, username, password, twofa = self._account_fields()
        self.login_website.set(website)
        slot = self.active_account.get()
        self.login_accounts[slot - 1] = {
            "slot": str(slot),
            "website": website,
            "username": username,
            "password": password,
            "twofa": twofa,
        }
        persist_login_account(slot, website, username, password, twofa)
        self.saved_username = username
        self.saved_password = password
        self.saved_2fa = twofa
        self._refresh_account_buttons()
        if self.workspace_key:
            self._save_current_workspace_state()

    def _scrape_date(self) -> str:
        selected = self.date_filter.get().strip()
        if selected in {"", "All dates", "(blank)"}:
            return local_today()
        return selected

    def _select_today(self) -> None:
        today = local_today()
        self.date_filter.set(today)
        self.sent_date_filter.set(today)
        self._refresh_filter_options()
        self._apply_filters()
        self._append_log(f"Date filter set to today ({today}).")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-r>", lambda _event: self._run_now())
        self.root.bind("<Control-R>", lambda _event: self._run_now())
        self.root.bind("<Control-t>", lambda _event: self._select_today())
        self.root.bind("<Control-T>", lambda _event: self._select_today())
        self.root.bind("<Control-Key-1>", lambda _event: self._show_tab(0))
        self.root.bind("<Control-Key-2>", lambda _event: self._show_tab(1))
        self.root.bind("<Control-Key-3>", lambda _event: self._show_tab(2))
        self.root.bind("<Control-Key-4>", lambda _event: self._show_tab(3))
        self.root.bind("<Control-D>", lambda _event: self._send_section_to_sheet("deposit"))
        self.root.bind("<Control-W>", lambda _event: self._send_section_to_sheet("withdraw"))
        self.root.bind("<Control-S>", lambda _event: self._sync_records_with_sheet())
        self.root.bind("<Control-l>", lambda _event: self._copy_selected_ids())
        self.root.bind("<Control-L>", lambda _event: self._copy_selected_ids())

    def _show_tab(self, index: int) -> None:
        self.pages.select(index)
        names = ("Latest scrape", "Deposits", "Withdrawals", "Google Sheet sent")
        if 0 <= index < len(names):
            self._append_log(f"Opened {names[index]}.")

    def _current_section(self) -> str:
        try:
            index = int(self.pages.index(self.pages.select()))
        except Exception:
            return "latest"
        return ("latest", "deposit", "withdraw", "sent")[index]

    def _select_all_to_send(self) -> None:
        section = self._current_section()
        if section == "sent":
            self.pages.select(1)
            section = "deposit"
        tree = self._tree_for(section)
        if section == "deposit":
            self.deposit_status_filter.set("To send")
        elif section == "withdraw":
            self.withdraw_status_filter.set("To send")
        self._apply_filters()
        selected: list[str] = []
        for item in tree.get_children(""):
            rec = self.row_store.get((section, item))
            if not rec or not self._row_matches(rec, section):
                continue
            status = str((rec.get("tags") or ("",))[0])
            if status in {"Copied", "Skipped"}:
                continue
            selected.append(item)
        tree.selection_set(selected)
        self._update_filter_caption()
        self._append_log(f"Selected {len(selected)} to-send row(s) in {section}.")

    def _copy_selected_ids(self) -> None:
        section = self._current_section()
        recs = self._selected_records(section) or self._section_records(section, visible_only=True)
        ids: list[str] = []
        for rec in recs:
            values = rec.get("values") or ()
            txn_id = str(values[1] if len(values) > 1 else "")
            if txn_id:
                ids.append(txn_id)
        if not ids:
            messagebox.showinfo("No IDs", "No transaction IDs in the current view to copy.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(ids))
        self._append_log(f"Copied {len(ids)} transaction ID(s) to the clipboard.")

    def _sheet_id_from_field(self, which: int = 1) -> str:
        index = max(1, min(int(which or 1), len(self.google_sheet_vars))) - 1
        return normalize_google_sheet_id(self.google_sheet_vars[index].get())

    def _persist_google_sheets(self, remember_workspace: bool = True) -> list[str]:
        ids = [self._sheet_id_from_field(slot) for slot in GOOGLE_SHEET_SLOTS]
        persist_env_values(
            {google_sheet_env_key(slot): ids[slot - 1] for slot in GOOGLE_SHEET_SLOTS}
        )
        for slot, sheet_id in enumerate(ids, start=1):
            self.google_sheet_vars[slot - 1].set(google_sheet_url(sheet_id) or sheet_id)
        self.settings = Settings.load()
        apply_workspace_to_settings(self.settings, self.workspace_key)
        if remember_workspace:
            self._save_current_workspace_state()
        return ids

    def _save_google_sheets(self) -> None:
        ids = self._persist_google_sheets()
        if not ids[0]:
            messagebox.showwarning(
                "Google Sheet required",
                "Paste the Google Sheet URL or ID into Sheet 1 for this website.",
            )
            return
        filled = [index for index, sheet_id in enumerate(ids, start=1) if sheet_id]
        labels = ", ".join(f"Sheet {index}" for index in filled)
        label = self._workspace_label(self.workspace_key)
        self._append_log(
            f"Saved {labels}"
            + (f" for {label}" if label else "")
            + ". Send will copy the same rows to each filled sheet for this website only."
        )

    def _open_google_sheet(self, which: int = 1) -> None:
        sheet_id = self._sheet_id_from_field(which) or self.settings.sheet_id_at(which)
        if not sheet_id:
            messagebox.showinfo(
                "Google Sheet not set",
                "Paste a Google Sheet URL into Sheet 1–5, then click Save sheets.",
            )
            return
        webbrowser.open(google_sheet_url(sheet_id))
        self._append_log(f"Opened Google Sheet {which} in the browser.")

    def _current_settings(self) -> Settings:
        settings = Settings.load()
        settings.headed = not self.headless.get()
        settings.use_open_browser = False
        settings.poll_interval_seconds = int(self.poll_interval.get() or 60)
        settings.dashboard_url = normalize_dashboard_url(self.login_website.get())
        settings.dashboard_username = self.login_username.get().strip()
        settings.dashboard_password = self.login_password.get()
        code = "".join(ch for ch in self.login_2fa.get() if ch.isdigit())
        settings.dashboard_2fa = code[:6] if code else self.login_2fa.get().strip()
        day = self._scrape_date()
        settings.filter_date_from = day
        settings.filter_date_to = day
        settings.filter_status = COMPLETED_STATUS
        types = []
        if self.scrape_deposits.get():
            types.append(STAFF_DEPOSIT_TYPE)
        if self.scrape_withdrawals.get():
            types.append(STAFF_WITHDRAW_TYPE)
        settings.filter_type = ",".join(types) or f"{STAFF_DEPOSIT_TYPE},{STAFF_WITHDRAW_TYPE}"
        settings.use_dashboard_api = False
        for slot in GOOGLE_SHEET_SLOTS:
            sheet_id = self._sheet_id_from_field(slot)
            settings.set_sheet_id_at(slot, sheet_id)
        apply_workspace_to_settings(settings, self._current_workspace_key() or self.workspace_key)
        settings.sheet_brands = tuple(self._brands_from_field())
        return settings

    def _scrape_ready(self, action: str) -> bool:
        if self.auto_running and action == "Run now":
            messagebox.showinfo(
                "Automated run is active",
                "Click Stop Automated Run before using Run now.",
            )
            return False
        if not normalize_dashboard_url(self.login_website.get()):
            messagebox.showwarning(
                "Website required",
                "Enter the dashboard website for this account before running.",
            )
            return False
        if not self.login_username.get().strip() or not self.login_password.get():
            messagebox.showwarning(
                "Login required",
                "Enter username and password in the Website login section, "
                "or click a saved account.",
            )
            return False
        if not self.scrape_deposits.get() and not self.scrape_withdrawals.get():
            messagebox.showwarning(
                "Nothing selected",
                "Check Scrape deposits and/or Scrape withdrawals before running.",
            )
            return False
        return True

    def _auto_interval_seconds(self) -> int:
        try:
            value = int(self.auto_interval.get() or 60)
        except (tk.TclError, TypeError, ValueError):
            value = 60
        return max(5, value)

    def _toggle_auto_send(self, _event=None) -> None:
        self.auto_send_to_sheet.set(not self.auto_send_to_sheet.get())
        self._on_auto_send_toggled()

    def _on_auto_send_toggled(self) -> None:
        button = getattr(self, "send_latest_btn", None)
        if button is None:
            return
        if self.auto_send_to_sheet.get():
            button.state(["disabled"])
        else:
            button.state(["!disabled"])

    def _auto_write_sheet(self) -> bool:
        if not self.auto_send_to_sheet.get():
            return False
        self._persist_google_sheets()
        if self._sheet_id_from_field(1):
            return True
        self._append_log(
            "Send Extracted records to Google Sheet Automatically is checked, "
            "but Sheet 1 is empty. This scrape will not write the sheet."
        )
        return False

    def _start_automated_run(self) -> None:
        if self.auto_running:
            messagebox.showinfo("Already running", "Automated Run is already active.")
            return
        if not self._scrape_ready("Automated Run"):
            return
        if self._busy():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        self.auto_send_to_sheet.set(True)
        self._persist_google_sheets()
        if not self._sheet_id_from_field(1):
            messagebox.showwarning(
                "Google Sheet required",
                "Paste the Google Sheet URL or ID into Sheet 1 so Completed "
                "rows can be written after each scrape.",
            )
            return
        self._persist_login_fields()
        if not self._switch_workspace_if_needed():
            return
        self._single_run_active = False
        self.auto_running = True
        self.capturing_latest = True
        self.arm_watcher_after_run = False
        self.pages.select(0)
        self._live_stop = threading.Event()
        seconds = self._auto_interval_seconds()
        self.auto_interval.set(seconds)
        self.poll_interval.set(seconds)
        self.status_text.set("Automated run: scraping Completed")
        self._append_log(
            "Automated Run started. The browser will select the date, set Type to "
            "STAFF DEPOSIT and STAFF WITHDRAW, set Status to COMPLETED, read those "
            "Completed rows into the GUI, and send new IDs to the Google Sheet "
            "(deposits from row 105, withdrawals from row 1024 on the day tab). "
            "Deposit ATTACHMENT screenshots are used to fill BANK on deposit rows."
            f" The next scrape waits {seconds}s after this one finishes."
        )
        self._auto_tick()

    def _stop_automated_run(self) -> None:
        if not self.auto_running and self._auto_after_id is None:
            self._append_log("Automated Run is not active.")
            return
        self.auto_running = False
        self._live_stop.set()
        self._cancel_auto_timer()
        if not self._busy():
            self.capturing_latest = False
            self.status_text.set("Idle")
        self._append_log("Automated Run stopped. Latest scrape rows are kept until you clear them.")

    def _cancel_auto_timer(self) -> None:
        if self._auto_after_id is None:
            return
        try:
            self.root.after_cancel(self._auto_after_id)
        except Exception:
            pass
        self._auto_after_id = None

    def _schedule_next_auto(self) -> None:
        if not self.auto_running:
            return
        self._cancel_auto_timer()
        seconds = self._auto_interval_seconds()
        self._auto_deadline = time.monotonic() + seconds
        self.status_text.set(f"Automated run: next scrape in {seconds}s")
        self._append_log(f"Automated Run waiting {seconds}s before the next scrape.")
        self._auto_after_id = self.root.after(1000, self._auto_countdown)

    def _auto_countdown(self) -> None:
        self._auto_after_id = None
        if not self.auto_running:
            return
        remaining = int(round(self._auto_deadline - time.monotonic()))
        if remaining <= 0:
            self._auto_tick()
            return
        self.status_text.set(f"Automated run: next scrape in {remaining}s")
        self._auto_after_id = self.root.after(1000, self._auto_countdown)

    def _auto_tick(self) -> None:
        self._auto_after_id = None
        if not self.auto_running:
            return
        if self._busy():
            self.status_text.set("Automated run: waiting for the current job to finish")
            self._auto_after_id = self.root.after(1000, self._auto_tick)
            return
        self.capturing_latest = True
        self.pages.select(0)
        write_sheet = self._auto_write_sheet()
        self._append_log(
            f"Automated Run tick: selecting date {self._scrape_date()}, "
            "Type STAFF DEPOSIT + STAFF WITHDRAW, Status COMPLETED, "
            f"and scraping every page on {normalize_dashboard_url(self.login_website.get())}."
            + (
                " Deposit ATTACHMENT screenshots will fill BANK on new deposit rows."
                if write_sheet
                else ""
            )
            + (
                " New extracted records will be sent to the Google Sheet one by one."
                if write_sheet
                else ""
            )
        )
        try:
            started = self._start_job(
                scrape=True,
                write_sheet=write_sheet,
                quiet=True,
                once=False,
                one_by_one=write_sheet,
            )
            if not started:
                self._schedule_next_auto()
        except Exception as exc:
            self._append_log(f"Automated Run tick failed: {exc}")
            self._schedule_next_auto()

    def _already_on_sheet(self, txn_id: str) -> bool:
        if not txn_id:
            return False
        selected = self.date_filter.get()
        if self.sheet_id_cache and (
            not self.sheet_id_cache_date
            or selected in {"", "All dates"}
            or self.sheet_id_cache_date == selected
        ):
            return txn_id in self.sheet_id_cache
        if self.bulk_loading or self._scrape_busy:
            return False
        for rec in self._section_records("sent"):
            values = rec.get("values") or ()
            if len(values) > 1 and str(values[1]) == txn_id:
                status = str((rec.get("tags") or ("",))[0])
                if status in {"Copied", "Skipped"}:
                    return True
        return False

    def _event_from_rec(self, rec: dict, status: str, detail: str) -> dict:
        values = rec.get("values") or ()

        def col(index: int) -> str:
            return str(values[index]) if len(values) > index else ""

        return {
            "transaction_id": col(1),
            "username": col(2),
            "name": col(3),
            "mobile": col(4),
            "amount": col(5),
            "type": rec.get("type") or col(6),
            "bank": col(7),
            "acc_name": col(8),
            "acc_no": col(9),
            "bsb": col(10),
            "pay_id": col(11),
            "bank_lock": col(12),
            "method": col(13),
            "brand": col(14),
            "datetime": col(0),
            "created": col(15),
            "processed": col(16),
            "tally_date": rec.get("date") or "",
            "status": status,
            "detail": detail,
        }

    def _remove_latest_id(self, txn_id: str) -> None:
        store_key = self._item_key("latest", txn_id)
        if store_key in self.row_items:
            _section, item = self.row_items.pop(store_key)
            tree = self._tree_for("latest")
            if tree.exists(item):
                tree.delete(item)
            self.row_store.pop(("latest", item), None)
        self.latest_run_ids.discard(txn_id)

    def _refresh_unsent_latest(self, log: bool = True) -> None:
        dated = self._dated(self._section_records("deposit")) + self._dated(
            self._section_records("withdraw")
        )
        by_id: dict[str, dict] = {}
        for rec in dated:
            values = rec.get("values") or ()
            txn_id = str(values[1] if len(values) > 1 else "")
            if txn_id:
                by_id[txn_id] = rec
        if not by_id:
            return
        unsent = [rec for txn_id, rec in by_id.items() if not self._already_on_sheet(txn_id)]
        self.bulk_loading = True
        try:
            for txn_id in list(self.latest_run_ids):
                if txn_id in by_id and self._already_on_sheet(txn_id):
                    self._remove_latest_id(txn_id)
            for rec in unsent:
                values = rec.get("values") or ()
                txn_id = str(values[1] if len(values) > 1 else "")
                if not txn_id:
                    continue
                self.latest_run_ids.add(txn_id)
                self._upsert_row(
                    self._event_from_rec(rec, "Gathered", "Not sent to Google Sheet yet"),
                    bucket="latest",
                )
        finally:
            self.bulk_loading = False
        missing = len(unsent)
        extracted = len(by_id)
        sent = extracted - missing
        if log:
            if missing:
                self._append_log(
                    f"{missing} extracted record(s) are not on the Google Sheet. "
                    f"They are listed in Latest scrape ({extracted} extracted · {sent} already sent)."
                )
            else:
                self._append_log(
                    f"All {extracted} extracted record(s) for this date are on the Google Sheet."
                )
        if missing and not self._prefer_sent_tab:
            self.pages.select(0)
        self._update_filter_caption()

    def _queue_sheet_unsent_check(self) -> None:
        day = self.date_filter.get().strip() or local_today()
        if day in {"", "All dates", "(blank)"}:
            day = self.website_date or local_today()
        self._refresh_unsent_latest(log=False)

        def work() -> None:
            try:
                settings = self._current_settings()
                settings.require_sheets()
                sheet = SheetClient(
                    settings.google_credentials_path,
                    settings.google_sheet_id,
                    settings.google_worksheet,
                )
                sheet.use_day(day)
                ids = sheet.existing_ids()
                self.events.put(
                    {
                        "kind": "sheet_ids",
                        "ids": list(ids),
                        "date": day,
                        "count": len(ids),
                    }
                )
            except Exception as exc:
                mapped = sheet_open_error(exc, settings.google_credentials_path)
                self.events.put(
                    {
                        "kind": "log",
                        "message": f"Could not compare Google Sheet IDs: {mapped or exc}",
                    }
                )

        threading.Thread(target=work, name="sheet-unsent", daemon=True).start()

    def _run_now(self) -> None:
        if not self._scrape_ready("Run now"):
            return
        self._persist_login_fields()
        if not self._switch_workspace_if_needed():
            return
        self._clear_bucket("latest")
        self.latest_run_ids = set()
        self.capturing_latest = True
        self.arm_watcher_after_run = False
        self._single_run_active = True
        self.auto_running = False
        self._cancel_auto_timer()
        self.pages.select(0)
        write_sheet = self._auto_write_sheet()
        self._append_log(
            "Run now started. Type STAFF DEPOSIT and STAFF WITHDRAW with Status "
            "COMPLETED will be read once, remaining needed transactions will be "
            "tracked, then this run will stop."
            + (
                " New extracted records will be sent to the Google Sheet."
                if write_sheet
                else ""
            )
        )
        try:
            self._start_job(
                scrape=True,
                write_sheet=write_sheet,
                once=True,
                one_by_one=write_sheet,
            )
        except Exception as exc:
            self._single_run_active = False
            self.capturing_latest = False
            messagebox.showerror("Run now failed", str(exc))
            self._append_log(f"Run now failed: {exc}")

    def _send_latest_to_sheet(self) -> None:
        if self.auto_send_to_sheet.get():
            messagebox.showinfo(
                "Automatic send is on",
                "Turn off Send Extracted records to Google Sheet Automatically "
                "to send the latest scrape manually.",
            )
            return
        if self._busy():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        ids = self._ids_to_send("latest")
        if not ids and not self.latest_tree.get_children(""):
            ids = [txn_id for txn_id in self.latest_run_ids if txn_id]
        if not ids:
            messagebox.showinfo(
                "Nothing to send",
                "Run now or Automated Run first. Then send this latest scrape to Google Sheets.",
            )
            return
        self._start_sheet_send(ids, "latest scrape")

    def _sync_records_with_sheet(self) -> None:
        if self._busy():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        day = self.sent_date_filter.get().strip() or local_today()
        self._persist_google_sheets()
        settings = self._current_settings()
        self.status_text.set("Syncing Google Sheet...")
        self.open_sent_after_send = True
        tab = sheet_tab_name(day) or day
        slots = [f"Sheet {slot}" for slot, _sheet_id in settings.sheet_slots()]
        self._append_log(
            f"Syncing GUI records for {day} to tab {tab} on "
            + (", ".join(slots) if slots else "Sheet 1")
            + "."
        )

        def work() -> None:
            try:
                sync_date_to_sheet(settings, day, on_event=self.events.put)
            except Exception as exc:
                self.events.put({"kind": "log", "message": f"Sync failed: {exc}"})
                self.events.put({"kind": "done", "message": str(exc)})

        self.worker = threading.Thread(target=work, name="sheet-sync", daemon=True)
        self.worker.start()

    def _send_section_to_sheet(self, section: str) -> None:
        if self._busy():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        ids = self._ids_to_send(section)
        if not ids:
            label = "deposits" if section == "deposit" else "withdrawals"
            messagebox.showinfo(
                "Nothing to send",
                f"No to-send {label} match the current date filter"
                + (" and selection." if self._tree_for(section).selection() else "."),
            )
            return
        label = "deposits" if section == "deposit" else "withdrawals"
        selected = bool(self._tree_for(section).selection())
        self._start_sheet_send(
            ids,
            f"{label} {'you selected' if selected else 'waiting to send'}",
        )

    def _start_sheet_send(self, ids: list[str], label: str) -> None:
        self._persist_google_sheets()
        settings = self._current_settings()
        self.status_text.set("Sending to Google Sheet...")
        self.open_sent_after_send = True
        slots = [f"Sheet {slot}" for slot, _sheet_id in settings.sheet_slots()]
        self._append_log(
            f"Sending {len(ids)} {label} to "
            + (", ".join(slots) if slots else "Google Sheets")
            + "."
        )

        def work() -> None:
            try:
                self.db.requeue(ids)
                process_new_notifications_only(
                    settings,
                    on_event=self.events.put,
                    dry_run=False,
                    only_ids=set(ids),
                )
            except Exception as exc:
                self.events.put({"kind": "log", "message": f"Send failed: {exc}"})
                self.events.put({"kind": "done", "message": str(exc)})

        self.worker = threading.Thread(target=work, name="sheet-send", daemon=True)
        self.worker.start()

    def _ids_to_send(self, section: str) -> list[str]:
        tree = self._tree_for(section)
        selected = list(tree.selection())
        items = selected or list(tree.get_children(""))
        ids: list[str] = []
        for item in items:
            rec = self.row_store.get((section, item))
            if not rec or not self._row_matches(rec, section):
                continue
            status = str((rec.get("tags") or ("",))[0])
            if status in {"Copied", "Skipped"}:
                continue
            txn_id = str(rec["values"][1] if rec.get("values") else "")
            if txn_id:
                ids.append(txn_id)
        return ids

    def _start_job(
        self,
        scrape: bool,
        write_sheet: bool = False,
        quiet: bool = False,
        once: bool = False,
        one_by_one: bool = False,
    ) -> bool:
        if self._busy():
            if not quiet:
                messagebox.showinfo("Busy", "A run is already in progress.")
            return False
        self.status_text.set(
            "Running and sending to Google Sheet..." if write_sheet else "Running..."
        )
        self._scrape_busy = True
        self.bulk_loading = True
        settings = self._current_settings()
        if write_sheet:
            sheet_note = (
                "Each extracted record will be sent to the Google Sheet automatically."
            )
        else:
            sheet_note = "The sheet is not updated yet."
        self._append_log(
            f"Gathering Completed transactions for {settings.filter_date_from} "
            f"from {settings.dashboard_url} as {settings.dashboard_username}. "
            f"{sheet_note}"
        )
        if settings.dashboard_2fa:
            self._append_log("Using the 2FA code from the Website login section.")
        else:
            self._append_log("No 2FA code entered. If the site asks, type it in the browser window.")

        def job(session) -> None:
            try:
                run_pipeline(
                    settings,
                    on_event=self.events.put,
                    scrape=scrape,
                    write_sheet=write_sheet,
                    once=once,
                    one_by_one=one_by_one,
                    session=session,
                )
            except Exception as exc:
                self.events.put({"kind": "log", "message": f"Run failed: {exc}"})
                self.events.put({"kind": "done", "message": str(exc)})

        self._ensure_scrape_worker()
        assert self._scrape_jobs is not None
        self._scrape_jobs.put(job)
        return True

    def _stop_watcher(self) -> None:
        if self.watcher:
            try:
                self.watcher.stop()
            except Exception:
                pass
            self.watcher = None

    def _drain_events(self) -> None:
        processed = 0
        while processed < EVENT_BATCH:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
            except Exception as exc:
                self._append_log(f"Event handler error: {exc}")
            processed += 1
        if self._tally_dirty:
            self._flush_live_tally()
        if not self._busy() and self.status_text.get() in {
            "Running...",
            "Running and sending to Google Sheet...",
            "Sending to Google Sheet...",
            "Syncing Google Sheet...",
        }:
            if self.auto_running:
                seconds = self._auto_interval_seconds()
                self.status_text.set(f"Automated run: next scrape in {seconds}s")
            else:
                self.status_text.set("Idle")
        if (
            self.auto_running
            and not self._single_run_active
            and not self._busy()
            and self._live_stop.is_set()
        ):
            self.auto_running = False
            self.capturing_latest = False
            self.status_text.set("Idle")
            self._append_log("Automated Run ended.")
        self.root.after(20 if processed else 50, self._drain_events)

    def _handle_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "row":
            status = str(event.get("status") or "")
            txn_id = str(event.get("transaction_id") or "")
            if self.capturing_latest and status == "Gathered" and self._type_wanted(event.get("type")):
                if txn_id and self._already_on_sheet(txn_id):
                    pass
                else:
                    if txn_id:
                        self.latest_run_ids.add(txn_id)
                    self._upsert_row(event, bucket="latest")
            if status in {"Gathered", "Copied", "Skipped", "Failed", "Preview", "Pending"}:
                if self._type_wanted(event.get("type")) or status != "Gathered":
                    self._upsert_row(event)
            if status in {"Copied", "Skipped", "Failed"} and txn_id in self.latest_run_ids:
                self._upsert_row(event, bucket="latest")
            if status in {"Copied", "Skipped"}:
                self._upsert_row(event, bucket="sent")
                if txn_id:
                    self.sheet_id_cache.add(txn_id)
            self._mark_tally_dirty()
        if event.get("message"):
            self._append_log(str(event["message"]))
        if event.get("counts"):
            self._apply_counts(event["counts"])
        if kind == "website_tally":
            self.website_records = int(event.get("records") or 0)
            self.website_total = str(event.get("total") or "")
            self.website_date = str(event.get("date") or "")
            if self.website_date:
                self.date_filter.set(self.website_date)
                self.sent_date_filter.set(self.website_date)
            if not self.bulk_loading:
                self._refresh_filter_options()
                self._apply_filters()
            self._mark_tally_dirty()
        if kind == "sheet_tally":
            self.sheet_date_count = int(event.get("sheet_count") or 0)
            self.sheet_tally_date = str(event.get("date") or "")
            self._mark_tally_dirty()
        if kind == "sheet_ids":
            self.sheet_id_cache = {str(item) for item in (event.get("ids") or []) if item}
            self.sheet_id_cache_date = str(event.get("date") or "")
            self.sheet_date_count = int(event.get("count") or len(self.sheet_id_cache))
            if self.sheet_id_cache_date:
                self.sheet_tally_date = self.sheet_id_cache_date
            self._refresh_unsent_latest()
        if kind == "remaining":
            remaining = int(event.get("remaining") or 0)
            scraped = int(event.get("scraped") or 0)
            records = int(event.get("records") or 0)
            if remaining:
                self.match_caption.set(
                    f"Remaining needed: {remaining}  ·  extracted {scraped}"
                    + (f" of website Record {records}" if records else "")
                )
        if kind == "done":
            self.bulk_loading = False
            will_reload = bool(self._single_run_active) or not self.auto_running
            try:
                if not will_reload:
                    self._prune_gui_rows()
                    self._refresh_filter_options()
                    self._apply_filters()
            except Exception:
                pass
            self._prefer_sent_tab = self.open_sent_after_send
            if self._single_run_active:
                self._single_run_active = False
                self.auto_running = False
                self._cancel_auto_timer()
                self.capturing_latest = False
                self.status_text.set("Idle")
                try:
                    self._refresh_counts()
                    self._reload_workspace()
                except Exception as exc:
                    self._append_log(f"Could not refresh workspace: {exc}")
                try:
                    self._queue_sheet_unsent_check()
                except Exception as exc:
                    self._append_log(f"Unsent check failed: {exc}")
                return
            if self.auto_running:
                self.capturing_latest = True
                self._schedule_next_auto()
                try:
                    self._refresh_counts()
                except Exception as exc:
                    self._append_log(f"Could not refresh counts: {exc}")
                try:
                    self._queue_sheet_unsent_check()
                except Exception as exc:
                    self._append_log(f"Unsent check failed: {exc}")
                return
            self.capturing_latest = False
            self.status_text.set("Idle")
            try:
                self._refresh_counts()
                self._reload_workspace()
            except Exception as exc:
                self._append_log(f"Could not refresh workspace: {exc}")
            if self.open_sent_after_send:
                self.open_sent_after_send = False
                self.pages.select(3)
            try:
                self._queue_sheet_unsent_check()
            except Exception as exc:
                self._append_log(f"Unsent check failed: {exc}")

    def _row_values(self, event: dict, stamp: str | None = None) -> tuple:
        when = record_local_datetime(
            event.get("datetime"),
            event.get("created"),
            event.get("processed"),
            stamp,
        ) or self.website_date or self._scrape_date()
        created = record_local_datetime(event.get("created")) or when
        processed = record_local_datetime(event.get("processed")) or when
        return (
            when,
            str(event.get("transaction_id") or ""),
            event.get("username") or "",
            event.get("name") or "",
            event.get("mobile") or "",
            event.get("amount") or "",
            event.get("type") or "",
            event.get("bank") or "",
            event.get("acc_name") or "",
            event.get("acc_no") or "",
            event.get("bsb") or "",
            event.get("pay_id") or "",
            event.get("bank_lock") or "",
            event.get("method") or "",
            event.get("brand") or "",
            created,
            processed,
            event.get("status") or "",
            event.get("detail") or "",
        )

    def _display_type(self, raw: object) -> str:
        value = str(raw or "").strip()
        return value or "(blank)"

    def _extract_date(self, *parts: object) -> str:
        for raw in parts:
            match = DATE_RE.search(str(raw or ""))
            if match:
                return match.group(1)
        return ""

    def _display_date(self, raw: object) -> str:
        value = str(raw or "").strip()
        return value or "(blank)"

    def _row_date(self, event: dict, values: tuple) -> str:
        tally = self._extract_date(event.get("tally_date"))
        if tally:
            return tally
        when = record_local_datetime(
            event.get("processed"),
            event.get("datetime"),
            event.get("created"),
            values[0] if values else "",
        )
        return self._extract_date(when) or self.website_date or self._scrape_date()

    def _known_dates(self) -> list[str]:
        dates: set[str] = set()
        for rec in self.row_store.values():
            dates.add(self._display_date(rec.get("date")))
        return sorted(dates, reverse=True)

    def _date_filter_options(self) -> list[str]:
        today = local_today()
        known = [item for item in self._known_dates() if item and item != "(blank)"]
        ordered = [today]
        for item in sorted(set(known) - {today}, reverse=True):
            ordered.append(item)
        return ["All dates", *ordered]

    def _date_matches(self, raw: str, section: str | None = None) -> bool:
        selected = self.sent_date_filter.get() if section == "sent" else self.date_filter.get()
        if selected == "All dates":
            return True
        return self._display_date(raw) == selected

    def _type_filter_value(self, section: str) -> str:
        if section == "latest":
            return self.latest_type_filter.get()
        if section == "sent":
            return self.sent_type_filter.get()
        return "All types"

    def _status_filter_value(self, section: str) -> str:
        if section == "deposit":
            return self.deposit_status_filter.get()
        if section == "withdraw":
            return self.withdraw_status_filter.get()
        return "All"

    def _type_matches(self, raw: str, section: str) -> bool:
        selected = self._type_filter_value(section)
        if selected == "All types":
            return True
        kind = txn_kind(raw)
        key = selected.strip().upper()
        if key in {"DEPOSIT", "STAFF DEPOSIT"}:
            return kind == "deposit"
        if key in {"WITHDRAW", "STAFF WITHDRAW", "WITHDRAWAL"}:
            return kind == "withdraw"
        return self._display_type(raw).upper().startswith(selected)

    def _status_matches(self, status: str, section: str) -> bool:
        selected = self._status_filter_value(section)
        key = str(status or "").title()
        if selected == "All":
            return True
        if selected == "To send":
            return key in {"Pending", "Gathered", "Failed", "Preview", "Copying"}
        if selected == "Sent":
            return key in {"Copied", "Skipped"}
        if selected == "Failed":
            return key == "Failed"
        return True

    def _row_matches(self, rec: dict, section: str | None = None) -> bool:
        bucket = section or str(rec.get("section") or "")
        return (
            self._date_matches(str(rec.get("date") or ""), bucket)
            and self._type_matches(str(rec.get("type") or ""), bucket)
            and self._status_matches(str((rec.get("tags") or ("",))[0]), bucket)
        )

    def _section_records(self, section: str, visible_only: bool = False) -> list[dict]:
        rows = []
        for key, rec in self.row_store.items():
            if rec.get("section") != section:
                continue
            if visible_only and not self._row_matches(rec, section):
                continue
            rows.append(rec)
        return rows

    def _amount_of(self, rec: dict) -> float:
        values = rec.get("values") or ()
        return parse_amount(values[5] if len(values) > 5 else "")

    def _tally_text(self, recs: list[dict]) -> str:
        total = sum(self._amount_of(rec) for rec in recs)
        return f"{len(recs)} txn  ·  {format_amount(total)}"

    def _fmt_txn_money(self, count: int, total: float) -> str:
        return f"{count} txn  ·  {format_amount(total)}"

    def _mark_tally_dirty(self) -> None:
        self._tally_dirty = True
        if self._tally_after_id is not None:
            return
        self._tally_after_id = self.root.after(LIVE_TALLY_MS, self._flush_live_tally)

    def _flush_live_tally(self) -> None:
        if self._tally_after_id is not None:
            try:
                self.root.after_cancel(self._tally_after_id)
            except Exception:
                pass
            self._tally_after_id = None
        if not self._tally_dirty:
            return
        self._tally_dirty = False
        try:
            self._update_filter_caption()
        except Exception:
            pass

    def _selected_records(self, section: str) -> list[dict]:
        tree = self._tree_for(section)
        rows = []
        for item in tree.selection():
            rec = self.row_store.get((section, item))
            if rec:
                rows.append(rec)
        return rows

    def _record_ids(self, recs: list[dict]) -> set[str]:
        ids: set[str] = set()
        for rec in recs:
            values = rec.get("values") or ()
            txn_id = str(values[1] if len(values) > 1 else "")
            if txn_id:
                ids.add(txn_id)
        return ids

    def _update_match_caption(self) -> None:
        self._update_filter_caption()

    def _update_filter_caption(self) -> None:
        self._tally_dirty = False
        date_sel = self.date_filter.get() or "All dates"
        date_label = "all dates" if date_sel == "All dates" else date_sel
        sent_date = self.sent_date_filter.get() or date_label
        extracted_ids: set[str] = set()
        sent_ids: set[str] = set()
        pending_ids: set[str] = set()
        failed_ids: set[str] = set()
        latest_visible_ids: set[str] = set()
        latest_visible: list[dict] = []
        visible_counts = {"deposit": 0, "withdraw": 0, "sent": 0}
        dep_n = dep_amt = 0.0
        wd_n = wd_amt = 0.0
        money = {
            "deposit": {"pending": [0, 0.0], "sent": [0, 0.0], "failed": [0, 0.0], "visible": [0, 0.0]},
            "withdraw": {"pending": [0, 0.0], "sent": [0, 0.0], "failed": [0, 0.0], "visible": [0, 0.0]},
            "sent_dep": [0, 0.0],
            "sent_wd": [0, 0.0],
            "sent_vis": [0, 0.0],
        }

        for rec in self.row_store.values():
            section = str(rec.get("section") or "")
            values = rec.get("values") or ()
            txn_id = str(values[1] if len(values) > 1 else "")
            status = str((rec.get("tags") or ("",))[0])
            amount = self._amount_of(rec)
            dated = self._date_matches(str(rec.get("date") or ""), section)
            visible = self._row_matches(rec, section)
            if section in {"deposit", "withdraw"}:
                if dated:
                    if txn_id:
                        extracted_ids.add(txn_id)
                        if status in PENDING_TALLY_STATUSES:
                            pending_ids.add(txn_id)
                        elif status == "Failed":
                            failed_ids.add(txn_id)
                    if section == "deposit":
                        dep_n += 1
                        dep_amt += amount
                    else:
                        wd_n += 1
                        wd_amt += amount
                    bucket = money[section]
                    if status in PENDING_TALLY_STATUSES:
                        bucket["pending"][0] += 1
                        bucket["pending"][1] += amount
                    elif status in SENT_TALLY_STATUSES:
                        bucket["sent"][0] += 1
                        bucket["sent"][1] += amount
                    elif status == "Failed":
                        bucket["failed"][0] += 1
                        bucket["failed"][1] += amount
                if visible:
                    visible_counts[section] += 1
                    money[section]["visible"][0] += 1
                    money[section]["visible"][1] += amount
            elif section == "sent":
                if dated and txn_id:
                    sent_ids.add(txn_id)
                    kind = self._section_for_type(rec.get("type"))
                    key = "sent_wd" if kind == "withdraw" else "sent_dep"
                    money[key][0] += 1
                    money[key][1] += amount
                if visible:
                    visible_counts["sent"] += 1
                    money["sent_vis"][0] += 1
                    money["sent_vis"][1] += amount
            elif section == "latest" and visible:
                latest_visible.append(rec)
                if txn_id:
                    latest_visible_ids.add(txn_id)

        extracted = len(extracted_ids)
        sent = len(sent_ids)
        self.stat_extracted.set(str(extracted))
        self.stat_copied.set(str(sent))
        self.stat_pending.set(str(len(pending_ids)))
        self.stat_failed.set(str(len(failed_ids)))
        self.filter_caption.set(
            f"Completed · {date_label}  ·  "
            f"{extracted} unique txn  ·  "
            f"Deposits {self._fmt_txn_money(int(dep_n), dep_amt)}  ·  "
            f"Withdrawals {self._fmt_txn_money(int(wd_n), wd_amt)}"
        )
        self.latest_title.set(
            f"Latest scrape  ·  Extracted Transactions: {extracted} on {date_label}"
        )
        self.deposit_title.set(f"Deposits  ·  {visible_counts['deposit']} visible")
        self.withdraw_title.set(f"Withdrawals  ·  {visible_counts['withdraw']} visible")
        self.sent_title.set(
            f"Google Sheet sent data  ·  Sent Count to Google Sheets: {sent} on {sent_date}"
        )
        self.latest_tally.set(self._section_tally_line("latest", latest_visible))
        for section, variable in (
            ("deposit", self.deposit_tally),
            ("withdraw", self.withdraw_tally),
        ):
            selected = self._selected_records(section)
            extra = f"  ·  Selected {self._tally_text(selected)}" if selected else ""
            variable.set(
                f"To send {self._fmt_txn_money(*money[section]['pending'])}  ·  "
                f"Sent {self._fmt_txn_money(*money[section]['sent'])}  ·  "
                f"Failed {self._fmt_txn_money(*money[section]['failed'])}  ·  "
                f"Visible {self._fmt_txn_money(*money[section]['visible'])}"
                f"{extra}"
            )
        sent_selected = self._selected_records("sent")
        sent_extra = f"  ·  Selected {self._tally_text(sent_selected)}" if sent_selected else ""
        self.sent_tally.set(
            f"Sent deposits {self._fmt_txn_money(*money['sent_dep'])}  ·  "
            f"Sent withdrawals {self._fmt_txn_money(*money['sent_wd'])}  ·  "
            f"Visible {self._fmt_txn_money(*money['sent_vis'])}"
            f"{sent_extra}"
        )

        website = self.website_records
        website_bit = (
            f"Website Completed Record: {website}"
            + (f" · Total {self.website_total}" if self.website_total else "")
            if website
            else "Website Completed Record: —"
        )
        compare_date = self.website_date or date_sel
        scrape_match = bool(website) and website == extracted
        sheet_match = bool(website) and website == sent and extracted == sent
        if website and scrape_match and sheet_match:
            tally = "ALL MATCH"
        elif website and scrape_match:
            tally = "scrape matches Completed · sheet not sent in full"
        else:
            tally = "not matched yet"
        sheet_bit = ""
        if self.sheet_date_count and (
            not self.sheet_tally_date
            or self.sheet_tally_date in {compare_date, date_sel, self.sent_date_filter.get()}
        ):
            sheet_live = (
                "match" if website and self.sheet_date_count == website else "live count"
            )
            sheet_bit = f"  ·  Google Sheet live: {self.sheet_date_count} txn ({sheet_live})"
        self.match_caption.set(
            f"{website_bit}  ·  Scraped {compare_date}: {extracted} txn  ·  "
            f"Latest scrape: {len(latest_visible_ids)}  ·  "
            f"Google Sheet sent: {sent} txn  ·  {tally}"
            f"{sheet_bit}"
        )

    def _dated(self, recs: list[dict], section: str | None = None) -> list[dict]:
        return [rec for rec in recs if self._date_matches(str(rec.get("date") or ""), section)]

    def _section_tally_line(self, section: str, visible: list[dict]) -> str:
        deposits = [rec for rec in visible if self._section_for_type(rec.get("type")) == "deposit"]
        withdrawals = [rec for rec in visible if self._section_for_type(rec.get("type")) == "withdraw"]
        selected = self._selected_records(section)
        extra = f"  ·  Selected {self._tally_text(selected)}" if selected else ""
        return (
            f"Visible {self._tally_text(visible)}  ·  "
            f"Deposits {self._tally_text(deposits)}  ·  "
            f"Withdrawals {self._tally_text(withdrawals)}"
            f"{extra}"
        )

    def _money_tally_line(self, section: str) -> str:
        dated = self._dated(self._section_records(section))
        to_send = [
            rec
            for rec in dated
            if str((rec.get("tags") or ("",))[0]) in {"Pending", "Gathered", "Preview", "Copying"}
        ]
        sent = [rec for rec in dated if str((rec.get("tags") or ("",))[0]) in {"Copied", "Skipped"}]
        failed = [rec for rec in dated if str((rec.get("tags") or ("",))[0]) == "Failed"]
        visible = self._section_records(section, visible_only=True)
        selected = self._selected_records(section)
        extra = f"  ·  Selected {self._tally_text(selected)}" if selected else ""
        return (
            f"To send {self._tally_text(to_send)}  ·  "
            f"Sent {self._tally_text(sent)}  ·  "
            f"Failed {self._tally_text(failed)}  ·  "
            f"Visible {self._tally_text(visible)}"
            f"{extra}"
        )

    def _sent_tally_line(self, visible: list[dict]) -> str:
        dated = self._dated(self._section_records("sent"), "sent")
        deposits = [rec for rec in dated if self._section_for_type(rec.get("type")) == "deposit"]
        withdrawals = [rec for rec in dated if self._section_for_type(rec.get("type")) == "withdraw"]
        selected = self._selected_records("sent")
        extra = f"  ·  Selected {self._tally_text(selected)}" if selected else ""
        return (
            f"Sent deposits {self._tally_text(deposits)}  ·  "
            f"Sent withdrawals {self._tally_text(withdrawals)}  ·  "
            f"Visible {self._tally_text(visible)}"
            f"{extra}"
        )

    def _refresh_filter_options(self) -> None:
        date_options = self._date_filter_options()
        if self.date_filter.get() not in date_options:
            self.date_filter.set(local_today())
        if self.sent_date_filter.get() not in date_options:
            self.sent_date_filter.set(local_today())
        self.date_combo.configure(values=date_options)
        if hasattr(self, "sent_date_combo"):
            self.sent_date_combo.configure(values=date_options)
        self._mark_tally_dirty()

    def _prune_gui_rows(self) -> None:
        limits = {
            "latest": min(400, GUI_ROW_LIMIT),
            "deposit": GUI_ROW_LIMIT,
            "withdraw": GUI_ROW_LIMIT,
            "sent": GUI_ROW_LIMIT,
        }
        for section, limit in limits.items():
            entries = [
                (key, rec)
                for key, rec in list(self.row_store.items())
                if key[0] == section
            ]
            if len(entries) <= limit:
                continue
            entries.sort(
                key=lambda item: str((item[1].get("values") or ("",))[0]),
                reverse=True,
            )
            tree = self._tree_for(section)
            for key, rec in entries[limit:]:
                _bucket, item = key
                if tree.exists(item):
                    tree.delete(item)
                self.row_store.pop(key, None)
                values = rec.get("values") or ()
                txn_id = str(values[1] if len(values) > 1 else "")
                store_key = self._item_key(section, txn_id) if txn_id else ""
                if store_key and self.row_items.get(store_key) == (section, item):
                    self.row_items.pop(store_key, None)
                if section == "latest" and txn_id:
                    self.latest_run_ids.discard(txn_id)

    def _apply_filters(self) -> None:
        self._filter_gen += 1
        self._apply_filters_chunked(list(self.row_store.items()), 0, self._filter_gen)

    def _apply_filters_chunked(self, items: list, index: int, gen: int) -> None:
        if gen != self._filter_gen:
            return
        end = min(index + FILTER_BATCH, len(items))
        for key, rec in items[index:end]:
            section, item = key
            tree = self._tree_for(section)
            if not tree.exists(item):
                continue
            if self._row_matches(rec, section):
                tree.reattach(item, "", 0)
            else:
                tree.detach(item)
        if end < len(items):
            self.root.after(
                10,
                lambda rows=items, nxt=end, token=gen: self._apply_filters_chunked(
                    rows, nxt, token
                ),
            )
            return
        self._update_filter_caption()

    def _on_filters_changed(self, _event=None) -> None:
        self._apply_filters()
        self._append_log(
            f"Filters set to Date={self.date_filter.get()}, "
            f"Latest type={self.latest_type_filter.get()}, "
            f"Deposits={self.deposit_status_filter.get()}, "
            f"Withdrawals={self.withdraw_status_filter.get()}, "
            f"Sent date={self.sent_date_filter.get()}, "
            f"Sent type={self.sent_type_filter.get()}."
        )

    def _item_key(self, section: str, txn_id: str) -> str:
        return f"{section}:{txn_id}"

    def _keep_status(self, previous: str, incoming: str, bucket: str) -> str:
        if bucket == "latest":
            return incoming
        if incoming == "Gathered" and STATUS_RANK.get(previous, 0) >= STATUS_RANK.get("Copied", 4):
            return previous
        if STATUS_RANK.get(incoming, 0) >= STATUS_RANK.get(previous, 0):
            return incoming
        return previous or incoming

    def _upsert_row(self, event: dict, stamp: str | None = None, bucket: str | None = None) -> None:
        txn_id = str(event.get("transaction_id") or "")
        values = self._row_values(event, stamp)
        incoming = str(event.get("status") or "")
        section = bucket or self._section_for_type(values[6])
        tree = self._tree_for(section)
        store_key = self._item_key(section, txn_id) if txn_id else ""
        previous_status = ""
        if store_key and store_key in self.row_items:
            old_section, item = self.row_items[store_key]
            old_tree = self._tree_for(old_section)
            previous = list(old_tree.item(item, "values")) if old_tree.exists(item) else []
            if previous:
                previous_status = str(previous[17] if len(previous) > 17 else "")
                values = list(values)
                if values[0] == "" and previous[0]:
                    values[0] = previous[0]
                brand_idx = ALL_COLUMNS.index("brand")
                if (
                    len(previous) > brand_idx
                    and not str(values[brand_idx] if len(values) > brand_idx else "").strip()
                    and previous[brand_idx]
                ):
                    values[brand_idx] = previous[brand_idx]
                values = tuple(values)
            status = self._keep_status(previous_status, incoming, section)
            values = values[:17] + (status,) + values[18:]
            if old_tree.exists(item):
                old_tree.item(item, values=values, tags=(status,))
            else:
                item = tree.insert("", 0, values=values, tags=(status,))
            self.row_items[store_key] = (section, item)
        else:
            status = incoming
            item = tree.insert("", 0, values=values, tags=(status,))
            if store_key:
                self.row_items[store_key] = (section, item)
        row_date = self._row_date(event, values)
        rec = {
            "section": section,
            "type": values[6],
            "date": row_date,
            "values": values,
            "tags": (status,),
        }
        self.row_store[(section, item)] = rec
        self._mark_tally_dirty()
        if not self.bulk_loading:
            self._refresh_filter_options()
            if self._row_matches(rec, section):
                tree.reattach(item, "", 0)
            else:
                tree.detach(item)

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {message}\n")
        try:
            if int(float(self.log.index("end-1c"))) > LOG_LINE_LIMIT:
                self.log.delete("1.0", "200.0")
        except Exception:
            pass
        self.log.see("end")
        self.log.configure(state="disabled")

    def _apply_counts(self, counts: dict) -> None:
        self.stat_skipped.set(str(counts.get("skipped", 0)))
        self._mark_tally_dirty()

    def _refresh_counts(self) -> None:
        self._apply_counts(self.db.counts())

    def _event_from_db_row(self, row: dict) -> dict:
        event = {
            "transaction_id": row["transaction_id"],
            "status": str(row["copy_status"]).title(),
            "detail": row["detail"],
        }
        payload = row.get("payload_json")
        if payload:
            txn = _transaction_from_payload(payload)
            event = txn_row_event(
                txn,
                str(row["copy_status"]).title(),
                row["detail"] or "",
            )
        return event

    def _reload_workspace(self) -> None:
        self._reload_gen += 1
        gen = self._reload_gen
        self.bulk_loading = True
        self._clear_bucket("deposit")
        self._clear_bucket("withdraw")
        self._clear_bucket("sent")
        rows = list(reversed(self.db.all_records(limit=GUI_ROW_LIMIT)))
        self._reload_rows_chunked(rows, 0, gen)

    def _reload_rows_chunked(self, rows: list, index: int, gen: int) -> None:
        if gen != self._reload_gen:
            return
        end = min(index + EVENT_BATCH, len(rows))
        for row in rows[index:end]:
            event = self._event_from_db_row(row)
            self._upsert_row(event)
            if str(row["copy_status"]) in {"copied", "skipped"}:
                self._upsert_row(event, bucket="sent")
        if end < len(rows):
            self.root.after(
                15,
                lambda data=rows, nxt=end, token=gen: self._reload_rows_chunked(
                    data, nxt, token
                ),
            )
            return
        self.bulk_loading = False
        self._refresh_filter_options()
        self._apply_filters()

    def _load_recent_rows(self) -> None:
        self._reload_workspace()
        self._queue_sheet_unsent_check()

    def _on_close(self) -> None:
        self.auto_running = False
        self._live_stop.set()
        self._cancel_auto_timer()
        if self._tally_after_id is not None:
            try:
                self.root.after_cancel(self._tally_after_id)
            except Exception:
                pass
            self._tally_after_id = None
        self._stop_watcher()
        if self._scrape_jobs is not None:
            self._scrape_jobs.put(None)
        self._save_current_workspace_state()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    FinanceAutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
