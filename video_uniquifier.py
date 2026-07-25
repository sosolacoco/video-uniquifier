#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_uniquifier.py
===================

Массовая уникализация видео через FFmpeg.

Из каждого исходного ролика в input_videos/ создаётся N уникальных копий.
Каждая копия отличается случайным набором микро-изменений:
  * случайное окно/обрезка (0.05-0.15 c) + подгонка длины под 6-8 сек;
  * случайное изменение скорости 0.98x-1.02x (видео + звук, если он остаётся);
  * лёгкая цветокоррекция (brightness/contrast/saturation/gamma);
  * приведение к вертикали 9:16 (1080x1920) с crop лишних краёв;
  * случайный PNG-оверлей из overlays/ (если папка не пуста);
  * аудио: mute / фоновый трек из audio_tracks/ / сохранить оригинал;
  * очистка метаданных.

Всё делается ОДНОЙ командой ffmpeg на копию (filter_complex), без промежуточных
файлов. Файлы обрабатываются параллельно (ProcessPoolExecutor), прогресс — tqdm.

Запуск:
    python video_uniquifier.py --copies 5 --audio mute --workers 4

Требуется установленный ffmpeg/ffprobe (в PATH или задать --ffmpeg / --ffprobe).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional, Tuple

# tqdm — единственная внешняя зависимость. Аккуратный фолбэк, если не установлена.
try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover — заглушка, если tqdm не установлен
    class _DummyBar:
        def __init__(self, *a, **k):
            pass

        def update(self, n=1):
            pass

        def set_postfix(self, *a, **k):
            pass

        def close(self):
            pass

    def tqdm(iterable=None, *a, **k):  # type: ignore
        return iterable if iterable is not None else _DummyBar()


# ---------------------------------------------------------------------------
# КОНФИГ (значения по умолчанию; всё переопределяется аргументами CLI)
# ---------------------------------------------------------------------------

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac", ".opus"}


@dataclass
class Config:
    # Пути
    input_dir: Path = Path("input_videos")
    output_dir: Path = Path("output_videos")
    audio_dir: Path = Path("audio_tracks")
    overlay_dir: Path = Path("overlays")

    # Сколько уникальных копий делать из КАЖДОГО исходного видео
    copies_per_video: int = 5

    # Режим работы с длинными видео:
    #   "window"     — каждая копия берёт СЛУЧАЙНОЕ окно 6-8 c из ролика (по умолчанию);
    #   "sequential" — длинное видео режется на ПОДРЯД идущие отрезки 6-8 c,
    #                  и для КАЖДОГО отрезка делается copies_per_video копий.
    segment_mode: str = "window"

    # Длина / скорость
    duration_range: Tuple[float, float] = (6.0, 8.0)      # финальная длина, сек
    # Скорость задаётся как ОТКЛОНЕНИЕ от 1.0: ускорить/замедлить на 0.01..0.1.
    # Итоговый множитель = 1 ± uniform(min, max). Так изменение гарантированно есть.
    speed_dev_range: Tuple[float, float] = (0.01, 0.10)
    trim_range: Tuple[float, float] = (0.01, 0.10)        # обрезка с начала/конца, сек

    # Формат кадра (вертикаль 9:16)
    out_width: int = 1080
    out_height: int = 1920
    target_fps: Optional[int] = 30                        # None -> оставить как в источнике

    # Аудио: "mute" | "background" | "keep"
    audio_mode: str = "mute"
    audio_volume_range: Tuple[float, float] = (0.15, 0.45)  # громкость фонового трека

    # Цветокоррекция. brightness — аддитивный сдвиг (±); остальные — множители вокруг 1.0.
    brightness_pct: Tuple[float, float] = (0.02, 0.05)    # ±2-5%  -> сдвиг ±0.02..0.05
    contrast_pct: Tuple[float, float] = (0.02, 0.05)      # ±2-5%
    saturation_pct: Tuple[float, float] = (0.03, 0.05)    # ±3-5%
    gamma_pct: Tuple[float, float] = (0.03, 0.05)         # ±3-5%

    # Доп. эффекты для уникальности (каждый включается/выключается отдельно).
    fx_noise: bool = True
    fx_noise_max: float = 8.0          # сила шума (разумно 0..30)
    fx_sharpen: bool = True
    fx_sharpen_max: float = 0.8        # сила резкости unsharp (0..1.5)
    fx_zoom: bool = True
    fx_zoom_max: float = 0.05          # микро-зум, доля (0.05 = до +5%)

    # Оверлей (фейк-точки)
    overlay_enabled: bool = True
    overlay_scale_range: Tuple[float, float] = (0.05, 0.13)  # ширина точки как доля кадра
    overlay_count_range: Tuple[int, int] = (1, 4)            # сколько точек на копию (случайно)
    overlay_opacity: float = 0.6                             # доп. приглушение (0..1)

    # Метаданные: очищать источник и подставлять УНИКАЛЬНЫЕ теги каждой копии
    # (случайные creation_time / handler_name / comment) + убирать сигнатуру ffmpeg.
    randomize_metadata: bool = True

    # Кодирование
    video_codec: str = "libx264"
    preset: str = "veryfast"
    crf: int = 20
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"

    # Параллелизм
    workers: int = 0                                       # 0 -> авто (cpu/2)

    # Бинарники
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    # Прочее
    seed: Optional[int] = None                             # для воспроизводимости
    overwrite: bool = False                                # перезаписывать существующие
    verify: bool = True                                    # проверка уникальности после обработки
    log_file: Path = Path("uniquifier.log")


