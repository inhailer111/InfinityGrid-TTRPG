"""Builds the Windows app: BattleMap-Portable.zip and, if Inno Setup is installed, Output/BattleMap-Setup.exe.

Run it on a Windows PC that has Python 3.10 or newer (python.org):
    double-click this file, or open Command Prompt in this folder and type:  py build_windows.py
The first run downloads about 300 MB. Later runs are quick.
"""
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)


def run(cmd):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def find_inno():
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
    for root in roots:
        if root:
            exe = Path(root) / "Inno Setup 6" / "ISCC.exe"
            if exe.exists():
                return exe
    return None


def main():
    print("Building Infinite Battle Map for Windows ...")
    for needed in ("battlemap.py", "worldgen.py"):
        if not (HERE / needed).exists():
            raise SystemExit(f"{needed} must be in the same folder as this script.")
    env = HERE / ".buildenv"
    if not env.exists():
        venv.create(env, with_pip=True)
    py = env / "Scripts" / "python.exe"
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "PySide6", "pyinstaller"])
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "BattleMap",
         "--hidden-import", "worldgen", "battlemap.py"])

    folder, out = HERE / "dist" / "BattleMap", HERE / "BattleMap-Portable.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(folder))
    print("Made", out.name)

    iscc = find_inno()
    if iscc and (HERE / "installer.iss").exists():
        run([iscc, "installer.iss"])
        print("Made Output\\BattleMap-Setup.exe")
    else:
        print("\nInno Setup 6 was not found, so no installer was made.")
        print("The zip still works: friends unzip it and double-click BattleMap.exe.")
        print("For a real installer, install Inno Setup 6 from jrsoftware.org and run this again.")
    print("\nFinished. Share Output\\BattleMap-Setup.exe if it exists, otherwise BattleMap-Portable.zip.")


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, SystemExit) as e:
        print("\nSomething went wrong:", e)
        print("Scroll up to see the message, and send it to whoever is helping you.")
    input("\nPress Enter to close this window.")
