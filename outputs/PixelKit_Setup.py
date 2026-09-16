from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


APP_NAME = "PixelKit"


def resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def install_root() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Programs" / APP_NAME


def powershell_quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(shortcut_path: Path, target: Path, icon_path: Path) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut({powershell_quote(shortcut_path)}); "
        f"$shortcut.TargetPath = {powershell_quote(target)}; "
        f"$shortcut.WorkingDirectory = {powershell_quote(target.parent)}; "
        f"$shortcut.IconLocation = {powershell_quote(str(icon_path) + ',0')}; "
        "$shortcut.Save()"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=flags,
    )


def install() -> Path:
    payload = resource_root() / "payload"
    destination = install_root()
    if not payload.exists():
        raise FileNotFoundError("В інсталяторі відсутні файли PixelKit.")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(payload, destination, dirs_exist_ok=True)

    executable = destination / "PixelKit.exe"
    icon = executable
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "PixelKit.lnk"
    start_menu = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "PixelKit.lnk"
    create_shortcut(desktop, executable, icon)
    create_shortcut(start_menu, executable, icon)
    return executable


def main() -> None:
    root = tk.Tk()
    root.title("Встановлення PixelKit")
    root.geometry("500x240")
    root.resizable(False, False)
    root.configure(bg="#0d1117")

    frame = tk.Frame(root, bg="#0d1117", padx=28, pady=24)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="PixelKit", bg="#0d1117", fg="#f4f7fb", font=("Segoe UI", 22, "bold")).pack(anchor="w")
    tk.Label(frame, text="Встановлення редактора зображень", bg="#0d1117", fg="#93a0b3", font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 18))
    status = tk.Label(frame, text="Підготовка…", bg="#0d1117", fg="#dce5f0", font=("Segoe UI", 10))
    status.pack(anchor="w")
    progress = ttk.Progressbar(frame, mode="indeterminate", length=440)
    progress.pack(fill="x", pady=(14, 0))
    progress.start(12)

    def run_install() -> None:
        try:
            status.configure(text="Копіюю файли програми…")
            root.update_idletasks()
            executable = install()
            progress.stop()
            progress.configure(mode="determinate", value=100)
            status.configure(text="PixelKit успішно встановлено")
            root.after(250, lambda: _finish(executable))
        except Exception as exc:
            progress.stop()
            status.configure(text="Встановлення не вдалося")
            messagebox.showerror("Помилка встановлення", str(exc), parent=root)

    def _finish(executable: Path) -> None:
        if messagebox.askyesno("PixelKit встановлено", "Створено ярлик на робочому столі.\n\nЗапустити PixelKit зараз?", parent=root):
            subprocess.Popen([str(executable)], cwd=str(executable.parent))
        root.destroy()

    root.after(100, run_install)
    root.mainloop()


if __name__ == "__main__":
    main()
