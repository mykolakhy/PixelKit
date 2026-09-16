from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "ImageMagick Studio"
SUPPORTED_INPUTS = [
    ("Зображення", "*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.avif *.heic *.ico"),
    ("Усі файли", "*.*"),
]
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".ico"}
OUTPUT_FORMATS = ["Автоматично", "JPG", "PNG", "WEBP", "AVIF", "GIF", "BMP", "TIFF"]


def find_magick() -> str | None:
    """Find ImageMagick's modern CLI entry point."""
    candidates = [
        shutil.which("magick"),
        r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe",
        r"C:\Program Files\ImageMagick-7.1.1-Q16-HDRI\magick.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def human_size(value: int) -> str:
    units = ["Б", "КБ", "МБ", "ГБ"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} {unit}"
        size /= 1024
    return f"{value} Б"


class ImageMagickGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(980, 680)
        self.colors = {
            "bg": "#0d1117",
            "panel": "#151b24",
            "panel_alt": "#1b2330",
            "input": "#202a38",
            "border": "#2b3747",
            "text": "#f4f7fb",
            "muted": "#93a0b3",
            "accent": "#7c5cff",
            "accent_hover": "#9075ff",
            "teal": "#32d6c8",
            "danger": "#f2768b",
        }
        self.configure(bg=self.colors["bg"])

        self.magick = find_magick()
        self.source_path: Path | None = None
        self.source_paths: list[Path] = []
        self.default_output_path = True
        self.preview_photo: tk.PhotoImage | None = None
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.format_var = tk.StringVar(value=OUTPUT_FORMATS[0])
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.quality_var = tk.IntVar(value=82)
        self.keep_ratio_var = tk.BooleanVar(value=True)
        self.strip_metadata_var = tk.BooleanVar(value=True)
        self.background_var = tk.StringVar(value="#ffffff")
        self.status_var = tk.StringVar(value="Готово до роботи")
        self.details_var = tk.StringVar(value="Вибери зображення, щоб побачити його параметри")

        self._setup_style()
        self._build_ui()
        self.after(100, self._poll_worker)

        if not self.magick:
            self.after(
                300,
                lambda: messagebox.showwarning(
                    "ImageMagick не знайдено",
                    "Не вдалося знайти magick.exe. Встанови ImageMagick або додай його до PATH.",
                ),
            )

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        c = self.colors
        style.configure("App.TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["panel"])
        style.configure("Title.TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI", 25, "bold"))
        style.configure("Subtitle.TLabel", background=c["bg"], foreground=c["muted"], font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=c["panel"], foreground=c["text"], font=("Segoe UI", 12, "bold"))
        style.configure("Body.TLabel", background=c["panel"], foreground="#dce5f0", font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=c["panel"], foreground=c["muted"], font=("Segoe UI", 9))
        style.configure("Primary.TButton", background=c["accent"], foreground="#ffffff", borderwidth=0, focusthickness=0, font=("Segoe UI", 10, "bold"), padding=(18, 10))
        style.map("Primary.TButton", background=[("active", c["accent_hover"]), ("pressed", "#6649e8"), ("disabled", "#3d3a52")], foreground=[("disabled", "#9b98ad")])
        style.configure("Secondary.TButton", background=c["input"], foreground="#dce5f0", bordercolor=c["border"], lightcolor=c["input"], darkcolor=c["input"], borderwidth=1, focusthickness=0, font=("Segoe UI", 9), padding=(11, 8))
        style.map("Secondary.TButton", background=[("active", c["border"]), ("pressed", "#344257")], foreground=[("active", "#ffffff")])
        style.configure("TCheckbutton", background=c["panel"], foreground="#dce5f0", font=("Segoe UI", 9), padding=(0, 2))
        style.map("TCheckbutton", background=[("active", c["panel"])], foreground=[("active", "#ffffff")])
        style.configure("TEntry", fieldbackground=c["input"], foreground=c["text"], insertcolor=c["text"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], borderwidth=1, padding=8)
        style.map("TEntry", fieldbackground=[("focus", "#263246")], bordercolor=[("focus", c["accent"])])
        style.configure("TCombobox", fieldbackground=c["input"], foreground=c["text"], background=c["input"], arrowcolor=c["muted"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", c["input"])], foreground=[("readonly", c["text"])], bordercolor=[("focus", c["accent"])])
        style.configure("Horizontal.TScale", background=c["panel"], troughcolor=c["input"], borderwidth=0, sliderlength=18)
        style.configure("Horizontal.TProgressbar", background=c["teal"], troughcolor=c["input"], borderwidth=0, lightcolor=c["teal"], darkcolor=c["teal"])

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="App.TFrame", padding=(26, 22, 26, 18))
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, style="App.TFrame")
        header.pack(fill="x", pady=(0, 18))
        c = self.colors
        brand = tk.Frame(header, bg=c["bg"])
        brand.pack(side="left", anchor="w")
        tk.Label(brand, text="✦", bg=c["accent"], fg="#ffffff", font=("Segoe UI Symbol", 20, "bold"), width=2, height=1).pack(side="left", padx=(0, 13))
        brand_text = tk.Frame(brand, bg=c["bg"])
        brand_text.pack(side="left", anchor="w")
        ttk.Label(brand_text, text="ImageMagick Studio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(brand_text, text="Стискай, змінюй розмір і конвертуй зображення в кілька кліків", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))
        tk.Label(header, text="●  IMAGE PROCESSOR", bg="#172d2d", fg=c["teal"], font=("Segoe UI", 9, "bold"), padx=12, pady=7).pack(side="right", anchor="n", pady=(5, 0))

        content = ttk.Frame(root, style="App.TFrame")
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)

        self._build_left_panel(content)
        self._build_right_panel(content)

        status = ttk.Frame(root, style="App.TFrame")
        status.pack(fill="x", pady=(14, 0))
        tk.Label(status, textvariable=self.status_var, bg=self.colors["bg"], fg=self.colors["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180)
        self.progress.pack(side="right")

    def _card(self, parent: tk.Misc, **grid_options: object) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=18)
        card.grid(**grid_options)
        return card

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        left = ttk.Frame(parent, style="App.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)

        source_card = self._card(left, row=0, column=0, sticky="ew", pady=(0, 12))
        source_card.columnconfigure(0, weight=1)
        ttk.Label(source_card, text="1. Вхідні зображення", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(source_card, text="Обери один файл, кілька файлів або цілу папку", style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(3, 12))
        source_entry = ttk.Entry(source_card, textvariable=self.source_var, state="readonly")
        source_entry.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(source_card, text="Один файл…", style="Secondary.TButton", command=self._choose_source).grid(row=2, column=1, padx=(0, 6))
        ttk.Button(source_card, text="Кілька…", style="Secondary.TButton", command=self._choose_multiple_sources).grid(row=2, column=2, padx=(0, 6))
        ttk.Button(source_card, text="Папка…", style="Secondary.TButton", command=self._choose_source_folder).grid(row=2, column=3)
        self.file_listbox = tk.Listbox(source_card, height=4, bg=self.colors["input"], fg="#dce5f0", selectbackground=self.colors["accent"], selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9), activestyle="none")
        self.file_listbox.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Button(source_card, text="Очистити", style="Secondary.TButton", command=self._clear_sources).grid(row=3, column=3, sticky="ne", pady=(12, 0))
        ttk.Label(source_card, textvariable=self.details_var, style="Muted.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))

        resize_card = self._card(left, row=1, column=0, sticky="nsew", pady=(0, 12))
        resize_card.columnconfigure(1, weight=1)
        ttk.Label(resize_card, text="2. Розмір зображення", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(resize_card, text="Залиш поля порожніми, щоб не змінювати розмір", style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(3, 14))
        ttk.Label(resize_card, text="Ширина", style="Body.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Entry(resize_card, textvariable=self.width_var, width=10).grid(row=2, column=1, sticky="w", padx=(8, 18))
        ttk.Label(resize_card, text="px", style="Muted.TLabel").grid(row=2, column=2, sticky="w")
        ttk.Label(resize_card, text="Висота", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(resize_card, textvariable=self.height_var, width=10).grid(row=3, column=1, sticky="w", padx=(8, 18), pady=(10, 0))
        ttk.Label(resize_card, text="px", style="Muted.TLabel").grid(row=3, column=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(resize_card, text="Зберігати пропорції", variable=self.keep_ratio_var).grid(row=2, column=3, rowspan=2, sticky="w", padx=(24, 0))

        quality_card = self._card(left, row=2, column=0, sticky="ew", pady=(0, 12))
        quality_card.columnconfigure(1, weight=1)
        ttk.Label(quality_card, text="3. Стиснення та додаткові параметри", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(quality_card, text="Якість: 100 — найкраща якість, менше значення — менший файл", style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 10))
        ttk.Scale(quality_card, from_=10, to=100, variable=self.quality_var, orient="horizontal").grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 10))
        self.quality_label = ttk.Label(quality_card, text="82", style="Body.TLabel", width=4)
        self.quality_label.grid(row=2, column=2, sticky="e")
        self.quality_var.trace_add("write", lambda *_: self.quality_label.configure(text=str(self.quality_var.get())))
        ttk.Checkbutton(quality_card, text="Видалити метадані (EXIF та ін.)", variable=self.strip_metadata_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(quality_card, text="Фон для прозорих зображень при збереженні в JPG", style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Entry(quality_card, textvariable=self.background_var, width=12).grid(row=4, column=2, sticky="e", pady=(10, 0))

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        right = ttk.Frame(parent, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        preview_card = self._card(right, row=0, column=0, sticky="nsew", pady=(0, 12))
        preview_card.columnconfigure(0, weight=1)
        preview_card.rowconfigure(1, weight=1)
        ttk.Label(preview_card, text="Попередній перегляд", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.preview = tk.Label(preview_card, text="Тут з’явиться прев’ю", bg=self.colors["panel_alt"], fg=self.colors["muted"], font=("Segoe UI", 10), anchor="center")
        self.preview.grid(row=1, column=0, sticky="nsew", pady=(12, 0), ipadx=8, ipady=8)

        output_card = self._card(right, row=1, column=0, sticky="ew")
        output_card.columnconfigure(0, weight=1)
        ttk.Label(output_card, text="4. Формат і збереження", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(output_card, text="Формат файлу", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 0))
        format_box = ttk.Combobox(output_card, textvariable=self.format_var, values=OUTPUT_FORMATS, state="readonly", width=16)
        format_box.grid(row=1, column=1, sticky="e", pady=(14, 0))
        format_box.bind("<<ComboboxSelected>>", self._format_changed)
        self.output_label = ttk.Label(output_card, text="Файл результату", style="Body.TLabel")
        self.output_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 4))
        output_row = ttk.Frame(output_card, style="Card.TFrame")
        output_row.grid(row=3, column=0, columnspan=2, sticky="ew")
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.output_button = ttk.Button(output_row, text="Обрати…", style="Secondary.TButton", command=self._choose_output)
        self.output_button.grid(row=0, column=1)
        self.process_button = ttk.Button(output_card, text="Обробити та зберегти", style="Primary.TButton", command=self._start_processing)
        self.process_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(18, 0))

    def _choose_source(self) -> None:
        path = filedialog.askopenfilename(title="Вибери зображення", filetypes=SUPPORTED_INPUTS)
        if not path:
            return
        self._set_sources([Path(path)])
        self.status_var.set("Зображення завантажено")

    def _choose_multiple_sources(self) -> None:
        paths = filedialog.askopenfilenames(title="Вибери зображення", filetypes=SUPPORTED_INPUTS)
        if not paths:
            return
        self._set_sources([Path(path) for path in paths])
        self.status_var.set(f"Вибрано файлів: {len(self.source_paths)}")

    def _choose_source_folder(self) -> None:
        folder = filedialog.askdirectory(title="Вибери папку із зображеннями")
        if not folder:
            return
        paths = sorted(
            (path for path in Path(folder).iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES),
            key=lambda path: path.name.lower(),
        )
        if not paths:
            messagebox.showwarning("Зображення не знайдено", "У вибраній папці немає підтримуваних зображень.")
            return
        self._set_sources(paths)
        self.status_var.set(f"Вибрано файлів із папки: {len(self.source_paths)}")

    def _set_sources(self, paths: list[Path]) -> None:
        unique_paths = list(dict.fromkeys(path.resolve() for path in paths if path.is_file()))
        self.source_paths = unique_paths
        self.source_path = unique_paths[0] if unique_paths else None
        self.file_listbox.delete(0, tk.END)
        if not unique_paths:
            self.source_var.set("")
            self.details_var.set("Вибери зображення, щоб побачити його параметри")
            self.output_var.set("")
            self.preview.configure(image="", text="Тут з’явиться прев’ю")
            self.preview_photo = None
            self._update_output_ui()
            return

        for path in unique_paths:
            self.file_listbox.insert(tk.END, path.name)
        self.source_var.set(str(unique_paths[0]) if len(unique_paths) == 1 else f"Вибрано {len(unique_paths)} файлів")
        self.default_output_path = True
        self._set_default_output()
        self._load_source_info()
        self._make_preview()
        self._update_output_ui()

    def _clear_sources(self) -> None:
        if self.busy:
            return
        self._set_sources([])
        self.status_var.set("Список зображень очищено")

    def _update_output_ui(self) -> None:
        batch = len(self.source_paths) > 1
        self.output_label.configure(text="Папка результатів" if batch else "Файл результату")
        self.output_button.configure(text="Обрати папку…" if batch else "Обрати…")

    def _load_source_info(self) -> None:
        if not self.source_path:
            return
        size = self.source_path.stat().st_size
        dimensions = "розмір невідомий"
        if self.magick:
            try:
                result = subprocess.run(
                    [self.magick, "identify", "-format", "%wx%h", str(self.source_path)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=True,
                )
                dimensions = result.stdout.strip().splitlines()[0]
            except (OSError, subprocess.SubprocessError, IndexError):
                pass
        details = f"{dimensions}  •  {human_size(size)}  •  {self.source_path.suffix.upper().lstrip('.') or 'без розширення'}"
        if len(self.source_paths) > 1:
            details += f"  •  та ще {len(self.source_paths) - 1} файлів"
        self.details_var.set(details)

    def _make_preview(self) -> None:
        if not self.source_path or not self.magick:
            return
        self.preview.configure(text="Створюю прев’ю…", image="")

        def worker() -> None:
            try:
                preview_file = Path(tempfile.gettempdir()) / "imagemagick_studio_preview.png"
                command = [
                    self.magick,
                    str(self.source_path),
                    "-auto-orient",
                    "-thumbnail",
                    "480x320^",
                    "-gravity",
                    "center",
                    "-extent",
                    "480x320",
                    "-background",
                    self.colors["panel_alt"],
                    str(preview_file),
                ]
                subprocess.run(command, capture_output=True, timeout=30, check=True)
                self.worker_queue.put(("preview", preview_file))
            except (OSError, subprocess.SubprocessError) as exc:
                self.worker_queue.put(("preview_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _set_default_output(self) -> None:
        if not self.source_path:
            return
        if len(self.source_paths) > 1:
            self.output_var.set(str(self.source_path.parent / "optimized"))
            return
        extension = self._selected_extension()
        suffix = f".{extension}" if extension else self.source_path.suffix
        self.output_var.set(str(self.source_path.with_name(f"{self.source_path.stem}_optimized{suffix}")))

    def _selected_extension(self, source: Path | None = None) -> str:
        selected = self.format_var.get().lower()
        if selected == "автоматично":
            current_source = source or self.source_path
            return current_source.suffix.lstrip(".").lower() if current_source else "jpg"
        return selected.lower()

    def _format_changed(self, _event: object = None) -> None:
        if self.default_output_path:
            self._set_default_output()

    def _choose_output(self) -> None:
        if len(self.source_paths) > 1:
            selected = filedialog.askdirectory(title="Вибери папку для результатів")
            if selected:
                self.output_var.set(selected)
                self.default_output_path = False
            return
        initial = self.output_var.get() or "optimized.jpg"
        selected = filedialog.asksaveasfilename(
            title="Зберегти результат",
            initialfile=Path(initial).name,
            initialdir=str(Path(initial).parent) if Path(initial).parent.exists() else None,
            defaultextension=f".{self._selected_extension()}",
            filetypes=[("Зображення", "*.jpg *.jpeg *.png *.webp *.avif *.gif *.bmp *.tif *.tiff"), ("Усі файли", "*.*")],
        )
        if selected:
            self.output_var.set(selected)
            self.default_output_path = False

    def _validate(self) -> tuple[list[Path], Path] | None:
        if not self.magick:
            messagebox.showerror("ImageMagick не знайдено", "Перевір встановлення ImageMagick та змінну PATH.")
            return None
        if not self.source_paths or any(not path.exists() for path in self.source_paths):
            messagebox.showerror("Немає вхідного файлу", "Спочатку обери зображення для обробки.")
            return None
        output_text = self.output_var.get().strip()
        if not output_text:
            messagebox.showerror("Немає файлу результату", "Вкажи шлях, куди зберегти результат.")
            return None
        try:
            width = self.width_var.get().strip()
            height = self.height_var.get().strip()
            if width and (not width.isdigit() or int(width) <= 0):
                raise ValueError
            if height and (not height.isdigit() or int(height) <= 0):
                raise ValueError
        except ValueError:
            messagebox.showerror("Некоректний розмір", "Ширина та висота мають бути додатними цілими числами.")
            return None
        output = Path(output_text)
        if len(self.source_paths) > 1:
            output.mkdir(parents=True, exist_ok=True)
            return self.source_paths, output
        if not output.suffix:
            output = output.with_suffix(f".{self._selected_extension()}")
            self.output_var.set(str(output))
        output.parent.mkdir(parents=True, exist_ok=True)
        return self.source_paths, output

    def _batch_output_path(self, source: Path, output_dir: Path, used: set[Path]) -> Path:
        extension = self._selected_extension(source)
        candidate = output_dir / f"{source.stem}_optimized.{extension}"
        counter = 2
        while candidate.resolve() in used or candidate.exists():
            candidate = output_dir / f"{source.stem}_optimized_{counter}.{extension}"
            counter += 1
        used.add(candidate.resolve())
        return candidate

    def _build_command(self, source: Path, output: Path) -> list[str]:
        command = [self.magick, str(source), "-auto-orient"]
        width = self.width_var.get().strip()
        height = self.height_var.get().strip()
        if width or height:
            if width and height:
                resize = f"{width}x{height}" if self.keep_ratio_var.get() else f"{width}x{height}!"
            elif width:
                resize = f"{width}x"
            else:
                resize = f"x{height}"
            command += ["-resize", resize]

        extension = output.suffix.lower().lstrip(".")
        if extension in {"jpg", "jpeg"}:
            command += ["-background", self.background_var.get().strip() or "#ffffff", "-alpha", "remove"]
        if self.strip_metadata_var.get():
            command.append("-strip")
        if extension in {"jpg", "jpeg", "webp", "avif", "heic", "heif", "jxl", "png"}:
            command += ["-quality", str(int(self.quality_var.get()))]
        command += [str(output)]
        return command

    def _start_processing(self) -> None:
        validated = self._validate()
        if not validated or self.busy:
            return
        sources, destination = validated
        batch = len(sources) > 1
        if not batch and destination.resolve() == sources[0].resolve():
            messagebox.showerror("Небезпечне перезаписування", "Файл результату має відрізнятися від вхідного файлу.")
            return

        jobs: list[tuple[Path, Path]]
        if batch:
            used: set[Path] = set()
            jobs = [(source, self._batch_output_path(source, destination, used)) for source in sources]
            existing = [output for _, output in jobs if output.exists()]
            if existing and not messagebox.askyesno(
                "Файли вже існують",
                f"У папці вже є {len(existing)} файл(ів) результату.\n\nПерезаписати їх?",
            ):
                return
        else:
            jobs = [(sources[0], destination)]
            if destination.exists() and not messagebox.askyesno("Файл уже існує", f"Перезаписати файл?\n\n{destination}"):
                return

        self.busy = True
        self.process_button.configure(state="disabled")
        if batch:
            self.progress.configure(mode="determinate", maximum=len(jobs), value=0)
            self.status_var.set(f"Обробляю 0 / {len(jobs)}…")
        else:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.status_var.set("Обробляю зображення…")

        def worker() -> None:
            errors: list[tuple[Path, str]] = []
            completed = 0
            for source, output in jobs:
                command = self._build_command(source, output)
                try:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
                    if result.returncode == 0 and output.exists():
                        completed += 1
                    else:
                        error = result.stderr.strip() or result.stdout.strip() or "ImageMagick повернув невідому помилку."
                        errors.append((source, error))
                except subprocess.TimeoutExpired:
                    errors.append((source, "Обробка триває надто довго й була зупинена після 5 хвилин."))
                except OSError as exc:
                    errors.append((source, str(exc)))

                if batch:
                    self.worker_queue.put(("progress", (completed + len(errors), len(jobs), source.name)))
                else:
                    if errors:
                        self.worker_queue.put(("error", errors[0][1]))
                    else:
                        self.worker_queue.put(("done", output))
                    return
            self.worker_queue.put(("done_batch", (completed, len(jobs), errors, destination)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_worker(self) -> None:
        try:
            while True:
                kind, payload = self.worker_queue.get_nowait()
                if kind == "preview":
                    preview_path = Path(payload)
                    try:
                        self.preview_photo = tk.PhotoImage(file=str(preview_path))
                        self.preview.configure(image=self.preview_photo, text="")
                    except tk.TclError:
                        self.preview.configure(image="", text="Не вдалося показати прев’ю")
                elif kind == "preview_error":
                    self.preview.configure(image="", text="Не вдалося показати прев’ю")
                elif kind == "progress":
                    completed, total, current_name = payload
                    self.progress.configure(value=completed)
                    self.status_var.set(f"Обробляю {completed} / {total}: {current_name}")
                elif kind == "done":
                    output = Path(payload)
                    self.busy = False
                    self.process_button.configure(state="normal")
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate", value=0)
                    self.status_var.set(f"Готово: {output.name}  •  {human_size(output.stat().st_size)}")
                    messagebox.showinfo("Готово", f"Зображення збережено:\n\n{output}")
                elif kind == "done_batch":
                    completed, total, errors, output_dir = payload
                    self.busy = False
                    self.process_button.configure(state="normal")
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate", value=0)
                    self.status_var.set(f"Готово: {completed} / {total} файлів")
                    if errors:
                        error_lines = "\n".join(f"• {path.name}: {error[:180]}" for path, error in errors[:5])
                        if len(errors) > 5:
                            error_lines += f"\n• … та ще {len(errors) - 5} помилок"
                        messagebox.showwarning(
                            "Пакетну обробку завершено",
                            f"Успішно оброблено: {completed} / {total}\n\nПапка результатів:\n{output_dir}\n\nПомилки:\n{error_lines}",
                        )
                    else:
                        messagebox.showinfo(
                            "Пакетну обробку завершено",
                            f"Оброблено файлів: {completed}\n\nРезультати збережено в:\n{output_dir}",
                        )
                elif kind == "error":
                    self.busy = False
                    self.process_button.configure(state="normal")
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate", value=0)
                    self.status_var.set("Помилка обробки")
                    messagebox.showerror("ImageMagick повідомляє про помилку", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._poll_worker)


def main() -> None:
    app = ImageMagickGUI()
    app.mainloop()


if __name__ == "__main__":
    from ImageMagick_Studio_Qt import main as qt_main

    qt_main()
