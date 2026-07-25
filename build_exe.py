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
    """Ищет name.exe: сначала в PATH, затем в типичных местах рядом с проектом."""
    p = shutil.which(name)
    if p:
        return p
    search_roots = [HERE, HERE.parent, HERE.parent / "ffmpeg",
                    Path("C:/ffmpeg/bin"), Path("C:/ffmpeg")]
    for root in search_roots:
        if not root.exists():
            continue
        try:
            for c in root.rglob(f"{name}.exe"):
                return str(c)
        except OSError:
            continue
    return None


def main() -> int:
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
        "--onefile", "--noconsole", "--name", "VideoUniquifier",
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
        exe = HERE / "dist" / "VideoUniquifier.exe"
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
