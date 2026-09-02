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
    Settings,
    active_login_slot,
    google_sheet_url,
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
from src.mapper import record_local_datetime, sheet_tab_name
from src.sheets import SheetClient
from src.pipeline import (
    process_new_notifications_only,
    run_pipeline,
    sync_date_to_sheet,
    txn_row_event,
)
from src.tally import COMPLETED_STATUS, format_amount, local_today, parse_amount, txn_kind

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
        self.db = GatheringDB(self.settings.database_path)
        self.events: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.watcher = None
        self.row_items: dict[str, tuple[str, str]] = {}

        self.scrape_deposits = tk.BooleanVar(value=True)
        self.scrape_withdrawals = tk.BooleanVar(value=True)
        self.use_open_browser = tk.BooleanVar(value=False)
        self.headless = tk.BooleanVar(value=not self.settings.headed)
        self.capturing_latest = False
        self.bulk_loading = False
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
        self.google_sheet = tk.StringVar(value=google_sheet_url(self.settings.google_sheet_id))
        self.google_sheet_2 = tk.StringVar(value=google_sheet_url(self.settings.google_sheet_id_2))
        self.dark_mode = tk.BooleanVar(value=load_gui_theme() == "dark")
        self.auto_running = False
        self._auto_after_id: str | None = None
        self._auto_deadline = 0.0
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

        self._build_style()
        self._build_layout()
        self._apply_theme()
        self._refresh_counts()
        self._load_recent_rows()
        self._append_log("GUI ready. Open a section on the right. Run now only scrapes.")
        self._append_log("Send deposits or withdrawals from those sections after you tally the rows.")
        self._append_log(
            "Send and Sync write each date to the Google Sheet tab with that day number "
            "(29th transactions go to tab 29)."
        )
        self._bind_shortcuts()
        self.root.after(120, self._drain_events)
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
            text="Scrapes Completed only. Pick a date so the GUI count matches the website Record count, then send once — no duplicate sheet rows.",
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
        ttk.Entry(sidebar, textvariable=self.login_website, width=28).pack(fill="x", pady=(2, 6))
        ttk.Label(sidebar, text="Username", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_username, width=28).pack(fill="x", pady=(2, 6))
        ttk.Label(sidebar, text="Password", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_password, width=28).pack(fill="x", pady=(2, 6))
        ttk.Label(sidebar, text="2FA code", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_2fa, width=28).pack(fill="x", pady=(2, 10))

        ttk.Label(sidebar, text="Saved accounts (3)", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar,
            text="Each account stores website, username, password, and 2FA. Click an account, then Run now.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(2, 6))
        accounts_box = ttk.Frame(sidebar, style="LoginBox.TFrame", padding=8)
        accounts_box.pack(fill="x", pady=(0, 10))
        self.account_use_btns = []
        for slot in (1, 2, 3):
            row = ttk.Frame(accounts_box, style="LoginBox.TFrame")
            row.pack(fill="x", pady=2)
            use_btn = ttk.Button(
                row,
                style="Cred.TButton",
                command=lambda number=slot: self._select_account(number),
            )
            use_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
            self.account_use_btns.append(use_btn)
            ttk.Button(
                row,
                text="Save",
                style="Quick.TButton",
                command=lambda number=slot: self._save_account(number),
            ).pack(side="right")
        self._refresh_account_buttons()

        ttk.Label(sidebar, text="What to scrape", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Checkbutton(sidebar, text="Scrape deposits", variable=self.scrape_deposits).pack(anchor="w")
        ttk.Checkbutton(sidebar, text="Scrape withdrawals", variable=self.scrape_withdrawals).pack(anchor="w")
        ttk.Checkbutton(
            sidebar,
            text="Hide browser (only if 2FA is already saved)",
            variable=self.headless,
        ).pack(anchor="w", pady=(4, 10))

        ttk.Label(sidebar, text="Scrape", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Button(sidebar, text="Run now", style="Run.TButton", command=self._run_now).pack(fill="x", pady=3)
        ttk.Label(
            sidebar,
            text="Run now opens Completed, then clicks page 1 through the last page-link (for example #page-28).",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(8, 8))
        timer_row = ttk.Frame(sidebar, style="Card.TFrame")
        timer_row.pack(fill="x", pady=(0, 4))
        ttk.Label(timer_row, text="Timer (seconds)", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(
            timer_row,
            from_=5,
            to=3600,
            increment=5,
            textvariable=self.auto_interval,
            width=8,
        ).pack(side="right")
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
            text="Automated Run scrapes Completed on this timer and adds only new IDs to Latest scrape. Stop Automated Run ends the loop.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(4, 10))

        ttk.Label(sidebar, text="Google Sheets", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Label(
            sidebar,
            text="Paste a spreadsheet URL or ID. Share each sheet with the service account as Editor. Send writes the same rows to both if Sheet 2 is filled.",
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(sidebar, text="Sheet 1", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.google_sheet, width=28).pack(fill="x", pady=(2, 6))
        ttk.Label(sidebar, text="Sheet 2 (optional copy)", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.google_sheet_2, width=28).pack(fill="x", pady=(2, 6))
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
        ttk.Button(
            sheet_btns, text="Open sheet 1", style="Quick.TButton", command=lambda: self._open_google_sheet(1)
        ).pack(fill="x", pady=2)
        ttk.Button(
            sheet_btns, text="Open sheet 2", style="Quick.TButton", command=lambda: self._open_google_sheet(2)
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
        ttk.Label(filter_card, text="Tally date  ·  Completed only", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w"
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
        ttk.Label(filter_card, textvariable=self.filter_caption, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Label(filter_card, textvariable=self.match_caption, style="Tally.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        quick = ttk.Frame(filter_card, style="Card.TFrame")
        quick.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
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
        ttk.Button(
            controls,
            text="Send latest scrape to Google Sheet",
            command=self._send_latest_to_sheet,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Clear this scrape", command=self._clear_latest).pack(side="left")
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
        return self.worker is not None and self.worker.is_alive()

    def _account_host(self, website: str) -> str:
        url = normalize_dashboard_url(website)
        host = url.split("://", 1)[-1].split("/", 1)[0].split("#", 1)[0]
        return host or ""

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

    def _select_account(self, slot: int) -> None:
        account = self.login_accounts[slot - 1]
        if not account["username"] and not account["password"] and not account.get("website"):
            messagebox.showinfo(
                f"Account {slot} is empty",
                "Enter website, username, password, and 2FA above, then click Save on this account.",
            )
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
        host = self._account_host(account.get("website", ""))
        self._append_log(
            f"Using saved Account {slot} ({account['username'] or 'no username'}"
            + (f" on {host}" if host else "")
            + ")."
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
        self._append_log(
            f"Saved current login fields to Account {slot} ({username} on {self._account_host(website)})."
        )

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
        raw = self.google_sheet.get() if which == 1 else self.google_sheet_2.get()
        return normalize_google_sheet_id(raw)

    def _persist_google_sheets(self) -> tuple[str, str]:
        first = self._sheet_id_from_field(1)
        second = self._sheet_id_from_field(2)
        persist_env_values(
            {
                "GOOGLE_SHEET_ID": first,
                "GOOGLE_SHEET_ID_2": second,
            }
        )
        self.google_sheet.set(google_sheet_url(first) or first)
        self.google_sheet_2.set(google_sheet_url(second) or second)
        self.settings = Settings.load()
        return first, second

    def _save_google_sheets(self) -> None:
        first, second = self._persist_google_sheets()
        if first and second:
            self._append_log("Saved Google Sheet 1 and Sheet 2. Send will copy the same rows to both.")
        elif first:
            self._append_log("Saved Google Sheet 1. Leave Sheet 2 blank to write only there.")
        else:
            messagebox.showwarning(
                "Google Sheet required",
                "Paste the Google Sheet URL or ID into Sheet 1.",
            )

    def _open_google_sheet(self, which: int = 1) -> None:
        sheet_id = self._sheet_id_from_field(which) or (
            self.settings.google_sheet_id if which == 1 else self.settings.google_sheet_id_2
        )
        if not sheet_id:
            messagebox.showinfo(
                "Google Sheet not set",
                "Paste a Google Sheet URL into Sheet 1 or Sheet 2, then click Save sheets.",
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
        first = self._sheet_id_from_field(1)
        second = self._sheet_id_from_field(2)
        if first:
            settings.google_sheet_id = first
        if second:
            settings.google_sheet_id_2 = second
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

    def _start_automated_run(self) -> None:
        if self.auto_running:
            messagebox.showinfo("Already running", "Automated Run is already active.")
            return
        if not self._scrape_ready("Automated Run"):
            return
        if self._busy():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        self._persist_login_fields()
        self.poll_interval.set(self._auto_interval_seconds())
        self.auto_running = True
        self.capturing_latest = True
        self.arm_watcher_after_run = False
        self.pages.select(0)
        seconds = self._auto_interval_seconds()
        self.status_text.set(f"Automated run every {seconds}s")
        self._append_log(
            f"Automated Run started. Scraping Completed every {seconds}s. "
            "Already scraped IDs are skipped in Latest scrape. "
            "The Google Sheet is not updated until you send."
        )
        try:
            started = self._start_job(scrape=True, write_sheet=False, quiet=True)
            if not started:
                self._schedule_next_auto()
        except Exception as exc:
            self._append_log(f"Automated Run failed: {exc}")
            self._schedule_next_auto()

    def _stop_automated_run(self) -> None:
        if not self.auto_running and self._auto_after_id is None:
            self._append_log("Automated Run is not active.")
            return
        self.auto_running = False
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
        self._append_log(
            f"Automated Run tick: scraping Completed for {self._scrape_date()} "
            f"on {normalize_dashboard_url(self.login_website.get())}."
        )
        try:
            started = self._start_job(scrape=True, write_sheet=False, quiet=True)
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
            if txn_id in self.sheet_id_cache:
                return True
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
                self.events.put(
                    {"kind": "log", "message": f"Could not compare Google Sheet IDs: {exc}"}
                )

        threading.Thread(target=work, name="sheet-unsent", daemon=True).start()

    def _run_now(self) -> None:
        if not self._scrape_ready("Run now"):
            return
        self._persist_login_fields()
        self._clear_bucket("latest")
        self.latest_run_ids = set()
        self.capturing_latest = True
        self.arm_watcher_after_run = False
        self.pages.select(0)
        try:
            self._start_job(scrape=True, write_sheet=False)
        except Exception as exc:
            self.capturing_latest = False
            messagebox.showerror("Run now failed", str(exc))
            self._append_log(f"Run now failed: {exc}")

    def _send_latest_to_sheet(self) -> None:
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
        self._append_log(
            f"Syncing GUI records for {day} to Google Sheet tab {tab}."
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
        self._append_log(f"Sending {len(ids)} {label} to Google Sheets.")

        def work() -> None:
            try:
                self.db.reset_to_pending(ids)
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

    def _start_job(self, scrape: bool, write_sheet: bool = False, quiet: bool = False) -> bool:
        if self._busy():
            if not quiet:
                messagebox.showinfo("Busy", "A run is already in progress.")
            return False
        self.status_text.set("Running...")
        settings = self._current_settings()
        self._append_log(
            f"Opening {settings.dashboard_url} as {settings.dashboard_username} "
            f"and gathering Completed transactions for {settings.filter_date_from}. "
            "The sheet is not updated yet."
        )
        if settings.dashboard_2fa:
            self._append_log("Using the 2FA code from the Website login section.")
        else:
            self._append_log("No 2FA code entered. If the site asks, type it in the browser window.")

        def work() -> None:
            try:
                run_pipeline(
                    settings,
                    on_event=self.events.put,
                    scrape=scrape,
                    write_sheet=write_sheet,
                )
            except Exception as exc:
                self.events.put({"kind": "log", "message": f"Run failed: {exc}"})
                self.events.put({"kind": "done", "message": str(exc)})

        self.worker = threading.Thread(target=work, name="automation-run", daemon=True)
        self.worker.start()
        return True

    def _stop_watcher(self) -> None:
        if self.watcher:
            try:
                self.watcher.stop()
            except Exception:
                pass
            self.watcher = None

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
            except Exception as exc:
                self._append_log(f"Event handler error: {exc}")
        if not self._busy() and self.status_text.get() in {
            "Running...",
            "Sending to Google Sheet...",
            "Syncing Google Sheet...",
        }:
            if self.auto_running:
                seconds = self._auto_interval_seconds()
                self.status_text.set(f"Automated run: next scrape in {seconds}s")
            else:
                self.status_text.set("Idle")
        if self.auto_running and self._auto_after_id is None and not self._busy():
            self._schedule_next_auto()
        self.root.after(120, self._drain_events)

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
            self._refresh_filter_options()
            self._apply_filters()
        if kind == "sheet_tally":
            self.sheet_date_count = int(event.get("sheet_count") or 0)
            self.sheet_tally_date = str(event.get("date") or "")
            self._update_match_caption()
        if kind == "sheet_ids":
            self.sheet_id_cache = {str(item) for item in (event.get("ids") or []) if item}
            self.sheet_id_cache_date = str(event.get("date") or "")
            self.sheet_date_count = int(event.get("count") or len(self.sheet_id_cache))
            if self.sheet_id_cache_date:
                self.sheet_tally_date = self.sheet_id_cache_date
            self._refresh_unsent_latest()
        if kind == "done":
            self._prefer_sent_tab = self.open_sent_after_send
            if self.auto_running:
                self.capturing_latest = True
                self._schedule_next_auto()
                try:
                    self._refresh_counts()
                except Exception as exc:
                    self._append_log(f"Could not refresh counts: {exc}")
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
        dated = self._dated(self._section_records("deposit")) + self._dated(
            self._section_records("withdraw")
        )
        gui_ids = self._record_ids(dated)
        sent_ids = self._record_ids(self._dated(self._section_records("sent"), "sent"))
        latest_ids = self._record_ids(self._section_records("latest", visible_only=True))
        date_sel = self.date_filter.get() or "All dates"
        website = self.website_records
        website_bit = (
            f"Website Completed Record: {website}"
            + (f" · Total {self.website_total}" if self.website_total else "")
            if website
            else "Website Completed Record: —"
        )
        compare_date = self.website_date or date_sel
        scraped = len(gui_ids)
        sent = len(sent_ids)
        scrape_match = bool(website) and website == scraped
        sheet_match = bool(website) and website == sent and scraped == sent
        if website and scrape_match and sheet_match:
            tally = "ALL MATCH"
        elif website and scrape_match:
            tally = "scrape matches Completed · sheet not sent in full"
        else:
            tally = "not matched yet"
        sheet_bit = ""
        if self.sheet_date_count and (not self.sheet_tally_date or self.sheet_tally_date in {compare_date, date_sel, self.sent_date_filter.get()}):
            sheet_live = (
                "match"
                if website and self.sheet_date_count == website
                else "live count"
            )
            sheet_bit = f"  ·  Google Sheet live: {self.sheet_date_count} txn ({sheet_live})"
        self.match_caption.set(
            f"{website_bit}  ·  Scraped {compare_date}: {scraped} txn  ·  "
            f"Latest scrape: {len(latest_ids)}  ·  "
            f"Google Sheet sent: {sent} txn  ·  {tally}"
            f"{sheet_bit}"
        )

    def _update_filter_caption(self) -> None:
        date_sel = self.date_filter.get() or "All dates"
        date_label = "all dates" if date_sel == "All dates" else date_sel
        latest = self._section_records("latest", visible_only=True)
        deposits = self._section_records("deposit", visible_only=True)
        withdrawals = self._section_records("withdraw", visible_only=True)
        sent = self._section_records("sent", visible_only=True)
        deposit_all = self._section_records("deposit")
        withdraw_all = self._section_records("withdraw")
        gui_ids = self._record_ids(
            self._dated(deposit_all) + self._dated(withdraw_all)
        )
        self.filter_caption.set(
            f"Completed · {date_label}  ·  "
            f"{len(gui_ids)} unique txn  ·  "
            f"Deposits {self._tally_text(self._dated(deposit_all))}  ·  "
            f"Withdrawals {self._tally_text(self._dated(withdraw_all))}"
        )
        self.latest_title.set(
            f"Latest scrape  ·  Extracted Transactions: {len(gui_ids)} on {date_label}"
        )
        self.deposit_title.set(f"Deposits  ·  {len(deposits)} visible")
        self.withdraw_title.set(f"Withdrawals  ·  {len(withdrawals)} visible")
        sent_date = self.sent_date_filter.get() or date_label
        sent_ids = self._record_ids(self._dated(self._section_records("sent"), "sent"))
        self.sent_title.set(
            f"Google Sheet sent data  ·  Sent Count to Google Sheets: {len(sent_ids)} on {sent_date}"
        )
        self.stat_extracted.set(str(len(gui_ids)))
        self.stat_copied.set(str(len(sent_ids)))
        pending_ids = self._record_ids(
            [
                rec
                for rec in self._dated(deposit_all) + self._dated(withdraw_all)
                if str((rec.get("tags") or ("",))[0])
                in {"Pending", "Gathered", "Preview", "Copying"}
            ]
        )
        failed_ids = self._record_ids(
            [
                rec
                for rec in self._dated(deposit_all) + self._dated(withdraw_all)
                if str((rec.get("tags") or ("",))[0]) == "Failed"
            ]
        )
        self.stat_pending.set(str(len(pending_ids)))
        self.stat_failed.set(str(len(failed_ids)))
        self.latest_tally.set(self._section_tally_line("latest", latest))
        self.deposit_tally.set(self._money_tally_line("deposit"))
        self.withdraw_tally.set(self._money_tally_line("withdraw"))
        self.sent_tally.set(self._sent_tally_line(sent))
        self._update_match_caption()

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
        self._update_filter_caption()

    def _apply_filters(self) -> None:
        for key, rec in self.row_store.items():
            section, item = key
            tree = self._tree_for(section)
            if not tree.exists(item):
                continue
            if self._row_matches(rec, section):
                tree.reattach(item, "", 0)
            else:
                tree.detach(item)
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
        if not self.bulk_loading:
            self._refresh_filter_options()
            if self._row_matches(rec, section):
                tree.reattach(item, "", 0)
            else:
                tree.detach(item)
            self._update_filter_caption()

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _apply_counts(self, counts: dict) -> None:
        self.stat_skipped.set(str(counts.get("skipped", 0)))
        self._update_filter_caption()

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
        self.bulk_loading = True
        try:
            self._clear_bucket("deposit")
            self._clear_bucket("withdraw")
            self._clear_bucket("sent")
            for row in reversed(self.db.all_records()):
                event = self._event_from_db_row(row)
                self._upsert_row(event)
                if str(row["copy_status"]) in {"copied", "skipped"}:
                    self._upsert_row(event, bucket="sent")
        finally:
            self.bulk_loading = False
        self._refresh_filter_options()
        self._apply_filters()

    def _load_recent_rows(self) -> None:
        self._reload_workspace()
        self._queue_sheet_unsent_check()

    def _on_close(self) -> None:
        self.auto_running = False
        self._cancel_auto_timer()
        self._stop_watcher()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    FinanceAutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
