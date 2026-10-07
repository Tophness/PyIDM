# PyIDM — High-Performance Download Manager & Media Grabber

**PyIDM** is an advanced, multi-threaded desktop download accelerator and multimedia stream capture engine built with Python and PyQt6. Designed with a lightweight footprint and maximum network throughput in mind, PyIDM features dynamic multi-part file segmentation, adaptive HLS/M3U8 stream assembly, granular bandwidth throttling, and native browser bridge integration with a real-time floating media overlay.

---

## 📸 Screenshots
<img width="1202" height="886" alt="1" src="https://github.com/user-attachments/assets/42ed75af-4a16-496f-8485-02720c590397" />
<img width="886" height="827" alt="2" src="https://github.com/user-attachments/assets/3b6df6ef-6a84-4dd0-8746-0813ccd1b1ca" />
<img width="886" height="827" alt="3" src="https://github.com/user-attachments/assets/839b47e3-ec6a-4296-837d-be8104d937a4" />
<img width="886" height="827" alt="4" src="https://github.com/user-attachments/assets/c58c9b81-111a-4c41-85f4-be00cd24d03b" />
<img width="886" height="827" alt="5" src="https://github.com/user-attachments/assets/a569c7ad-51a0-4a78-b608-8c41be0dc132" />
<img width="886" height="827" alt="6" src="https://github.com/user-attachments/assets/593953e9-e08f-4abe-a0bf-2f9d218f964f" />
<img width="886" height="827" alt="7" src="https://github.com/user-attachments/assets/242a31a1-6be7-42a9-bc54-4fa4b62fd76c" />
<img width="886" height="827" alt="8" src="https://github.com/user-attachments/assets/65e73ec9-9cd5-4335-bc65-2a4cf1b84236" />
<img width="886" height="827" alt="9" src="https://github.com/user-attachments/assets/ee5c328d-2d65-497b-bb6f-e689a7cbb27a" />
<img width="886" height="827" alt="10" src="https://github.com/user-attachments/assets/847b6dfd-6862-4d4c-92fa-9956cefb2e6e" />
<img width="886" height="827" alt="11" src="https://github.com/user-attachments/assets/c0b00466-dcf8-4f19-bc4b-1ec90aa32e9d" />
<img width="886" height="827" alt="12" src="https://github.com/user-attachments/assets/bdd1992e-6a6b-47e9-9f58-9dcd4631d268" />
<img width="927" height="689" alt="13" src="https://github.com/user-attachments/assets/7392532a-4726-40a4-8f29-d5f95641d701" />

---

## ✨ Features

### 🚀 Acceleration & Engine
* **Segmented Multi-Part Downloads**: Divides files into up to 32 concurrent byte-range connections (`Range: bytes=...`) for maximum transfer rates over high-latency networks.
* **Real-Time Visual Connection Map**: Live graphical segment visualizer displaying chunk allocation, download states, and socket activity.
* **Adaptive HLS/M3U8 Capture**: Parses `#EXTM3U` master playlists, enumerates bandwidth and resolution variants, asynchronously pools chunk downloads, and stitches them seamlessly on completion.
* **Pre-Allocation & Flexible IO**: Choose between writing directly to destination targets or assembling in dedicated temporary staging directories.

### 🌐 Browser Bridge & Media Interception
* **Local WebSocket Bridge**: High-speed, local bidirectional communication engine (`127.0.0.1:1001`) handling metadata, stream payloads, and intercepted requests.
* **Floating Video Download Panel**: Context-aware floating UI rendered directly above web video players with multi-stream quality selector support (resolutions, bitrates, audio/video split streams).
* **Win32 Window Tracking**: Utilizes native OS APIs (`GetWindowRect`, `ClientToScreen`, DPR normalization) to anchor overlays directly to browser viewports with zero flicker.
* **One-Click Extension Management**: Automated registry and policy provisioning for Chromium-based browsers (Chrome, Edge, Opera) and Gecko-based browsers (Firefox).

### 🛠️ Organization & Automation
* **Queue & Scheduler Engine**: Configure multi-queue pools (*Main Download Queue*, *Synchronization Queue*, or custom queues) with timed execution, auto-shutdown, and sleep/hibernate hooks.
* **Speed Limiter**: Fine-grained transfer throttling to manage bandwidth usage without degrading local network performance.
* **Smart File Categorization**: Automatic rule-based routing of incoming files into categorized directories (`Compressed`, `Video`, `Programs`, `Music`, `Documents`).
* **Conflict & Duplicate Management**: Configurable rules for duplicate URLs (prompting, auto-versioning/numbering, or overwriting).

### 🎨 User Interface & Ergonomics
* **Classic Desktop Interface**: Clear, uncluttered desktop UI with sorting, search (`Ctrl+F`), column reordering, and customization.
* **System Tray Minimization**: Background transfer execution with tray notifications and instant restore.
* **Dark Mode**: Native dark styling support for reduced eye strain.

---

## 🏗️ Architecture Overview

The application is structured into three primary decoupled modules:

```
├── main.py                   # Core GUI, download engine, worker threads, and server bridge
├── video_overlay.py          # Translucent, Win32-tracked floating download panel
├── browser_integration.py    # Registry handlers and extension installation logic
├── selectors.json            # Dynamic site matching rules and element selectors
└── settings.json             # Persistent application state and user configuration
```

1. **`DownloadWorker` (Multi-threading)**: Subclassed from `QRunnable` and managed via `QThreadPool`. Operates via atomic pause/resume events, socket connection pools, and thread-safe signal emissions.
2. **`WebSocketServerThread`**: Asynchronous `asyncio`/`websockets` daemon processing incoming addon messages, parsing payload headers, and dispatching stream metadata to the main UI thread.
3. **`VideoOverlay`**: Frameless, transparent `QWidget` with native Win32 window tracking to ensure accurate layout persistence across multi-monitor, high-DPI displays.

---

## 📦 Requirements

* **Operating System**: Windows 10 / 11 *(Windows required for native overlay tracking and browser registry registration)*
* **Python**: 3.10 or later

---

## 🚀 Getting Started

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Tophness/PyIDM
   cd pyidm
   pip install PyQt6 websockets
   ```

2. **Run the Application**:
   ```bash
   python main.py
   ```

3. **Configure Browser Integration**:
   * Open **Options** (`Options` button on the toolbar or via `Downloads -> Options`).
   * Navigate to the **General** tab.
   * Verify your installed browsers are checked, then click **Restart** to ensure registry extensions are installed.

---

## ⚙️ Configuration & Customization

* **Max Connections**: Tune parallel connections (1–32) based on connection bandwidth.
* **Download Panels**: Customize file extension capture triggers (MP4, MKV, TS, M3U8, etc.) and exclude specific domain names.
* **Speed Limiter**: Set default download speed caps per file.
* **Storage Paths**: Configure directory paths for specific categories and temporary file assembly.

---

## 📄 License

Distributed under the [MIT License](LICENSE). 
