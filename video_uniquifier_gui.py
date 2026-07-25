#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_uniquifier_gui.py
=======================

Тёмное окно (tkinter) для video_uniquifier.py — все параметры настраиваются мышкой.

Запуск:
    python video_uniquifier_gui.py

Сборка в .exe:
    Собрать_EXE.bat   (или см. README)
"""

from __future__ import annotations

import queue
import shutil
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import video_uniquifier as engine


APP_TITLE = "Video Uniquifier — уникализация видео"
SETTINGS_FILE = "video_uniquifier_settings.json"

FROZEN = getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Папка рядом с .exe (в собранном виде) или рядом со скриптом."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled(rel: str) -> Path:
    """Путь к ресурсу, вшитому в .exe (_MEIPASS), с фолбэком рядом с приложением."""
    if FROZEN:
        p = Path(getattr(sys, "_MEIPASS", app_dir())) / rel
        if p.exists():
            return p
    return app_dir() / rel


def default_binary(name: str) -> str:
    """Встроенный ffmpeg/ffprobe: сначала рядом с exe (стабильно), затем из бандла, иначе PATH."""
    if FROZEN:
        for cand in (app_dir() / f"{name}.exe",
                     Path(getattr(sys, "_MEIPASS", "")) / f"{name}.exe"):
            if cand.exists():
                return str(cand)
    return name


def prepare_runtime() -> None:
    """
    Готовит рабочее окружение рядом с приложением:
      * создаёт папки input_videos / output_videos / audio_tracks / overlays;
      * в собранном exe — распаковывает встроенные FFmpeg и фейк-точки в стабильные
        пути рядом с exe (иначе они лежат во временной _MEIPASS, которая недоступна
        дочерним процессам обработки — из-за этого FFmpeg «не находился»).
    """
    base = app_dir()
    for sub in ("input_videos", "output_videos", "audio_tracks", "overlays"):
        try:
            (base / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    if not FROZEN:
        return

    mei = Path(getattr(sys, "_MEIPASS", ""))
    # Фейк-точки -> overlays рядом с exe.
    src_ov = mei / "overlays"
    if src_ov.is_dir():
        for png in src_ov.glob("*.png"):
            dst = base / "overlays" / png.name
            if not dst.exists():
                try:
                    shutil.copy2(png, dst)
                except OSError:
                    pass
    # FFmpeg/ffprobe -> рядом с exe (стабильный путь для subprocess в воркерах).
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        src = mei / name
        dst = base / name
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass

# ---- тёмная палитра ----
BG = "#1f1f27"
PANEL = "#2a2a35"
FG = "#e6e6ea"
MUTED = "#9aa0b5"
ENTRY_BG = "#33333f"
TROUGH = "#3a3a46"
ACCENT = "#4c78d0"
ACCENT_ACT = "#5f8ae0"
OK_GREEN = "#4caf7d"


def apply_dark_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, fieldbackground=ENTRY_BG,
                    bordercolor="#454552", darkcolor=PANEL, lightcolor=PANEL,
                    troughcolor=TROUGH, arrowcolor=FG, insertcolor=FG,
                    focuscolor=BG, selectbackground=ACCENT, selectforeground="white")
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("TLabelframe", background=BG, bordercolor="#454552", relief="groove")
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
    style.configure("TButton", background=PANEL, foreground=FG, bordercolor="#555")
    style.map("TButton",
              background=[("active", ACCENT), ("pressed", ACCENT_ACT), ("disabled", PANEL)],
              foreground=[("active", "white"), ("disabled", MUTED)])
    style.configure("Accent.TButton", background=ACCENT, foreground="white")
    style.map("Accent.TButton", background=[("active", ACCENT_ACT), ("disabled", "#3a4256")])
    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", MUTED)])
    style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG, insertcolor=FG)
    style.configure("TSpinbox", fieldbackground=ENTRY_BG, foreground=FG,
                    arrowcolor=FG, insertcolor=FG)
    style.configure("TCombobox", fieldbackground=ENTRY_BG, foreground=FG, arrowcolor=FG)
    style.map("TCombobox", fieldbackground=[("readonly", ENTRY_BG)],
              foreground=[("readonly", FG)])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=TROUGH,
                    bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)

    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", ENTRY_BG)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(APP_TITLE)
        root.geometry("820x760")
        root.minsize(720, 620)
        apply_dark_theme(root)

        self.msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self._closing = False

        # Создаём папки и распаковываем встроенные FFmpeg/точки рядом с приложением.
        prepare_runtime()

        d = engine.Config()
        base = app_dir()

        # ---- переменные ----
        # Рабочие папки и точки — рядом с приложением (стабильные пути).
        self.var_input = tk.StringVar(value=str(base / d.input_dir))
        self.var_output = tk.StringVar(value=str(base / d.output_dir))
        self.var_audiodir = tk.StringVar(value=str(base / d.audio_dir))
        self.var_overlaydir = tk.StringVar(value=str(base / "overlays"))

        self.var_copies = tk.IntVar(value=d.copies_per_video)
        self.var_audio = tk.StringVar(value=d.audio_mode)
        self.var_sequential = tk.BooleanVar(value=False)
        self.var_overlay = tk.BooleanVar(value=d.overlay_enabled)

        self.var_mindur = tk.DoubleVar(value=d.duration_range[0])
        self.var_maxdur = tk.DoubleVar(value=d.duration_range[1])
        self.var_trimmin = tk.DoubleVar(value=d.trim_range[0])
        self.var_trimmax = tk.DoubleVar(value=d.trim_range[1])
        self.var_spmin = tk.DoubleVar(value=d.speed_dev_range[0])
        self.var_spmax = tk.DoubleVar(value=d.speed_dev_range[1])

        self.var_width = tk.IntVar(value=d.out_width)
        self.var_height = tk.IntVar(value=d.out_height)
        self.var_fps = tk.IntVar(value=d.target_fps or 0)
        self.var_workers = tk.IntVar(value=d.workers)
        self.var_crf = tk.IntVar(value=d.crf)
        self.var_seed = tk.StringVar(value="")
        self.var_overwrite = tk.BooleanVar(value=d.overwrite)
        self.var_meta = tk.BooleanVar(value=d.randomize_metadata)
        self.var_verify = tk.BooleanVar(value=d.verify)

        self.var_ovmin = tk.IntVar(value=d.overlay_count_range[0])
        self.var_ovmax = tk.IntVar(value=d.overlay_count_range[1])
        self.var_ovop = tk.DoubleVar(value=d.overlay_opacity)
        self.var_ovsmin = tk.DoubleVar(value=d.overlay_scale_range[0])
        self.var_ovsmax = tk.DoubleVar(value=d.overlay_scale_range[1])

        self.var_color = tk.BooleanVar(value=True)
        # Сила цветокоррекции в % (макс. отклонение каждого канала).
        self.var_br = tk.DoubleVar(value=round(d.brightness_pct[1] * 100, 1))
        self.var_ct = tk.DoubleVar(value=round(d.contrast_pct[1] * 100, 1))
        self.var_sat = tk.DoubleVar(value=round(d.saturation_pct[1] * 100, 1))
        self.var_gm = tk.DoubleVar(value=round(d.gamma_pct[1] * 100, 1))
        self.var_fx_noise = tk.BooleanVar(value=d.fx_noise)
        self.var_noise_max = tk.DoubleVar(value=d.fx_noise_max)
        self.var_fx_sharpen = tk.BooleanVar(value=d.fx_sharpen)
        self.var_sharpen_max = tk.DoubleVar(value=d.fx_sharpen_max)
        self.var_fx_zoom = tk.BooleanVar(value=d.fx_zoom)
        self.var_zoom_max = tk.DoubleVar(value=d.fx_zoom_max)

        self.var_ffmpeg = tk.StringVar(value=default_binary("ffmpeg"))
        self.var_ffprobe = tk.StringVar(value=default_binary("ffprobe"))

        # Подхватываем сохранённые настройки (папки и т.д.) до построения интерфейса.
        self._load_settings()
        self._fixup_binaries()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_id = self.root.after(100, self._poll_queue)

    # ------------------------------------------------------- память настроек
    def _all_vars(self) -> dict:
        return {k: v for k, v in self.__dict__.items()
                if k.startswith("var_") and isinstance(v, tk.Variable)}

    def _settings_path(self) -> Path:
        return app_dir() / SETTINGS_FILE

    def _load_settings(self) -> None:
        import json
        f = self._settings_path()
        if not f.exists():
            return
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for name, var in self._all_vars().items():
            if name in data:
                try:
                    var.set(data[name])
                except Exception:  # noqa: BLE001 — битое/несовместимое значение игнорируем
                    pass

    def _save_settings(self) -> None:
        import json
        data = {name: var.get() for name, var in self._all_vars().items()}
        try:
            self._settings_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _fixup_binaries(self) -> None:
        """Сбросить путь к ffmpeg/ffprobe на актуальный, если сохранённый указывает в никуда."""
        for var, name in ((self.var_ffmpeg, "ffmpeg"), (self.var_ffprobe, "ffprobe")):
            val = var.get().strip()
            if val and ("/" in val or "\\" in val) and not Path(val).exists():
                var.set(default_binary(name))

    def _on_close(self) -> None:
        self._closing = True
        try:
            self.root.after_cancel(self._poll_id)
        except Exception:  # noqa: BLE001
            pass
        self._save_settings()
        self.root.destroy()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        # Нижняя панель (кнопки + прогресс + лог) — фиксированная.
        bottom = ttk.Frame(self.root, padding=(12, 6, 12, 10))
        bottom.pack(side="bottom", fill="both")

        ctl = ttk.Frame(bottom)
        ctl.pack(fill="x")
        self.btn_start = ttk.Button(ctl, text="▶  Старт", style="Accent.TButton", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(ctl, text="■  Отмена", command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self.btn_check = ttk.Button(ctl, text="Проверить FFmpeg", command=self.on_check_ffmpeg)
        self.btn_check.pack(side="left", padx=(8, 0))
        self.lbl_status = ttk.Label(ctl, text="Готов к работе.", style="Muted.TLabel")
        self.lbl_status.pack(side="left", padx=(14, 0))

        self.progress = ttk.Progressbar(bottom, mode="determinate", style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(8, 6))

        logframe = ttk.LabelFrame(bottom, text="Лог", padding=4)
        logframe.pack(fill="both")
        self.log = tk.Text(logframe, height=8, wrap="word", state="disabled",
                           bg=ENTRY_BG, fg=FG, insertbackground=FG, relief="flat",
                           highlightthickness=0, borderwidth=0)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logframe, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set)

        # Верхняя часть — прокручиваемая область настроек.
        outer = ttk.Frame(self.root)
        outer.pack(side="top", fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        main = ttk.Frame(canvas, padding=12)
        win = canvas.create_window((0, 0), window=main, anchor="nw")
        main.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind_all("<MouseWheel>", _wheel)

        self._build_settings(main)

    def _build_settings(self, main: ttk.Frame) -> None:
        pad = dict(padx=6, pady=3)

        # ---- Папки ----
        folders = ttk.LabelFrame(main, text="1 · Папки", padding=8)
        folders.pack(fill="x")
        self._folder_row(folders, "Исходные видео:", self.var_input, 0)
        self._folder_row(folders, "Результат:", self.var_output, 1)
        self._folder_row(folders, "Аудио-треки:", self.var_audiodir, 2)
        self._folder_row(folders, "Оверлеи (PNG-точки):", self.var_overlaydir, 3)
        folders.columnconfigure(1, weight=1)

        # ---- Основное ----
        core = ttk.LabelFrame(main, text="2 · Основное", padding=8)
        core.pack(fill="x", pady=(8, 0))
        ttk.Label(core, text="Копий из каждого видео:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Spinbox(core, from_=1, to=999, textvariable=self.var_copies, width=8).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(core, text="Аудио:").grid(row=0, column=2, sticky="w", **pad)
        ttk.Combobox(core, textvariable=self.var_audio, width=12, state="readonly",
                     values=["mute", "background", "keep"]).grid(row=0, column=3, sticky="w", **pad)
        ttk.Checkbutton(core, text="Накладывать фейк-точки (PNG из overlays/)",
                        variable=self.var_overlay).grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(core, text="Резать длинные видео на отрезки 6-8с",
                        variable=self.var_sequential).grid(row=1, column=2, columnspan=2, sticky="w", **pad)

        # ---- Фейк-точки ----
        dots = ttk.LabelFrame(main, text="2b · Фейк-точки (незаметные, из overlays/)", padding=8)
        dots.pack(fill="x", pady=(8, 0))
        self._range_row(dots, "Точек на видео:", self.var_ovmin, self.var_ovmax,
                        0, 0, 12, 1, "случайно в этом диапазоне")
        ttk.Label(dots, text="Прозрачность:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Spinbox(dots, from_=0.05, to=1.0, increment=0.05, textvariable=self.var_ovop, width=8).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(dots, text="меньше = незаметнее (0.6)", style="Muted.TLabel").grid(row=1, column=2, columnspan=3, sticky="w", **pad)
        self._range_row(dots, "Размер (доля кадра):", self.var_ovsmin, self.var_ovsmax,
                        2, 0.02, 0.5, 0.01, "напр. 0.05 / 0.13")
        ttk.Button(dots, text="Сгенерировать 20 точек",
                   command=self.on_generate_dots).grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(dots, text="(создаст PNG в overlays/ — нужен Pillow)",
                  style="Muted.TLabel").grid(row=3, column=2, columnspan=3, sticky="w", **pad)

        # ---- Длина / обрезка / скорость ----
        tim = ttk.LabelFrame(main, text="3 · Длина, обрезка, скорость", padding=8)
        tim.pack(fill="x", pady=(8, 0))
        self._range_row(tim, "Финальная длина, сек:", self.var_mindur, self.var_maxdur,
                        0, 1, 60, 0.5, "мин / макс (итог 6-8с)")
        self._range_row(tim, "Обрезка с края, сек:", self.var_trimmin, self.var_trimmax,
                        1, 0.0, 2.0, 0.01, "напр. 0.01 / 0.10")
        self._range_row(tim, "Скорость ± (отклонение):", self.var_spmin, self.var_spmax,
                        2, 0.0, 0.5, 0.01, "0.01 = ±1%, 0.1 = ±10%")

        # ---- Формат / качество ----
        fmt = ttk.LabelFrame(main, text="4 · Формат и качество", padding=8)
        fmt.pack(fill="x", pady=(8, 0))
        ttk.Label(fmt, text="Кадр Ш × В:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Spinbox(fmt, from_=16, to=8192, textvariable=self.var_width, width=8).grid(row=0, column=1, sticky="w", **pad)
        ttk.Spinbox(fmt, from_=16, to=8192, textvariable=self.var_height, width=8).grid(row=0, column=2, sticky="w", **pad)
        ttk.Label(fmt, text="(9:16 = 1080×1920)", style="Muted.TLabel").grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(fmt, text="FPS (0 = как в источнике):").grid(row=1, column=0, sticky="w", **pad)
        ttk.Spinbox(fmt, from_=0, to=120, textvariable=self.var_fps, width=8).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(fmt, text="CRF (меньше = лучше):").grid(row=1, column=2, sticky="w", **pad)
        ttk.Spinbox(fmt, from_=0, to=51, textvariable=self.var_crf, width=8).grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(fmt, text="Процессов (0 = авто):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Spinbox(fmt, from_=0, to=64, textvariable=self.var_workers, width=8).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(fmt, text="Seed (пусто = случайно):").grid(row=2, column=2, sticky="w", **pad)
        ttk.Entry(fmt, textvariable=self.var_seed, width=10).grid(row=2, column=3, sticky="w", **pad)
        ttk.Checkbutton(fmt, text="Перезаписывать существующие файлы",
                        variable=self.var_overwrite).grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(fmt, text="Уникальные метаданные (дата/comment/handler)",
                        variable=self.var_meta).grid(row=3, column=2, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(fmt, text="Проверять уникальность копий в конце",
                        variable=self.var_verify).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

        # ---- Цвет и эффекты ----
        fx = ttk.LabelFrame(main, text="5 · Цветокоррекция и эффекты", padding=8)
        fx.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(fx, text="Цветокоррекция (случайное отклонение ± по каждому каналу)",
                        variable=self.var_color).grid(row=0, column=0, columnspan=6, sticky="w", **pad)
        # Сила каждого канала в % (макс. отклонение).
        cc = ttk.Frame(fx)
        cc.grid(row=1, column=0, columnspan=6, sticky="w")
        for i, (lbl, var) in enumerate([("Яркость %", self.var_br), ("Контраст %", self.var_ct),
                                        ("Насыщ. %", self.var_sat), ("Гамма %", self.var_gm)]):
            ttk.Label(cc, text=lbl, style="Muted.TLabel").grid(row=0, column=i * 2, sticky="e", padx=(6, 2), pady=3)
            ttk.Spinbox(cc, from_=0, to=50, increment=0.5, textvariable=var, width=6).grid(row=0, column=i * 2 + 1, sticky="w", padx=(0, 8), pady=3)
        self._fx_row(fx, "Шум", self.var_fx_noise, self.var_noise_max, 2, 0, 30, 1, "сила 0-30")
        self._fx_row(fx, "Резкость", self.var_fx_sharpen, self.var_sharpen_max, 3, 0, 1.5, 0.1, "0-1.5")
        self._fx_row(fx, "Микро-зум", self.var_fx_zoom, self.var_zoom_max, 4, 0, 0.1, 0.01, "0-0.1 (доля)")

        # ---- FFmpeg ----
        ff = ttk.LabelFrame(main, text="6 · FFmpeg (если не в PATH — укажите путь)", padding=8)
        ff.pack(fill="x", pady=(8, 0))
        ttk.Label(ff, text="ffmpeg:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.var_ffmpeg).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(ff, text="…", width=3, command=lambda: self._pick_file(self.var_ffmpeg)).grid(row=0, column=2, **pad)
        ttk.Label(ff, text="ffprobe:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.var_ffprobe).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(ff, text="…", width=3, command=lambda: self._pick_file(self.var_ffprobe)).grid(row=1, column=2, **pad)
        ff.columnconfigure(1, weight=1)

    # --- строители строк ---
    def _folder_row(self, parent, label, var, row) -> None:
        pad = dict(padx=6, pady=3)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(parent, text="Обзор…", command=lambda v=var: self._pick_dir(v)).grid(row=row, column=2, **pad)

    def _range_row(self, parent, label, vmin, vmax, row, lo, hi, step, hint) -> None:
        pad = dict(padx=6, pady=3)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **pad)
        ttk.Spinbox(parent, from_=lo, to=hi, increment=step, textvariable=vmin, width=8).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(parent, text="–", style="Muted.TLabel").grid(row=row, column=2, **pad)
        ttk.Spinbox(parent, from_=lo, to=hi, increment=step, textvariable=vmax, width=8).grid(row=row, column=3, sticky="w", **pad)
        ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=row, column=4, sticky="w", **pad)

    def _fx_row(self, parent, name, var_on, var_max, row, lo, hi, step, hint) -> None:
        pad = dict(padx=6, pady=3)
        ttk.Checkbutton(parent, text=name, variable=var_on).grid(row=row, column=0, sticky="w", **pad)
        ttk.Label(parent, text="макс:", style="Muted.TLabel").grid(row=row, column=1, sticky="e", **pad)
        ttk.Spinbox(parent, from_=lo, to=hi, increment=step, textvariable=var_max, width=8).grid(row=row, column=2, sticky="w", **pad)
        ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=row, column=3, sticky="w", **pad)

    def _pick_dir(self, var) -> None:
        path = filedialog.askdirectory(initialdir=var.get() or ".")
        if path:
            var.set(path)

    def _pick_file(self, var) -> None:
        path = filedialog.askopenfilename(initialdir=".")
        if path:
            var.set(path)

    # ------------------------------------------------------------- запуск
    def _build_config(self) -> engine.Config:
        seed_txt = self.var_seed.get().strip()
        seed = int(seed_txt) if seed_txt else None
        fps = self.var_fps.get()
        lo, hi = self.var_mindur.get(), self.var_maxdur.get()
        tmin, tmax = self.var_trimmin.get(), self.var_trimmax.get()
        smin, smax = self.var_spmin.get(), self.var_spmax.get()

        kwargs = dict(
            input_dir=Path(self.var_input.get()),
            output_dir=Path(self.var_output.get()),
            audio_dir=Path(self.var_audiodir.get()),
            overlay_dir=Path(self.var_overlaydir.get()),
            copies_per_video=max(1, self.var_copies.get()),
            segment_mode=("sequential" if self.var_sequential.get() else "window"),
            duration_range=(min(lo, hi), max(lo, hi)),
            trim_range=(min(tmin, tmax), max(tmin, tmax)),
            speed_dev_range=(min(smin, smax), max(smin, smax)),
            out_width=self.var_width.get(),
            out_height=self.var_height.get(),
            target_fps=(fps if fps > 0 else None),
            audio_mode=self.var_audio.get(),
            overlay_enabled=self.var_overlay.get(),
            overlay_count_range=(min(self.var_ovmin.get(), self.var_ovmax.get()),
                                 max(self.var_ovmin.get(), self.var_ovmax.get())),
            overlay_opacity=self.var_ovop.get(),
            overlay_scale_range=(min(self.var_ovsmin.get(), self.var_ovsmax.get()),
                                 max(self.var_ovsmin.get(), self.var_ovsmax.get())),
            fx_noise=self.var_fx_noise.get(), fx_noise_max=self.var_noise_max.get(),
            fx_sharpen=self.var_fx_sharpen.get(), fx_sharpen_max=self.var_sharpen_max.get(),
            fx_zoom=self.var_fx_zoom.get(), fx_zoom_max=self.var_zoom_max.get(),
            crf=self.var_crf.get(),
            workers=self.var_workers.get(),
            seed=seed,
            overwrite=self.var_overwrite.get(),
            randomize_metadata=self.var_meta.get(),
            verify=self.var_verify.get(),
            ffmpeg=self.var_ffmpeg.get().strip() or "ffmpeg",
            ffprobe=self.var_ffprobe.get().strip() or "ffprobe",
        )
        if self.var_color.get():
            def rng_pct(var):
                m = max(0.0, var.get()) / 100.0
                return (round(m * 0.4, 4), round(m, 4))
            kwargs.update(brightness_pct=rng_pct(self.var_br),
                          contrast_pct=rng_pct(self.var_ct),
                          saturation_pct=rng_pct(self.var_sat),
                          gamma_pct=rng_pct(self.var_gm))
        else:
            kwargs.update(brightness_pct=(0.0, 0.0), contrast_pct=(0.0, 0.0),
                          saturation_pct=(0.0, 0.0), gamma_pct=(0.0, 0.0))
        return engine.Config(**kwargs)

    def on_generate_dots(self) -> None:
        out = Path(self.var_overlaydir.get())
        try:
            import generate_dots as gd
            import random as _r
        except ImportError:
            messagebox.showerror(
                "Нет Pillow",
                "Для генерации точек нужен модуль Pillow.\nУстановите:  pip install Pillow")
            return
        try:
            out.mkdir(parents=True, exist_ok=True)
            rng = _r.Random()
            for i in range(20):
                style = rng.choice(gd.STYLES)
                size = rng.choice(gd.SIZES)
                gd.make_dot(size, style, rng).save(out / f"dot_{i + 1:02d}_{style}_{size}.png")
            self._log(f"Сгенерировано 20 точек в {out}")
            messagebox.showinfo("Готово", f"Создано 20 фейк-точек в:\n{out}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка генерации", str(e))

    def on_check_ffmpeg(self) -> None:
        try:
            engine.resolve_binaries(self._build_config())
            self.lbl_status.config(text="✔ FFmpeg найден.")
            messagebox.showinfo("FFmpeg", "FFmpeg и ffprobe найдены — можно запускать.")
        except SystemExit as e:
            messagebox.showerror("FFmpeg не найден", str(e))

    def on_start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            cfg = self._build_config()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка настроек", str(e))
            return
        self._save_settings()   # запоминаем выбранные настройки
        self.cancel_event.clear()
        self._set_running(True)
        self._clear_log()
        self.progress.config(value=0, maximum=100)
        self._log("Запуск обработки…")
        self.worker = threading.Thread(target=self._run_engine, args=(cfg,), daemon=True)
        self.worker.start()

    def on_cancel(self) -> None:
        self.cancel_event.set()
        self.lbl_status.config(text="Отмена — дождитесь завершения текущих клипов…")
        self.btn_cancel.config(state="disabled")

    def _run_engine(self, cfg: engine.Config) -> None:
        def progress_cb(done, total, ok, fail, skip):
            self.msg_queue.put(("progress", (done, total, ok, fail, skip)))

        def log_cb(msg):
            self.msg_queue.put(("log", msg))

        try:
            rc = engine.run(cfg, progress_cb=progress_cb, log_cb=log_cb,
                            cancel_check=self.cancel_event.is_set)
            self.msg_queue.put(("done", rc))
        except SystemExit as e:
            self.msg_queue.put(("fatal", str(e)))
        except Exception:  # noqa: BLE001
            self.msg_queue.put(("fatal", traceback.format_exc()))

    # ----------------------------------------------------- очередь → GUI
    def _poll_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "progress":
                    done, total, ok, fail, skip = payload
                    self.progress.config(maximum=total, value=done)
                    self.lbl_status.config(
                        text=f"{done}/{total}  |  готово: {ok}  ошибок: {fail}  пропущено: {skip}")
                elif kind == "done":
                    self._finish(payload)
                elif kind == "fatal":
                    self._log(payload)
                    last = payload.strip().splitlines()[-1] if payload.strip() else "Ошибка"
                    messagebox.showerror("Ошибка", last)
                    self._set_running(False)
                    self.lbl_status.config(text="Остановлено из-за ошибки.")
        except queue.Empty:
            pass
        self._poll_id = self.root.after(100, self._poll_queue)

    def _finish(self, rc: int) -> None:
        self._set_running(False)
        if rc == 0:
            self.lbl_status.config(text="✔ Готово. Результат в папке вывода.")
            messagebox.showinfo("Готово", f"Обработка завершена успешно.\nРезультат:\n{self.var_output.get()}")
        elif rc == 3:
            self.lbl_status.config(text="■ Отменено пользователем.")
        else:
            self.lbl_status.config(text="Завершено с ошибками — см. лог.")
            messagebox.showwarning("Завершено с ошибками",
                                   "Часть клипов не обработалась. Подробности — в логе и uniquifier.log.")

    # ------------------------------------------------------------ helpers
    def _set_running(self, running: bool) -> None:
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_cancel.config(state="normal" if running else "disabled")

    def _clear_log(self) -> None:
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _log(self, msg: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", msg.rstrip() + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


def main() -> None:
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    engine.setup_logging(engine.Config().log_file)
    main()
