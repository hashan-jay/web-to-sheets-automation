from __future__ import annotations

import ctypes
import queue
import re
import threading
import tkinter as tk
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
    "bank": ("Bank", 70),
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
TREE_TAGS = (
    ("Copied", "#15803d"),
    ("Preview", "#1d4ed8"),
    ("Gathered", "#0f766e"),
    ("Pending", "#0f766e"),
    ("Copying", "#a16207"),
    ("Failed", "#b91c1c"),
    ("Skipped", "#6b7280"),
)
STATUS_RANK = {
    "Failed": 1,
    "Gathered": 2,
    "Pending": 2,
    "Preview": 2,
    "Copying": 3,
    "Skipped": 4,
    "Copied": 4,
}

from src.config import Settings, persist_env_values
from src.database import GatheringDB, _transaction_from_payload
from src.mapper import record_local_datetime
from src.pipeline import process_new_notifications_only, run_pipeline, txn_row_event
from src.tally import COMPLETED_STATUS, format_amount, local_today, parse_amount, txn_kind
from src.watcher import NotificationWatcher

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
        self.watcher: NotificationWatcher | None = None
        self.row_items: dict[str, tuple[str, str]] = {}

        self.scrape_deposits = tk.BooleanVar(value=True)
        self.scrape_withdrawals = tk.BooleanVar(value=True)
        self.use_open_browser = tk.BooleanVar(value=self.settings.use_open_browser)
        self.headless = tk.BooleanVar(value=not self.settings.headed)
        self.capturing_latest = False
        self.bulk_loading = False
        self.latest_run_ids: set[str] = set()
        self.login_username = tk.StringVar(value=self.settings.dashboard_username)
        self.login_password = tk.StringVar(value=self.settings.dashboard_password)
        self.login_2fa = tk.StringVar(value=self.settings.dashboard_2fa)
        self.saved_username = self.settings.dashboard_username
        self.saved_password = self.settings.dashboard_password
        self.saved_2fa = self.settings.dashboard_2fa
        self.arm_watcher_after_run = False
        self.open_sent_after_send = False
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
        self.deposit_extended = False
        self.extend_btn_text = tk.StringVar(value="Show hidden details")
        self.poll_interval = tk.IntVar(value=self.settings.poll_interval_seconds)
        self.status_text = tk.StringVar(value="Idle")
        self.watch_text = tk.StringVar(value="Watcher: Off")
        self.stat_pending = tk.StringVar(value="0")
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
        self._refresh_counts()
        self._load_recent_rows()
        self._append_log("GUI ready. Open a section on the right. Run now only scrapes.")
        self._append_log("Send deposits or withdrawals from those sections after you tally the rows.")
        self.root.after(120, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        self.root.configure(bg="#eef2f7")
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Root.TFrame", background="#eef2f7")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Header.TFrame", background="#0f2744")
        style.configure("Header.TLabel", background="#0f2744", foreground="#ffffff", font=("Segoe UI", 16, "bold"))
        style.configure("HeaderSub.TLabel", background="#0f2744", foreground="#c5d4e8", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background="#ffffff", foreground="#0f2744", font=("Segoe UI", 11, "bold"))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#5b6b7c", font=("Segoe UI", 9))
        style.configure("Stat.TLabel", background="#ffffff", foreground="#0f2744", font=("Segoe UI", 20, "bold"))
        style.configure("Tally.TLabel", background="#f4f7fb", foreground="#0f2744", font=("Segoe UI", 10, "bold"))
        style.configure("TallyBox.TFrame", background="#f4f7fb")
        style.configure("Run.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Cred.TButton", font=("Consolas", 9), padding=5, background="#f4f7fb")
        style.configure("LoginBox.TFrame", background="#f4f7fb")
        style.configure("TNotebook", background="#eef2f7", borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=(18, 10))
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=26)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_layout(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(20, 14))
        header.pack(fill="x")
        ttk.Label(header, text="Finance Automation", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Scrapes Completed only. Pick a date so the GUI count matches the website Record count, then send once — no duplicate sheet rows.",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(self.root, style="Root.TFrame", padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_workspace(body)

    def _build_sidebar(self, body: ttk.Frame) -> None:
        side_wrap = ttk.Frame(body, style="Card.TFrame")
        side_wrap.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        side_canvas = tk.Canvas(side_wrap, bg="#ffffff", highlightthickness=0, width=310, height=780)
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
        ttk.Label(sidebar, text="https://skgaming16.as6868.com/#login", style="Muted.TLabel").pack(
            anchor="w", pady=(2, 8)
        )
        ttk.Label(sidebar, textvariable=self.status_text, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        ttk.Label(sidebar, text="Saved details — click to fill", style="Muted.TLabel").pack(anchor="w")
        saved = ttk.Frame(sidebar, style="LoginBox.TFrame", padding=8)
        saved.pack(fill="x", pady=(4, 10))
        self.saved_user_btn = ttk.Button(
            saved, style="Cred.TButton", command=lambda: self._fill_login_field("username")
        )
        self.saved_user_btn.pack(fill="x", pady=2)
        self.saved_pass_btn = ttk.Button(
            saved, style="Cred.TButton", command=lambda: self._fill_login_field("password")
        )
        self.saved_pass_btn.pack(fill="x", pady=2)
        self.saved_2fa_btn = ttk.Button(
            saved, style="Cred.TButton", command=lambda: self._fill_login_field("2fa")
        )
        self.saved_2fa_btn.pack(fill="x", pady=2)
        ttk.Button(saved, text="Use these credentials", command=self._fill_all_saved_login).pack(
            fill="x", pady=(6, 0)
        )
        self._refresh_saved_login_buttons()

        ttk.Label(sidebar, text="Username", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_username, width=28).pack(fill="x", pady=(2, 6))
        ttk.Label(sidebar, text="Password", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_password, width=28).pack(fill="x", pady=(2, 6))
        ttk.Label(sidebar, text="2FA code", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.login_2fa, width=28).pack(fill="x", pady=(2, 10))

        ttk.Label(sidebar, text="What to scrape", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Checkbutton(sidebar, text="Scrape deposits", variable=self.scrape_deposits).pack(anchor="w")
        ttk.Checkbutton(sidebar, text="Scrape withdrawals", variable=self.scrape_withdrawals).pack(anchor="w")
        ttk.Checkbutton(
            sidebar,
            text="Use the website already open in Chrome/Brave",
            variable=self.use_open_browser,
        ).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(
            sidebar,
            text="Hide browser (only if 2FA is already saved)",
            variable=self.headless,
        ).pack(anchor="w", pady=(4, 10))

        ttk.Label(sidebar, text="Scrape", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Button(sidebar, text="Run now", style="Run.TButton", command=self._run_now).pack(fill="x", pady=3)
        ttk.Button(sidebar, text="Start watcher", command=self._start_watcher).pack(fill="x", pady=3)
        ttk.Button(sidebar, text="Stop watcher", command=self._stop_watcher).pack(fill="x", pady=3)
        ttk.Label(sidebar, textvariable=self.watch_text, style="Muted.TLabel").pack(anchor="w", pady=(8, 10))

        interval = ttk.Frame(sidebar, style="Card.TFrame")
        interval.pack(fill="x", pady=(0, 14))
        ttk.Label(interval, text="Dashboard poll (seconds)", style="Muted.TLabel").pack(anchor="w")
        ttk.Spinbox(interval, from_=5, to=3600, textvariable=self.poll_interval, width=10).pack(
            anchor="w", pady=(4, 0)
        )

        ttk.Label(sidebar, text="Gathering database", style="CardTitle.TLabel").pack(anchor="w", pady=(8, 8))
        stats = ttk.Frame(sidebar, style="Card.TFrame")
        stats.pack(fill="x")
        self._stat_block(stats, "To send", self.stat_pending).grid(row=0, column=0, padx=(0, 12))
        self._stat_block(stats, "Sent", self.stat_copied).grid(row=0, column=1, padx=(0, 12))
        self._stat_block(stats, "Failed", self.stat_failed).grid(row=1, column=0, padx=(0, 12), pady=(10, 0))
        self._stat_block(stats, "Already on sheet", self.stat_skipped).grid(
            row=1, column=1, padx=(0, 12), pady=(10, 0)
        )
        ttk.Label(
            sidebar,
            text="Run now scrapes Completed for the selected tally date. Send from Latest scrape, Deposits, or Withdrawals. Duplicate IDs are skipped.",
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
            bg="#0f2744",
            fg="#e6eef8",
            insertbackground="#e6eef8",
            relief="flat",
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
            text="This run only. Send writes these rows to Google Sheets and adds them to Google Sheet sent.",
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
        ttk.Button(controls, text="Refresh sent list", command=self._reload_workspace).pack(side="left")
        ttk.Label(
            page,
            text="Already written to Google Sheets. Use this list to tally sent deposits and withdrawals.",
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
        self._append_log("Cleared the latest scrape table. Saved deposits and withdrawals are unchanged.")

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

    def _refresh_saved_login_buttons(self) -> None:
        user = self.saved_username or "(none)"
        password = self.saved_password or "(none)"
        twofa = self.saved_2fa or "(none)"
        self.saved_user_btn.configure(text=f"Username    {user}")
        self.saved_pass_btn.configure(text=f"Password    {password}")
        self.saved_2fa_btn.configure(text=f"2FA         {twofa}")

    def _fill_login_field(self, kind: str) -> None:
        if kind == "username":
            self.login_username.set(self.saved_username)
        elif kind == "password":
            self.login_password.set(self.saved_password)
        elif kind == "2fa":
            self.login_2fa.set(self.saved_2fa)
        self._append_log(f"Filled {kind} from saved login details.")

    def _fill_all_saved_login(self) -> None:
        self.login_username.set(self.saved_username)
        self.login_password.set(self.saved_password)
        self.login_2fa.set(self.saved_2fa)
        self._append_log("Filled username, password, and 2FA from saved login details.")

    def _persist_login_fields(self) -> None:
        username = self.login_username.get().strip()
        password = self.login_password.get()
        twofa = self.login_2fa.get().strip()
        persist_env_values(
            {
                "DASHBOARD_USERNAME": username,
                "DASHBOARD_PASSWORD": password,
                "DASHBOARD_2FA": twofa,
            }
        )
        self.saved_username = username
        self.saved_password = password
        self.saved_2fa = twofa
        self._refresh_saved_login_buttons()

    def _scrape_date(self) -> str:
        selected = self.date_filter.get().strip()
        if selected in {"", "All dates", "(blank)"}:
            return local_today()
        return selected

    def _select_today(self) -> None:
        self.date_filter.set(local_today())
        self._refresh_filter_options()
        self._apply_filters()
        self._append_log(f"Date filter set to today ({local_today()}).")

    def _current_settings(self) -> Settings:
        settings = Settings.load()
        settings.headed = not self.headless.get()
        settings.use_open_browser = self.use_open_browser.get()
        settings.poll_interval_seconds = int(self.poll_interval.get() or 60)
        settings.dashboard_username = self.login_username.get().strip()
        settings.dashboard_password = self.login_password.get()
        code = "".join(ch for ch in self.login_2fa.get() if ch.isdigit())
        settings.dashboard_2fa = code[:6] if code else self.login_2fa.get().strip()
        day = self._scrape_date()
        settings.filter_date_from = day
        settings.filter_date_to = day
        settings.filter_status = COMPLETED_STATUS
        return settings

    def _run_now(self) -> None:
        if not self.login_username.get().strip() or not self.login_password.get():
            messagebox.showwarning(
                "Login required",
                "Enter username and password in the Website login section, "
                "or click the saved details to fill them.",
            )
            return
        if not self.scrape_deposits.get() and not self.scrape_withdrawals.get():
            messagebox.showwarning(
                "Nothing selected",
                "Check Scrape deposits and/or Scrape withdrawals before Run now.",
            )
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
                "Run now first. Then send this latest scrape to Google Sheets.",
            )
            return
        self._start_sheet_send(ids, "latest scrape")

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

    def _start_job(self, scrape: bool, write_sheet: bool = False) -> None:
        if self._busy():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        self.status_text.set("Running...")
        settings = self._current_settings()
        self._append_log(
            f"Logging in as {settings.dashboard_username} "
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

    def _start_watcher(self) -> None:
        if self.watcher and self.watcher.running:
            return
        settings = self._current_settings()
        self.watcher = NotificationWatcher(
            settings,
            self.events.put,
            dry_run=True,
            poll_interval=settings.poll_interval_seconds,
        )
        self.watcher.start()
        self.watch_text.set("Watcher: On — waiting for notifications")
        self._append_log("Watcher is on. New rows appear in Latest scrape, Deposits, and Withdrawals.")

    def _stop_watcher(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self.watch_text.set("Watcher: Off")

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        if not self._busy() and self.status_text.get() in {"Running...", "Sending to Google Sheet..."}:
            self.status_text.set("Idle")
        self.root.after(120, self._drain_events)

    def _handle_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "row":
            status = str(event.get("status") or "")
            txn_id = str(event.get("transaction_id") or "")
            if self.capturing_latest and status == "Gathered" and self._type_wanted(event.get("type")):
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
            self._refresh_filter_options()
            self._apply_filters()
        if kind == "done":
            self.status_text.set("Idle")
            self.capturing_latest = False
            self._refresh_counts()
            self._reload_workspace()
            if self.open_sent_after_send:
                self.open_sent_after_send = False
                self.pages.select(3)
            if self.arm_watcher_after_run:
                self.arm_watcher_after_run = False
                message = str(event.get("message") or "").lower()
                failed = (
                    "run failed" in message
                    or "invalid login" in message
                    or "login was not" in message
                    or "rejected this login" in message
                )
                if failed:
                    self._append_log("Watcher not started because login or scrape failed.")
                else:
                    self._start_watcher()

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
        when = record_local_datetime(
            event.get("datetime"),
            event.get("created"),
            event.get("processed"),
            values[0] if values else "",
        )
        return self._extract_date(when)

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

    def _date_matches(self, raw: str) -> bool:
        selected = self.date_filter.get()
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
            self._date_matches(str(rec.get("date") or ""))
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
        sent_ids = self._record_ids(self._dated(self._section_records("sent")))
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
        match = "match" if website and website == len(gui_ids) else "not matched yet"
        sent_match = "match" if website and website == len(sent_ids) else "not sent in full"
        self.match_caption.set(
            f"{website_bit}  ·  GUI {compare_date}: {len(gui_ids)} txn ({match})  ·  "
            f"Latest scrape: {len(latest_ids)}  ·  "
            f"Google Sheet sent: {len(sent_ids)} txn ({sent_match})"
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
        self.latest_title.set(f"Latest scrape  ·  {len(latest)} row(s) on {date_label}")
        self.deposit_title.set(f"Deposits  ·  {len(deposits)} visible")
        self.withdraw_title.set(f"Withdrawals  ·  {len(withdrawals)} visible")
        self.sent_title.set(f"Google Sheet sent data  ·  {len(sent)} visible on {date_label}")
        self.latest_tally.set(self._section_tally_line("latest", latest))
        self.deposit_tally.set(self._money_tally_line("deposit"))
        self.withdraw_tally.set(self._money_tally_line("withdraw"))
        self.sent_tally.set(self._sent_tally_line(sent))
        self._update_match_caption()

    def _dated(self, recs: list[dict]) -> list[dict]:
        return [rec for rec in recs if self._date_matches(str(rec.get("date") or ""))]

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
        dated = self._dated(self._section_records("sent"))
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
        self.date_combo.configure(values=date_options)
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
                if values[0] == "" and previous[0]:
                    values = (previous[0],) + values[1:]
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
        self.stat_pending.set(str(counts.get("pending", 0)))
        self.stat_copied.set(str(counts.get("copied", 0)))
        self.stat_failed.set(str(counts.get("failed", 0)))
        self.stat_skipped.set(str(counts.get("skipped", 0)))

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

    def _on_close(self) -> None:
        self._stop_watcher()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    FinanceAutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