# ---------------------------------------------------------------------------
# УТИЛИТЫ
# ---------------------------------------------------------------------------

def setup_logging(log_file: Path) -> None:
    # Не падать на символах, которых нет в кодировке консоли (напр. cp1251).
    for stream in (sys.stderr, sys.stdout):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    # Меньше болтовни от tqdm/др.
    logging.getLogger("PIL").setLevel(logging.WARNING)


def resolve_binaries(cfg: Config) -> Config:
    """Найти ffmpeg/ffprobe. Возвращает cfg с абсолютными путями или падает с подсказкой."""
    ffmpeg = cfg.ffmpeg if os.path.isabs(cfg.ffmpeg) else (shutil.which(cfg.ffmpeg) or cfg.ffmpeg)
    ffprobe = cfg.ffprobe if os.path.isabs(cfg.ffprobe) else (shutil.which(cfg.ffprobe) or cfg.ffprobe)

    missing = []
    if not (os.path.isfile(ffmpeg) or shutil.which(ffmpeg)):
        missing.append("ffmpeg")
    if not (os.path.isfile(ffprobe) or shutil.which(ffprobe)):
        missing.append("ffprobe")

    if missing:
        raise SystemExit(
            f"[ОШИБКА] Не найдено: {', '.join(missing)}.\n"
            "Установите FFmpeg и убедитесь, что он в PATH, либо задайте пути явно:\n"
            "  --ffmpeg  C:\\ffmpeg\\bin\\ffmpeg.exe\n"
            "  --ffprobe C:\\ffmpeg\\bin\\ffprobe.exe\n"
            "Windows (быстро):   winget install --id Gyan.FFmpeg\n"
            "или скачать сборку:  https://www.gyan.dev/ffmpeg/builds/  (ffmpeg-release-full)\n"
        )
    return replace(cfg, ffmpeg=ffmpeg, ffprobe=ffprobe)


def ffprobe_duration(ffprobe: str, path: Path) -> Optional[float]:
    """Длительность видео в секундах (None при ошибке)."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        val = out.stdout.strip()
        return float(val) if val and val != "N/A" else None
    except Exception as e:  # noqa: BLE001
        logging.warning("ffprobe duration failed for %s: %s", path.name, e)
        return None


def ffprobe_dimensions(ffprobe: str, path: Path) -> Optional[Tuple[int, int]]:
    """(width, height) первого видеопотока изображения/видео. None при ошибке."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout or "{}")
        streams = data.get("streams") or []
        if streams:
            return int(streams[0]["width"]), int(streams[0]["height"])
    except Exception as e:  # noqa: BLE001
        logging.warning("ffprobe dimensions failed for %s: %s", path.name, e)
    return None


def has_audio_stream(ffprobe: str, path: Path) -> bool:
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# ГЕНЕРАЦИЯ СЛУЧАЙНЫХ ПАРАМЕТРОВ
# ---------------------------------------------------------------------------

def _signed_pct(rng: random.Random, lo: float, hi: float) -> float:
    """Случайная величина ±[lo..hi] со случайным знаком."""
    return rng.uniform(lo, hi) * rng.choice((-1.0, 1.0))


