from __future__ import annotations

import importlib.util
import queue
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import customtkinter as ctk
except ImportError as error:
    raise SystemExit(
        "The GUI dependency is missing. Run: pip install -r requirements.txt"
    ) from error


ROOT = Path(__file__).resolve().parent


def load_cli_module():
    spec = importlib.util.spec_from_file_location("live_torrent_cli", ROOT / "main-cli.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load main-cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = load_cli_module()


class LiveTorrentApp(ctk.CTk):
    COLORS = {
        "bg": "#0b1120",
        "panel": "#111a2e",
        "card": "#18233a",
        "accent": "#36c5a3",
        "accent_hover": "#2daf91",
        "blue": "#579dff",
        "text": "#f3f6fb",
        "muted": "#9ba9bf",
        "danger": "#ef6673",
    }

    def __init__(self) -> None:
        ctk.set_widget_scaling(1.15)
        super().__init__(fg_color=self.COLORS["bg"])
        self.title("Live Torrent Client")
        self.geometry("1240x800")
        self.minsize(1040, 680)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        self.events: queue.Queue = queue.Queue()
        self.aria = None
        self.torrent_gid: str | None = None
        self.source_uri = ""
        self.files: list[dict] = []
        self.results: list = []
        self.stop_download = threading.Event()
        self.busy = False
        self.output_dir = ctk.StringVar(value=str((ROOT / "downloads").resolve()))
        self.status_text = ctk.StringVar(value="Ready")
        self.detail_text = ctk.StringVar(value="Paste a magnet link or search the web")
        self.seeders_text = ctk.StringVar(value="—")
        self.speed_text = ctk.StringVar(value="0 B/s")
        self.progress_text = ctk.StringVar(value="0.00%")

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self._configure_tree_style()
        self._build_layout()
        self.after(100, self._process_events)

    def _configure_tree_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Torrent.Treeview",
            background=self.COLORS["panel"],
            foreground=self.COLORS["text"],
            fieldbackground=self.COLORS["panel"],
            borderwidth=0,
            rowheight=38,
            font=("Segoe UI", 12),
        )
        style.configure(
            "Torrent.Treeview.Heading",
            background=self.COLORS["card"],
            foreground=self.COLORS["muted"],
            borderwidth=0,
            font=("Segoe UI Semibold", 11),
        )
        style.map("Torrent.Treeview", background=[("selected", "#235b73")])

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)

        self._build_header()
        self.pages = {}
        self._build_search_page()
        self._build_explorer_page()
        self._build_download_page()
        self._build_settings_page()
        self.show_page("search")

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color="#0e1729")
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="◉  LIVE TORRENT", font=("Segoe UI Semibold", 19),
            text_color=self.COLORS["accent"],
        ).pack(anchor="w", padx=24, pady=(30, 42))

        self.nav_buttons = {}
        for key, icon, label in (
            ("search", "⌕", "Search"),
            ("explorer", "▦", "Torrent Explorer"),
            ("download", "↓", "Transfer"),
            ("settings", "⚙", "Settings"),
        ):
            button = ctk.CTkButton(
                sidebar, text=f"{icon}   {label}", anchor="w", height=44,
                corner_radius=9, fg_color="transparent",
                hover_color=self.COLORS["card"], font=("Segoe UI", 13),
                command=lambda page=key: self.show_page(page),
            )
            button.pack(fill="x", padx=14, pady=4)
            self.nav_buttons[key] = button

        ctk.CTkLabel(
            sidebar, text="Powered by aria2 + VLC", font=("Segoe UI", 10),
            text_color=self.COLORS["muted"],
        ).pack(side="bottom", pady=22)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.content, fg_color="transparent", height=64)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(
            header, text="Search", font=("Segoe UI Semibold", 26),
            text_color=self.COLORS["text"],
        )
        self.page_title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, textvariable=self.status_text, font=("Segoe UI", 11),
            text_color=self.COLORS["accent"], fg_color=self.COLORS["card"],
            corner_radius=12, padx=14, pady=7,
        ).grid(row=0, column=1, sticky="e")

    def _page(self, name: str) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid(row=1, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.pages[name] = page
        return page

    def _card(self, parent, **grid) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=self.COLORS["panel"], corner_radius=14)
        card.grid(**grid)
        return card

    def _build_search_page(self) -> None:
        page = self._page("search")
        top = self._card(page, row=0, column=0, sticky="ew", pady=(0, 14))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Search across all available engines", font=("Segoe UI Semibold", 16)).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 8))
        self.search_entry = ctk.CTkEntry(top, height=44, placeholder_text="Movie, Linux distribution, documentary…")
        self.search_entry.grid(row=1, column=0, sticky="ew", padx=(20, 10), pady=(0, 20))
        self.search_entry.bind("<Return>", lambda _event: self.start_search())
        self.search_button = self._primary_button(top, "⌕  Search", self.start_search)
        self.search_button.grid(row=1, column=1, padx=(0, 20), pady=(0, 20))

        table_card = self._card(page, row=1, column=0, sticky="nsew")
        table_card.grid_columnconfigure(0, weight=1)
        table_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(table_card, text="Results", font=("Segoe UI Semibold", 15)).grid(row=0, column=0, sticky="w", padx=18, pady=14)
        self.result_count = ctk.CTkLabel(table_card, text="0 items", text_color=self.COLORS["muted"])
        self.result_count.grid(row=0, column=1, sticky="e", padx=18)
        self.results_tree = self._tree(table_card, ("title", "size", "seeders", "leechers", "source"))
        for column, title, width, anchor in (
            ("title", "Name", 500, "w"), ("size", "Size", 95, "e"),
            ("seeders", "Seeds", 70, "center"), ("leechers", "Peers", 70, "center"),
            ("source", "Source", 120, "w"),
        ):
            self.results_tree.heading(column, text=title)
            self.results_tree.column(column, width=width, anchor=anchor, stretch=column == "title")
        self.results_tree.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=18)
        self.results_tree.bind("<Double-1>", lambda _event: self.open_result())
        self._primary_button(table_card, "Open in Explorer  →", self.open_result).grid(row=2, column=1, sticky="e", padx=18, pady=14)

    def _build_explorer_page(self) -> None:
        page = self._page("explorer")
        top = self._card(page, row=0, column=0, sticky="ew", pady=(0, 14))
        top.grid_columnconfigure(0, weight=1)
        self.magnet_entry = ctk.CTkEntry(top, height=44, placeholder_text="Paste a magnet link or .torrent URL")
        self.magnet_entry.grid(row=0, column=0, sticky="ew", padx=(20, 10), pady=20)
        self.fetch_button = self._primary_button(top, "▦  Explore", self.start_metadata)
        self.fetch_button.grid(row=0, column=1, padx=(0, 20), pady=20)

        card = self._card(page, row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)
        self.torrent_name = ctk.CTkLabel(card, text="No torrent loaded", font=("Segoe UI Semibold", 17))
        self.torrent_name.grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        self.torrent_meta = ctk.CTkLabel(card, text="Files and availability will appear here", text_color=self.COLORS["muted"])
        self.torrent_meta.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 12))
        self.files_tree = self._tree(card, ("number", "path", "size"), selectmode="extended")
        for column, title, width, anchor in (("number", "#", 50, "center"), ("path", "File", 650, "w"), ("size", "Size", 110, "e")):
            self.files_tree.heading(column, text=title)
            self.files_tree.column(column, width=width, anchor=anchor, stretch=column == "path")
        self.files_tree.grid(row=2, column=0, columnspan=4, sticky="nsew", padx=18)
        ctk.CTkButton(card, text="Select all", fg_color="transparent", hover_color=self.COLORS["card"], command=self.select_all_files).grid(row=3, column=0, sticky="w", padx=18, pady=14)
        ctk.CTkButton(card, text="Clear", fg_color="transparent", hover_color=self.COLORS["card"], command=lambda: self.files_tree.selection_remove(self.files_tree.selection())).grid(row=3, column=1, sticky="w", pady=14)
        self._primary_button(card, "↓  Download selected", lambda: self.start_download(False)).grid(row=3, column=2, sticky="e", padx=8, pady=14)
        self.stream_button = self._primary_button(card, "▶  Stream with VLC", lambda: self.start_download(True), color=self.COLORS["blue"])
        self.stream_button.grid(row=3, column=3, sticky="e", padx=(0, 18), pady=14)

    def _build_download_page(self) -> None:
        page = self._page("download")
        card = self._card(page, row=0, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, textvariable=self.detail_text, font=("Segoe UI Semibold", 18)).grid(row=0, column=0, columnspan=3, sticky="w", padx=22, pady=(22, 6))
        self.progress_bar = ctk.CTkProgressBar(card, height=12, progress_color=self.COLORS["accent"])
        self.progress_bar.set(0)
        self.progress_bar.grid(row=1, column=0, columnspan=3, sticky="ew", padx=22, pady=14)
        for column, label, variable in ((0, "Progress", self.progress_text), (1, "Speed", self.speed_text), (2, "Seeders", self.seeders_text)):
            box = ctk.CTkFrame(card, fg_color=self.COLORS["card"], corner_radius=10)
            box.grid(row=2, column=column, sticky="ew", padx=(22 if column == 0 else 6, 22 if column == 2 else 6), pady=(4, 22))
            card.grid_columnconfigure(column, weight=1)
            ctk.CTkLabel(box, text=label, text_color=self.COLORS["muted"], font=("Segoe UI", 10)).pack(pady=(10, 0))
            ctk.CTkLabel(box, textvariable=variable, font=("Segoe UI Semibold", 16)).pack(pady=(0, 10))

        log_card = self._card(page, row=1, column=0, sticky="nsew")
        page.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(log_card, text="Activity", font=("Segoe UI Semibold", 15)).grid(row=0, column=0, sticky="w", padx=18, pady=14)
        self.log_box = ctk.CTkTextbox(log_card, fg_color="#0d1525", text_color=self.COLORS["muted"], font=("Cascadia Mono", 11))
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.log_box.configure(state="disabled")
        self.pause_button = ctk.CTkButton(log_card, text="Ⅱ  Pause transfer", fg_color=self.COLORS["danger"], hover_color="#d9505e", command=self.pause_download)
        self.pause_button.grid(row=2, column=0, sticky="e", padx=18, pady=(0, 14))

    def _build_settings_page(self) -> None:
        page = self._page("settings")
        card = self._card(page, row=0, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Download folder", font=("Segoe UI Semibold", 14)).grid(row=0, column=0, sticky="w", padx=22, pady=(22, 6))
        self.output_entry = ctk.CTkEntry(card, textvariable=self.output_dir, height=42)
        self.output_entry.grid(row=1, column=0, sticky="ew", padx=(22, 10), pady=(0, 16))
        ctk.CTkButton(card, text="Browse…", fg_color=self.COLORS["card"], command=self.choose_output).grid(row=1, column=1, padx=(0, 22), pady=(0, 16))
        ctk.CTkLabel(card, text="Metadata timeout (seconds)", font=("Segoe UI Semibold", 14)).grid(row=2, column=0, sticky="w", padx=22, pady=(6, 6))
        self.metadata_timeout = ctk.CTkEntry(card, width=120, height=40)
        self.metadata_timeout.insert(0, "120")
        self.metadata_timeout.grid(row=3, column=0, sticky="w", padx=22, pady=(0, 22))
        ctk.CTkLabel(card, text="VLC is required only for watch-while-downloading mode.", text_color=self.COLORS["muted"]).grid(row=4, column=0, columnspan=2, sticky="w", padx=22, pady=(0, 22))

    def _tree(self, parent, columns, selectmode="browse") -> ttk.Treeview:
        return ttk.Treeview(parent, columns=columns, show="headings", style="Torrent.Treeview", selectmode=selectmode)

    def _primary_button(self, parent, text, command, color=None):
        return ctk.CTkButton(parent, text=text, height=42, corner_radius=9, command=command, fg_color=color or self.COLORS["accent"], hover_color=self.COLORS["accent_hover"], text_color="#071712", font=("Segoe UI Semibold", 12))

    def show_page(self, name: str) -> None:
        titles = {"search": "Search", "explorer": "Torrent Explorer", "download": "Transfer", "settings": "Settings"}
        for page_name, page in self.pages.items():
            if page_name == name:
                page.tkraise()
            self.nav_buttons[page_name].configure(fg_color=self.COLORS["card"] if page_name == name else "transparent")
        self.page_title.configure(text=titles[name])

    def _run(self, target) -> None:
        threading.Thread(target=target, daemon=True).start()

    def _emit(self, kind: str, payload=None) -> None:
        self.events.put((kind, payload))

    def _process_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                handler = getattr(self, f"_event_{kind}", None)
                if handler:
                    handler(payload)
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def start_search(self) -> None:
        query = self.search_entry.get().strip()
        if not query or self.busy:
            return
        self.busy = True
        self.status_text.set("Searching…")
        self._clear_tree(self.results_tree)
        self._log(f"Searching all available engines for: {query}")

        def worker():
            try:
                results, errors = [], []
                config = ROOT / "search_config.json"
                if config.is_file():
                    torznab, source_errors = cli.search_all(cli.load_sources(config), query, 30)
                    results.extend(torznab)
                    errors.extend(source_errors)
                plugins, plugin_errors = cli.search_local_plugins(query, 30)
                results.extend(plugins)
                errors.extend(plugin_errors)
                self._emit("search_done", (cli.merge_results(results), errors))
            except Exception as error:
                self._emit("error", str(error))

        self._run(worker)

    def _event_search_done(self, payload) -> None:
        self.results, errors = payload
        self.results.sort(key=lambda item: (item.seeders, item.size), reverse=True)
        for index, item in enumerate(self.results):
            self.results_tree.insert("", "end", iid=str(index), values=(item.title, cli.format_size(item.size), item.seeders, item.leechers, item.source))
        self.result_count.configure(text=f"{len(self.results)} items")
        self.status_text.set("Ready")
        self.busy = False
        self._log(f"Search complete: {len(self.results)} unique results, {len(errors)} unavailable engines.")
        if not self.results:
            messagebox.showinfo("No results", "No torrent was found for this search.")

    def open_result(self) -> None:
        selected = self.results_tree.selection()
        if not selected:
            messagebox.showinfo("Select a result", "Choose a result to explore first.")
            return
        result = self.results[int(selected[0])]
        self.magnet_entry.delete(0, "end")
        self.magnet_entry.insert(0, result.url)
        self.show_page("explorer")
        self.start_metadata()

    def start_metadata(self) -> None:
        source = self.magnet_entry.get().strip()
        if self.busy or not source.startswith(("magnet:?", "http://", "https://")):
            if source and not self.busy:
                messagebox.showerror("Invalid link", "Paste a valid magnet link or torrent URL.")
            return
        try:
            timeout = int(self.metadata_timeout.get())
        except ValueError:
            messagebox.showerror("Invalid timeout", "Metadata timeout must be a number.")
            return
        self.busy = True
        self.status_text.set("Fetching metadata…")
        self.torrent_name.configure(text="Contacting peers…")
        self._clear_tree(self.files_tree)

        def worker():
            try:
                if self.aria:
                    self.aria.close()
                output = Path(self.output_dir.get()).expanduser().resolve()
                output.mkdir(parents=True, exist_ok=True)
                self.aria = cli.Aria2(output)
                gid, status = cli.wait_for_metadata(self.aria, source, timeout, lambda update: self._emit("metadata_progress", update))
                self._emit("metadata_done", (gid, status))
            except Exception as error:
                self._emit("error", str(error))

        self._run(worker)

    def _event_metadata_progress(self, update) -> None:
        if update["event"] == "waiting":
            self.torrent_meta.configure(text=f"Waiting {update['elapsed']}s  •  {update['seeders']} seeders  •  {update['connections']} connections")

    def _event_metadata_done(self, payload) -> None:
        self.torrent_gid, status = payload
        self.source_uri = self.magnet_entry.get().strip()
        self.files = status.get("files", [])
        name = status.get("bittorrent", {}).get("info", {}).get("name", "Unnamed torrent")
        self.torrent_name.configure(text=name)
        self.torrent_meta.configure(text=f"{len(self.files)} files  •  {status.get('numSeeders', '0')} current seeders")
        for index, item in enumerate(self.files, 1):
            self.files_tree.insert("", "end", iid=str(index), values=(index, item.get("path", "").replace("/", "\\"), cli.format_size(int(item.get("length", 0)))))
        self.status_text.set("Ready")
        self.busy = False
        self._log(f"Metadata received: {name} ({len(self.files)} files).")

    def select_all_files(self) -> None:
        self.files_tree.selection_set(self.files_tree.get_children())

    def start_download(self, stream: bool) -> None:
        selected_rows = self.files_tree.selection()
        if not self.torrent_gid or not selected_rows or self.busy:
            if not self.busy:
                messagebox.showinfo("Select files", "Select one or more files to download.")
            return
        selected = {int(item) for item in selected_rows}
        stream_item = None
        if stream:
            if len(selected) != 1 or not cli.is_video(self.files[next(iter(selected)) - 1]):
                messagebox.showerror("Streaming unavailable", "Select exactly one video file to stream.")
                return
            if not cli.find_vlc():
                messagebox.showerror("VLC required", "VLC is required for streaming. Install it from https://www.videolan.org/vlc/")
                return
            stream_item = self.files[next(iter(selected)) - 1]
        self.busy = True
        self.stop_download.clear()
        self.detail_text.set(Path(self.files[next(iter(selected)) - 1]["path"]).name if len(selected) == 1 else f"Downloading {len(selected)} files")
        self.status_text.set("Downloading…")
        self.show_page("download")
        metadata_timeout = int(self.metadata_timeout.get())
        source_uri = self.source_uri

        def worker():
            try:
                current_stream_item = stream_item
                self._emit("download_event", ("preparing", {}))
                self.torrent_gid, refreshed_status = cli.renew_finished_torrent(
                    self.aria,
                    self.torrent_gid,
                    source_uri,
                    metadata_timeout,
                    lambda update: self._emit("metadata_progress", update),
                )
                refreshed_files = refreshed_status.get("files", [])
                if refreshed_files:
                    self.files = refreshed_files
                    if current_stream_item:
                        current_stream_item = self.files[next(iter(selected)) - 1]
                cli.download(self.aria, self.torrent_gid, selected, current_stream_item, lambda update: self._emit("download_progress", update), lambda event, data: self._emit("download_event", (event, data)), self.stop_download.is_set)
            except Exception as error:
                self._emit("error", str(error))

        self._run(worker)

    def _event_download_progress(self, update) -> None:
        self.progress_bar.set(update["progress"] / 100)
        self.progress_text.set(f"{update['progress']:.2f}%")
        self.speed_text.set(update["speed"])
        self.seeders_text.set(str(update["seeders"]))

    def _event_download_event(self, payload) -> None:
        event, data = payload
        messages = {"preparing": "Preparing torrent task…", "started": "Transfer started.", "opening_vlc": "Buffer ready. Opening VLC…", "complete": "Download complete.", "paused": "Transfer paused."}
        if event == "buffering":
            self.detail_text.set(
                f"Buffering for VLC: {cli.format_size(data['buffered'])} / "
                f"{cli.format_size(data['required'])}"
            )
        else:
            self._log(messages.get(event, event))
        if event in {"complete", "paused"}:
            self.busy = False
            self.status_text.set("Complete" if event == "complete" else "Paused")

    def pause_download(self) -> None:
        if self.busy:
            self.stop_download.set()

    def _event_error(self, message: str) -> None:
        self.busy = False
        self.status_text.set("Error")
        self._log(f"ERROR: {message}")
        messagebox.showerror("Live Torrent Client", message)

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir.get())
        if selected:
            self.output_dir.set(selected)

    def _clear_tree(self, tree) -> None:
        tree.delete(*tree.get_children())

    def _log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"• {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def close_app(self) -> None:
        self.stop_download.set()
        if self.aria:
            try:
                self.aria.close()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    LiveTorrentApp().mainloop()
