#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_exe.py
============

Собирает автономный VideoUniquifier.exe через PyInstaller.

Автоматически:
  * вшивает video_uniquifier.py и generate_dots.py;
  * вшивает папку overlays/ (20 фейк-точек) — работают из коробки;
  * НАХОДИТ ffmpeg.exe/ffprobe.exe (в PATH или рядом) и ВСТРАИВАЕТ их в exe,
    чтобы получившийся файл не требовал установленного FFmpeg.

Если FFmpeg не найден — exe всё равно соберётся, но FFmpeg нужно будет
установить на целевом ПК (или указать путь в полях программы).

Запуск:  python build_exe.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEP = os.pathsep  # ';' на Windows — разделитель src/dst в --add-data


def find_binary(name: str) -> str | None:
    """
    Ищет name.exe. Приоритет:
      1) локальная папка ffmpeg_bin рядом со скриптом (если положили туда);
      2) PATH;
      3) поиск в типичных местах — из нескольких сборок берём САМУЮ ЛЁГКУЮ
         (essentials меньше full, папка exe получится компактнее).
    """
    local = HERE / "ffmpeg_bin" / f"{name}.exe"
    if local.exists():
        return str(local)

    p = shutil.which(name)
    if p:
        return p

    search_roots = [HERE, HERE.parent, HERE.parent / "ffmpeg",
                    Path("C:/ffmpeg/bin"), Path("C:/ffmpeg")]
    candidates = []
    for root in search_roots:
        if not root.exists():
            continue
        try:
            candidates.extend(root.rglob(f"{name}.exe"))
        except OSError:
            continue
    if not candidates:
        return None
    return str(min(candidates, key=lambda c: c.stat().st_size))


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Сборка VideoUniquifier.exe")
    ap.add_argument("--name", default="VideoUniquifier",
                    help="Имя exe (напр. VideoUniquifier_Lite)")
    opts = ap.parse_args()

    # Убедимся, что PyInstaller установлен.
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Устанавливаю PyInstaller…")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                        "pyinstaller", "tqdm", "Pillow"], check=True)

    ffmpeg = find_binary("ffmpeg")
    ffprobe = find_binary("ffprobe")

    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--noconsole", "--name", opts.name,
        "--add-data", f"{HERE / 'video_uniquifier.py'}{SEP}.",
        "--add-data", f"{HERE / 'generate_dots.py'}{SEP}.",
    ]

    overlays = HERE / "overlays"
    if overlays.exists():
        args += ["--add-data", f"{overlays}{SEP}overlays"]
        print(f"[+] Вшиваю точки: {overlays}")

    if ffmpeg and ffprobe:
        args += ["--add-binary", f"{ffmpeg}{SEP}.",
                 "--add-binary", f"{ffprobe}{SEP}."]
        print(f"[+] FFmpeg ВСТРОЕН в exe:\n      {ffmpeg}\n      {ffprobe}")
    else:
        print("[!] FFmpeg НЕ найден — exe соберётся без него.")
        print("    На целевом ПК нужно установить FFmpeg или указать путь в программе.")

    args += [str(HERE / "video_uniquifier_gui.py")]

    print("\nЗапускаю сборку…\n")
    r = subprocess.run(args)
    if r.returncode == 0:
        exe = HERE / "dist" / f"{opts.name}.exe"
        print("\n===========================================")
        print(f"ГОТОВО: {exe}")
        if ffmpeg and ffprobe:
            print("Это ОДИН автономный файл — можно кидать как есть.")
        else:
            print("Рядом с exe положите ffmpeg.exe и ffprobe.exe (или установите FFmpeg).")
        print("===========================================")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