def compute_window(region_start: float, region_dur: float, speed: float,
                   cfg: Config, rng: random.Random) -> Tuple[float, float]:
    """
    Вернуть (start, raw_len) — окно исходника до применения скорости, ВНУТРИ
    заданной области [region_start, region_start + region_dur].

    В режиме "window" область = весь ролик -> случайное окно 6-8 c.
    В режиме "sequential" область = конкретный отрезок -> окно ≈ весь отрезок минус trim.
    Финальная длина = raw_len / speed, стремится в диапазон 6-8 c.
    """
    target_final = rng.uniform(*cfg.duration_range)     # желаемая финальная длина
    raw_len = target_final * speed                      # сколько взять из источника
    trim = rng.uniform(*cfg.trim_range)                 # микро-обрезка
    trim_from_start = rng.random() < 0.5

    usable = max(0.0, region_dur - trim)

    if usable <= 0.05:
        return round(region_start, 3), max(0.05, round(region_dur, 3))

    if raw_len >= usable:
        # Область короче желаемого окна — берём почти всё, отрезая trim с одной стороны.
        offset = trim if trim_from_start else 0.0
        return round(region_start + offset, 3), round(usable, 3)

    # Область длиннее — вырезаем случайное окно внутри неё.
    max_start = usable - raw_len
    base_start = rng.uniform(0.0, max_start)
    offset = base_start + (trim if trim_from_start else 0.0)
    return round(region_start + offset, 3), round(raw_len, 3)


def compute_segments(src_dur: float, cfg: Config,
                     rng: random.Random) -> List[Tuple[float, float]]:
    """
    Разбить ролик на ПОДРЯД идущие отрезки длиной ~6-8 c (для segment_mode='sequential').
    Возвращает список (start, dur). Хвост короче половины min_dur отбрасывается.
    """
    lo, hi = cfg.duration_range
    segments: List[Tuple[float, float]] = []
    pos = 0.0
    min_tail = lo * 0.5
    while pos < src_dur - 0.05:
        seg_len = rng.uniform(lo, hi)
        remaining = src_dur - pos
        if remaining <= hi:
            # Последний кусок: берём остаток, если он не слишком мал.
            if remaining >= min_tail:
                segments.append((round(pos, 3), round(remaining, 3)))
            elif segments:
                # Прицепляем крошечный хвост к предыдущему отрезку.
                ps, pd = segments[-1]
                segments[-1] = (ps, round(pd + remaining, 3))
            else:
                segments.append((round(pos, 3), round(remaining, 3)))
            break
        segments.append((round(pos, 3), round(seg_len, 3)))
        pos += seg_len
    return segments or [(0.0, round(src_dur, 3))]


@dataclass
class CopyParams:
    """Полный набор случайных параметров одной копии (пикутся в воркере по seed)."""
    speed: float
    start: float
    dur: float
    brightness: float
    contrast: float
    saturation: float
    gamma: float
    overlays: List[Tuple[str, int, int, int, int]]  # (путь, ширина, высота, x, y) для каждой точки
    audio_track: Optional[str]
    audio_volume: float
    fx_noise: int = 0
    fx_sharpen: float = 0.0
    fx_zoom: float = 0.0
    meta_time: str = ""
    meta_handler: str = ""
    meta_tag: str = ""


