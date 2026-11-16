# InfinityGrid Tabletop

A lightweight, 2D tactical grid mapping and virtual tabletop (VTT) application designed for tabletop role-playing games. Built with Python and PySide6, InfinityGrid Tabletop provides fast, responsive battle map management with dynamic fog of war, vector drawing, and multi-tier zooming.

## Features

* **Tactical Grid & Tokens:** Snap-to-grid token movement with customizable sizing and map layouts.
* **4-Tier Camera Zoom & Panning:** Smooth, multi-level zoom tiers paired with intuitive right-click camera panning.
* **Dynamic Vision & Fog of War:** Real-time line-of-sight and fog of war masking to keep unrevealed areas hidden from players.
* **Vector Drawing Tools:** Sketch terrain, spell areas of effect, walls, and quick notes directly onto the map canvas.

---

## Installation & Setup

All project scripts and asset folders are included in this repository. Follow the steps for your operating system below to install **Python 3** and **PySide6**, then launch the program.

### Windows Installation

1. **Download & Install Python 3:**
   * Go to [python.org/downloads](https://www.python.org/downloads/) and click the button to download the latest Python 3 installer for Windows.
   * Open the downloaded `.exe` installer.
   * **Important:** Check the box at the bottom labeled **"Add python.exe to PATH"**, then click **Install Now**.
   * *Verify:* Press the **Windows Key**, type `cmd`, and press **Enter** to open Command Prompt. Type `python --version` and press **Enter**—you should see a version number like `Python 3.14.x` or `3.15.x`.

2. **Download the Project Files:**
   * At the top of this GitHub repository, click the green **`<> Code`** button and select **Download ZIP**.
   * Right-click the downloaded `.zip` file, choose **Extract All...**, and extract it to a folder (e.g., on your Desktop).

3. **Install the PySide6 Library:**
   * In your Command Prompt window, type the following command and press **Enter**:
     ```cmd
     pip install PySide6
     ```
   * *Verify:* Once the download finishes, run `pyside6-uic --version`. Seeing a version number confirms PySide6 is ready.

4. **Launch the Application:**
   * Navigate into your extracted folder in Command Prompt (replace the path below with your actual folder path):
     ```cmd
     cd Desktop\InfinityGrid-Tabletop-main
     ```
   * Start the program:
     ```cmd
     python main.py
     ```
     *(Replace `main.py` with your primary startup script name if different).*

---

### macOS Installation

1. **Download & Install Python 3:**
   * Go to [python.org/downloads](https://www.python.org/downloads/) and download the latest macOS installer (`.pkg` file).
   * Double-click the `.pkg` file and follow the on-screen installer prompts.
   * *Verify:* Open **Terminal** (`Cmd + Space`, type `Terminal`, press **Enter**). Type `python3 --version` and press **Enter**—you should see `Python 3.14.x` or `3.15.x`.

2. **Download the Project Files:**
   * At the top of this GitHub repository, click the green **`<> Code`** button and select **Download ZIP**.
   * Double-click the downloaded `.zip` file in your **Downloads** folder to extract it.

3. **Install the PySide6 Library:**
   * In Terminal, run the following command to install PySide6:
     ```bash
     pip3 install PySide6
     ```
   * *Verify:* Run `python3 -c "import PySide6; print(PySide6.__version__)"`. If a version number prints without errors, the library is installed.

4. **Launch the Application:**
   * In Terminal, move into the extracted project folder:
     ```bash
     cd ~/Downloads/InfinityGrid-Tabletop-main
     ```
   * Start the program:
     ```bash
     python3 main.py
     ```
     *(Replace `main.py` with your primary startup script name if different).*

---

## Quick Controls

| Action | Control |
| :--- | :--- |
| **Pan Camera** | Hold `Right-Click` + Drag |
| **Zoom In / Out** | `Mouse Scroll Wheel` (4 zoom tiers) |
| **Move Token** | `Left-Click` + Drag |
| **Draw / Fog Tools** | Select from the toolbar menu |

## Disclamer
| :--- |
| "This Program is still a work in progress" |
| "Features are still coming" |
| "Suggestions are welcome" |