def build_params(region_start: float, region_dur: float, cfg: Config,
                 overlays: List[Tuple[str, int, int]],
                 audio_tracks: List[str],
                 rng: random.Random) -> CopyParams:
    speed = 1.0 + _signed_pct(rng, *cfg.speed_dev_range)   # 1 ± (0.01..0.1)
    start, dur = compute_window(region_start, region_dur, speed, cfg, rng)

    brightness = _signed_pct(rng, *cfg.brightness_pct)
    contrast = 1.0 + _signed_pct(rng, *cfg.contrast_pct)
    saturation = 1.0 + _signed_pct(rng, *cfg.saturation_pct)
    gamma = 1.0 + _signed_pct(rng, *cfg.gamma_pct)

    # Эффекты (случайная величина в 40-100% от максимума, чтобы были заметны).
    fx_noise = int(rng.uniform(cfg.fx_noise_max * 0.4, cfg.fx_noise_max)) \
        if cfg.fx_noise and cfg.fx_noise_max > 0 else 0
    fx_sharpen = round(rng.uniform(cfg.fx_sharpen_max * 0.4, cfg.fx_sharpen_max), 2) \
        if cfg.fx_sharpen and cfg.fx_sharpen_max > 0 else 0.0
    fx_zoom = round(rng.uniform(cfg.fx_zoom_max * 0.4, cfg.fx_zoom_max), 4) \
        if cfg.fx_zoom and cfg.fx_zoom_max > 0 else 0.0

    # Оверлей — СЛУЧАЙНОЕ число точек в СЛУЧАЙНЫХ местах.
    ov_list: List[Tuple[str, int, int, int, int]] = []
    if cfg.overlay_enabled and overlays:
        cmin, cmax = cfg.overlay_count_range
        count = rng.randint(min(cmin, cmax), max(cmin, cmax))
        for _ in range(count):
            opath, ow, oh = rng.choice(overlays)
            target_w = max(12, int(cfg.out_width * rng.uniform(*cfg.overlay_scale_range)))
            scale = target_w / ow if ow else 1.0
            w = target_w
            h = max(12, int(oh * scale))
            x = rng.randint(0, max(0, cfg.out_width - w))
            y = rng.randint(0, max(0, cfg.out_height - h))
            ov_list.append((opath, w, h, x, y))

    # Аудио
    track = None
    vol = rng.uniform(*cfg.audio_volume_range)
    if cfg.audio_mode == "background" and audio_tracks:
        track = rng.choice(audio_tracks)

    # Уникальные метаданные (детерминированно по seed, без обращения к системным часам).
    meta_time = meta_handler = meta_tag = ""
    if cfg.randomize_metadata:
        anchor = datetime.datetime(2026, 7, 1, 12, 0, 0)   # фиксированная опора
        dt = anchor - datetime.timedelta(days=rng.randint(0, 720), seconds=rng.randint(0, 86399))
        meta_time = dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        meta_handler = rng.choice(["Core Media Video", "VideoHandler",
                                   "ISO Media file", "Camera"])
        meta_tag = "".join(rng.choice("0123456789abcdef") for _ in range(16))

    return CopyParams(
        speed=round(speed, 5),
        start=start,
        dur=dur,
        brightness=round(brightness, 4),
        contrast=round(contrast, 4),
        saturation=round(saturation, 4),
        gamma=round(gamma, 4),
        overlays=ov_list,
        audio_track=track,
        audio_volume=round(vol, 3),
        fx_noise=fx_noise,
        fx_sharpen=fx_sharpen,
        fx_zoom=fx_zoom,
        meta_time=meta_time,
        meta_handler=meta_handler,
        meta_tag=meta_tag,
    )


# ---------------------------------------------------------------------------
# ПОСТРОЕНИЕ КОМАНДЫ FFMPEG
# ---------------------------------------------------------------------------

def build_ffmpeg_cmd(cfg: Config, src: Path, dst: Path,
                     p: CopyParams, keep_audio_possible: bool) -> List[str]:
    """Собрать одну команду ffmpeg для создания копии."""
    W, H = cfg.out_width, cfg.out_height

    cmd: List[str] = [cfg.ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    # -ss/-t на входе для быстрой и точной вырезки окна.
    cmd += ["-ss", f"{p.start:.3f}", "-t", f"{p.dur:.3f}", "-i", str(src)]

    input_idx = 1
    overlay_inputs = []  # (input_index, w, h, x, y)
    audio_idx = None

    for (opath, ow, oh, ox, oy) in p.overlays:
        cmd += ["-i", opath]
        overlay_inputs.append((input_idx, ow, oh, ox, oy))
        input_idx += 1

    if cfg.audio_mode == "background" and p.audio_track:
        # Зацикливаем трек, чтобы гарантированно покрыть длину клипа.
        cmd += ["-stream_loop", "-1", "-i", p.audio_track]
        audio_idx = input_idx
        input_idx += 1

    # ---- Видео-цепочка ----
    fps_part = f",fps={cfg.target_fps}" if cfg.target_fps else ""
    vchain = (
        f"[0:v]setpts=PTS/{p.speed:.5f}{fps_part},"
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"eq=brightness={p.brightness:.4f}:contrast={p.contrast:.4f}:"
        f"saturation={p.saturation:.4f}:gamma={p.gamma:.4f}"
    )

    # Доп. эффекты (микро-зум -> резкость -> шум).
    if p.fx_zoom and p.fx_zoom > 0:
        cw = int(W / (1.0 + p.fx_zoom)); cw -= cw % 2
        ch = int(H / (1.0 + p.fx_zoom)); ch -= ch % 2
        vchain += f",crop={cw}:{ch},scale={W}:{H}"
    if p.fx_sharpen and p.fx_sharpen > 0:
        vchain += f",unsharp=5:5:{p.fx_sharpen:.2f}:5:5:0.0"
    if p.fx_noise and p.fx_noise > 0:
        vchain += f",noise=alls={p.fx_noise}:allf=t+u"

    filter_parts = []
    if overlay_inputs:
        filter_parts.append(f"{vchain}[base]")
        aa = (f",colorchannelmixer=aa={cfg.overlay_opacity:.3f}"
              if 0.0 <= cfg.overlay_opacity < 1.0 else "")
        cur = "base"
        last = len(overlay_inputs) - 1
        for n, (idx, ow, oh, ox, oy) in enumerate(overlay_inputs):
            filter_parts.append(f"[{idx}:v]scale={ow}:{oh},format=rgba{aa}[ov{n}]")
            out = "vout" if n == last else f"t{n}"
            filter_parts.append(f"[{cur}][ov{n}]overlay={ox}:{oy}[{out}]")
            cur = out
    else:
        filter_parts.append(f"{vchain}[vout]")

    # ---- Аудио-цепочка ----
    audio_map = None
    if cfg.audio_mode == "mute":
        pass  # звук отключаем через -an
    elif cfg.audio_mode == "background" and audio_idx is not None:
        filter_parts.append(f"[{audio_idx}:a]volume={p.audio_volume:.3f}[aout]")
        audio_map = "[aout]"
    elif cfg.audio_mode == "keep" and keep_audio_possible:
        # atempo поддерживает 0.5-2.0 — наш диапазон 0.98-1.02 в норме.
        filter_parts.append(f"[0:a]atempo={p.speed:.5f}[aout]")
        audio_map = "[aout]"
    # keep без аудио в источнике -> просто без звука

    cmd += ["-filter_complex", ";".join(filter_parts)]
    cmd += ["-map", "[vout]"]

    if audio_map:
        cmd += ["-map", audio_map, "-c:a", cfg.audio_codec, "-b:a", cfg.audio_bitrate]
    else:
        cmd += ["-an"]

    # Кодек видео + очистка метаданных источника.
    cmd += [
        "-c:v", cfg.video_codec,
        "-preset", cfg.preset,
        "-crf", str(cfg.crf),
        "-pix_fmt", "yuv420p",
        "-map_metadata", "-1",
        "-movflags", "+faststart",
    ]

    # Уникальные теги на копию + убираем сигнатуру ffmpeg (bitexact -> нет encoder-тега).
    if cfg.randomize_metadata:
        cmd += [
            "-fflags", "+bitexact",
            "-flags:v", "+bitexact",
            "-flags:a", "+bitexact",
            "-metadata", f"creation_time={p.meta_time}",
            "-metadata", f"comment={p.meta_tag}",
            "-metadata:s:v", f"handler_name={p.meta_handler}",
        ]

    # -shortest, чтобы зацикленный фоновый трек обрезался по длине видео.
    if audio_map and cfg.audio_mode == "background":
        cmd += ["-shortest"]

    cmd += [str(dst)]
    return cmd


# ---------------------------------------------------------------------------
# ЗАДАЧА ДЛЯ ВОРКЕРА
# ---------------------------------------------------------------------------

@dataclass
class Task:
    src: Path
    dst: Path
    copy_index: int
    region_start: float
    region_dur: float
    keep_audio_possible: bool
    seed: int


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def process_task(args: Tuple[Config, Task,
                             List[Tuple[str, int, int]], List[str]]) -> Tuple[str, bool, str, str]:
    """
    Выполняется в отдельном процессе. Пикает параметры по seed, вызывает ffmpeg.
    Возвращает (имя_файла, успех, сообщение, md5). md5 = "" при ошибке.
    """
    cfg, task, overlays, audio_tracks = args
    rng = random.Random(task.seed)

    if task.dst.exists() and not cfg.overwrite:
        md5 = _file_md5(task.dst) if cfg.verify else ""
        return task.dst.name, True, "skip (exists)", md5

    try:
        params = build_params(task.region_start, task.region_dur, cfg,
                              overlays, audio_tracks, rng)
        cmd = build_ffmpeg_cmd(cfg, task.src, task.dst, params, task.keep_audio_possible)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            # Чистим неполный файл.
            if task.dst.exists():
                try:
                    task.dst.unlink()
                except OSError:
                    pass
            err = (proc.stderr or "").strip().splitlines()
            return task.dst.name, False, (err[-1] if err else f"ffmpeg rc={proc.returncode}"), ""

        if not task.dst.exists() or task.dst.stat().st_size == 0:
            return task.dst.name, False, "output empty", ""

        md5 = _file_md5(task.dst) if cfg.verify else ""
        return task.dst.name, True, "", md5
    except subprocess.TimeoutExpired:
        return task.dst.name, False, "timeout", ""
    except Exception as e:  # noqa: BLE001
        return task.dst.name, False, f"{type(e).__name__}: {e}", ""


# ---------------------------------------------------------------------------
# СБОР ЗАДАЧ И ОРКЕСТРАЦИЯ
# ---------------------------------------------------------------------------

def list_files(directory: Path, exts: set) -> List[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")
    )


def collect_overlays(cfg: Config) -> List[Tuple[str, int, int]]:
    """Список (путь, ширина, высота) для PNG-оверлеев."""
    if not cfg.overlay_enabled:
        return []
    result: List[Tuple[str, int, int]] = []
    for p in list_files(cfg.overlay_dir, {".png"}):
        dims = ffprobe_dimensions(cfg.ffprobe, p)
        if dims:
            result.append((str(p), dims[0], dims[1]))
        else:
            logging.warning("Пропущен оверлей (не удалось прочитать размеры): %s", p.name)
    return result


def build_task_list(cfg: Config, sources: List[Path]) -> List[Task]:
    tasks: List[Task] = []
    base_seed = cfg.seed if cfg.seed is not None else int(time.time())
    cwidth = max(2, len(str(cfg.copies_per_video)))

    for si, src in enumerate(sources):
        dur = ffprobe_duration(cfg.ffprobe, src)
        if not dur or dur < 0.2:
            logging.warning("Пропущено (не удалось определить длину / слишком коротко): %s", src.name)
            continue
        keep_audio = cfg.audio_mode == "keep" and has_audio_stream(cfg.ffprobe, src)

        # Определяем список областей (сегментов), внутри которых будут вырезаться копии.
        if cfg.segment_mode == "sequential" and dur > cfg.duration_range[1] + 0.5:
            seg_rng = random.Random((base_seed * 2_654_435_761 + si) & 0x7FFFFFFF)
            regions = compute_segments(dur, cfg, seg_rng)
        else:
            regions = [(0.0, dur)]  # одна область = весь ролик (режим "window")

        multi_seg = len(regions) > 1
        swidth = max(2, len(str(len(regions))))

        for seg_i, (rstart, rdur) in enumerate(regions):
            for ci in range(cfg.copies_per_video):
                if multi_seg:
                    name = f"{src.stem}_seg{seg_i + 1:0{swidth}d}_copy{ci + 1:0{cwidth}d}.mp4"
                else:
                    name = f"{src.stem}_copy{ci + 1:0{cwidth}d}.mp4"
                dst = cfg.output_dir / name
                seed = (base_seed * 1_000_003 + si * 9_973 + seg_i * 131 + ci) & 0x7FFFFFFF
                tasks.append(Task(
                    src=src, dst=dst, copy_index=ci,
                    region_start=rstart, region_dur=rdur,
                    keep_audio_possible=keep_audio, seed=seed,
                ))
    return tasks


def run(cfg: Config, progress_cb=None, log_cb=None, cancel_check=None) -> int:
    """
    Запуск обработки.

    progress_cb(done, total, ok, fail, skip) — вызывается после каждой готовой копии.
    log_cb(str)                              — строки лога (для GUI); дублируют logging.
    cancel_check() -> bool                   — если вернёт True, обработка мягко прерывается.
    """
    def emit(msg: str) -> None:
        logging.info(msg)
        if log_cb:
            log_cb(msg)

    def cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    cfg = resolve_binaries(cfg)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    sources = list_files(cfg.input_dir, VIDEO_EXTS)
    if not sources:
        emit(f"[ОШИБКА] В папке {cfg.input_dir} нет видеофайлов. Положите исходники и запустите снова.")
        return 1

    overlays = collect_overlays(cfg)
    audio_tracks = [str(p) for p in list_files(cfg.audio_dir, AUDIO_EXTS)]

    emit(f"Исходников: {len(sources)} | копий на видео: {cfg.copies_per_video} | "
         f"режим нарезки: {cfg.segment_mode} | оверлеев: {len(overlays)} | "
         f"треков: {len(audio_tracks)} | аудио: {cfg.audio_mode}")
    if cfg.audio_mode == "background" and not audio_tracks:
        emit(f"[ВНИМАНИЕ] audio=background, но в {cfg.audio_dir} нет треков — копии будут БЕЗ звука.")

    tasks = build_task_list(cfg, sources)
    if not tasks:
        emit("[ОШИБКА] Нет валидных задач для обработки.")
        return 1

    workers = cfg.workers if cfg.workers > 0 else max(1, (os.cpu_count() or 2) // 2)
    emit(f"Всего копий к созданию: {len(tasks)} | воркеров: {workers}")

    ok = fail = skip = 0
    done = 0
    total = len(tasks)
    failures: List[str] = []
    produced: List[Tuple[str, str, str]] = []   # (имя_источника, имя_копии, md5)
    payloads = [(cfg, t, overlays, audio_tracks) for t in tasks]
    use_tqdm = progress_cb is None

    start = time.time()
    pool = ProcessPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(process_task, pl): pl[1] for pl in payloads}
        bar = tqdm(total=len(futures), desc="Уникализация", unit="clip") if use_tqdm else None
        for fut in as_completed(futures):
            name, success, msg, md5 = fut.result()
            task = futures[fut]
            done += 1
            if success:
                if msg.startswith("skip"):
                    skip += 1
                else:
                    ok += 1
                if md5:
                    produced.append((task.src.name, name, md5))
            else:
                fail += 1
                failures.append(f"{name}: {msg}")
                logging.error("FAIL %s -> %s", name, msg)
                if log_cb:
                    log_cb(f"[ОШИБКА] {name}: {msg}")
            if bar is not None:
                bar.update(1)
                bar.set_postfix(ok=ok, fail=fail, skip=skip)
            if progress_cb:
                progress_cb(done, total, ok, fail, skip)

            if cancelled():
                emit("[ОТМЕНА] Останавливаю: отменяю оставшиеся задачи…")
                for f in futures:
                    f.cancel()
                break
        if bar is not None:
            bar.close()
    finally:
        # cancel_futures доступен с Python 3.9+
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        except TypeError:  # старые версии
            pool.shutdown(wait=True)

    elapsed = time.time() - start
    emit(f"Готово за {elapsed:.1f} c | успешно: {ok} | пропущено: {skip} | ошибок: {fail}")
    if failures:
        emit(f"Ошибок: {len(failures)} (подробности в {cfg.log_file})")

    unique_ok = True
    if cfg.verify and produced and not cancelled():
        unique_ok = verify_uniqueness(produced, emit)

    if cancelled():
        return 3
    if fail:
        return 2
    return 0 if unique_ok else 4


def verify_uniqueness(produced: List[Tuple[str, str, str]], emit) -> bool:
    """
    Проверяет, что все созданные копии уникальны по содержимому (MD5).
    Отдельно проверяет, что нет одинаковых копий ВНУТРИ одного источника.
    Возвращает True, если дубликатов нет.
    """
    from collections import defaultdict
    by_hash: dict = defaultdict(list)
    by_src: dict = defaultdict(list)
    for src_name, dst_name, h in produced:
        by_hash[h].append(dst_name)
        by_src[src_name].append(h)

    total = len(produced)
    unique = len(by_hash)
    dups = {h: names for h, names in by_hash.items() if len(names) > 1}

    emit("======== Проверка уникальности ========")
    emit(f"Проверено копий: {total} | уникальных по содержимому: {unique}")

    ok = True
    if dups:
        ok = False
        emit(f"[ВНИМАНИЕ] Найдены ИДЕНТИЧНЫЕ файлы — {len(dups)} групп:")
        for names in list(dups.values())[:10]:
            emit("   = " + ", ".join(names))
    bad_src = [s for s, hs in by_src.items() if len(set(hs)) != len(hs)]
    if bad_src:
        ok = False
        emit(f"[ВНИМАНИЕ] Дубликаты внутри источников: {', '.join(bad_src[:5])}")

    if ok:
        emit("[OK] Все копии УНИКАЛЬНЫ (нет совпадений по содержимому).")
    emit("=======================================")
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> Config:
    d = Config()  # значения по умолчанию
    ap = argparse.ArgumentParser(
        description="Массовая уникализация видео через FFmpeg (9:16, микро-изменения, оверлеи).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--input", type=Path, default=d.input_dir, help="Папка с исходными видео")
    ap.add_argument("--output", type=Path, default=d.output_dir, help="Папка для результата")
    ap.add_argument("--audio-dir", type=Path, default=d.audio_dir, help="Папка с фоновыми треками")
    ap.add_argument("--overlay-dir", type=Path, default=d.overlay_dir, help="Папка с PNG-оверлеями")

    ap.add_argument("--copies", type=int, default=d.copies_per_video,
                    help="Сколько уникальных копий делать из КАЖДОГО видео")

    ap.add_argument("--audio", choices=["mute", "background", "keep"], default=d.audio_mode,
                    help="mute — без звука; background — трек из audio_tracks/; keep — оригинал")
    ap.add_argument("--segment", choices=["window", "sequential"], default=d.segment_mode,
                    help="window — случайное окно 6-8с из ролика; "
                         "sequential — резать длинное видео на подряд идущие отрезки 6-8с")
    ap.add_argument("--no-overlay", action="store_true", help="Отключить наложение фейк-точек")
    ap.add_argument("--dots-min", type=int, default=d.overlay_count_range[0], help="Мин. точек на копию")
    ap.add_argument("--dots-max", type=int, default=d.overlay_count_range[1], help="Макс. точек на копию")
    ap.add_argument("--dots-opacity", type=float, default=d.overlay_opacity,
                    help="Прозрачность точек 0..1 (меньше = незаметнее)")

    ap.add_argument("--min-dur", type=float, default=d.duration_range[0], help="Мин. финальная длина, сек")
    ap.add_argument("--max-dur", type=float, default=d.duration_range[1], help="Макс. финальная длина, сек")

    ap.add_argument("--trim-min", type=float, default=d.trim_range[0], help="Мин. обрезка с края, сек")
    ap.add_argument("--trim-max", type=float, default=d.trim_range[1], help="Макс. обрезка с края, сек")
    ap.add_argument("--speed-min", type=float, default=d.speed_dev_range[0],
                    help="Мин. отклонение скорости (0.01 = ±1%%)")
    ap.add_argument("--speed-max", type=float, default=d.speed_dev_range[1],
                    help="Макс. отклонение скорости (0.1 = ±10%%)")

    ap.add_argument("--no-noise", action="store_true", help="Выключить эффект шума")
    ap.add_argument("--no-sharpen", action="store_true", help="Выключить эффект резкости")
    ap.add_argument("--no-zoom", action="store_true", help="Выключить эффект микро-зума")
    ap.add_argument("--noise-max", type=float, default=d.fx_noise_max, help="Макс. сила шума (0..30)")
    ap.add_argument("--sharpen-max", type=float, default=d.fx_sharpen_max, help="Макс. резкость (0..1.5)")
    ap.add_argument("--zoom-max", type=float, default=d.fx_zoom_max, help="Макс. микро-зум (0..0.1)")

    ap.add_argument("--width", type=int, default=d.out_width, help="Ширина кадра")
    ap.add_argument("--height", type=int, default=d.out_height, help="Высота кадра")
    ap.add_argument("--fps", type=int, default=d.target_fps, help="Целевой FPS (0 — оставить исходный)")

    ap.add_argument("--workers", type=int, default=d.workers, help="Число процессов (0 — авто: cpu/2)")
    ap.add_argument("--crf", type=int, default=d.crf, help="Качество x264 (меньше = лучше)")
    ap.add_argument("--preset", default=d.preset, help="Пресет x264")

    ap.add_argument("--seed", type=int, default=None, help="Seed для воспроизводимости")
    ap.add_argument("--overwrite", action="store_true", help="Перезаписывать существующие файлы")
    ap.add_argument("--no-metadata", action="store_true",
                    help="Не подставлять уникальные метаданные (creation_time/comment/handler)")
    ap.add_argument("--no-verify", action="store_true",
                    help="Не проверять уникальность копий после обработки")

    ap.add_argument("--ffmpeg", default=d.ffmpeg, help="Путь к ffmpeg")
    ap.add_argument("--ffprobe", default=d.ffprobe, help="Путь к ffprobe")

    a = ap.parse_args(argv)

    return Config(
        input_dir=a.input, output_dir=a.output, audio_dir=a.audio_dir, overlay_dir=a.overlay_dir,
        copies_per_video=max(1, a.copies),
        duration_range=(min(a.min_dur, a.max_dur), max(a.min_dur, a.max_dur)),
        trim_range=(min(a.trim_min, a.trim_max), max(a.trim_min, a.trim_max)),
        speed_dev_range=(min(a.speed_min, a.speed_max), max(a.speed_min, a.speed_max)),
        out_width=a.width, out_height=a.height,
        target_fps=(a.fps if a.fps and a.fps > 0 else None),
        audio_mode=a.audio,
        segment_mode=a.segment,
        overlay_enabled=not a.no_overlay,
        overlay_count_range=(min(a.dots_min, a.dots_max), max(a.dots_min, a.dots_max)),
        overlay_opacity=a.dots_opacity,
        fx_noise=not a.no_noise, fx_noise_max=a.noise_max,
        fx_sharpen=not a.no_sharpen, fx_sharpen_max=a.sharpen_max,
        fx_zoom=not a.no_zoom, fx_zoom_max=a.zoom_max,
        randomize_metadata=not a.no_metadata,
        crf=a.crf, preset=a.preset,
        workers=a.workers,
        seed=a.seed, overwrite=a.overwrite,
        verify=not a.no_verify,
        ffmpeg=a.ffmpeg, ffprobe=a.ffprobe,
    )


def main() -> None:
    cfg = parse_args()
    setup_logging(cfg.log_file)
    try:
        rc = run(cfg)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logging.warning("Прервано пользователем.")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # нужно для сборки в .exe (PyInstaller)
    main()
