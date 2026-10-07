import sys
import os
import json
import re
import traceback
import asyncio
import websockets
import time
from datetime import datetime
from urllib.parse import unquote
from pathlib import Path
import urllib.parse
import urllib.request
import http.client
import ssl
import platform
import subprocess
import struct
import locale
import uuid
import threading
import concurrent.futures
from websockets.connection import State
import ctypes
from ctypes import wintypes
import gzip
import zlib
import shutil

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QProgressBar,
    QFileDialog, QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QDialogButtonBox, QCheckBox, QMenuBar, QLabel, QMenu, QTabWidget,
    QWidget, QTextEdit, QListWidget, QListWidgetItem, QInputDialog, QSplitter,
    QHeaderView, QComboBox, QSpinBox, QRadioButton, QButtonGroup, QGroupBox,
    QSystemTrayIcon, QStyle, QFrame, QSizePolicy, QToolBar, QToolButton,
    QTreeWidget, QTreeWidgetItem, QTimeEdit, QDateEdit, QFontDialog
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QRunnable, QThreadPool, Qt, QRect, QPoint, QTime, QDate, QSize
)
from PyQt6.QtGui import (
    QIcon, QAction, QGuiApplication, QCursor, QPainter, QColor, QBrush, QLinearGradient, QPen, QFont
)
from video_overlay import VideoOverlay
import browser_integration

APP_NAME = "PyIDM"
VERSION = "1.6.0"
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "settings.json"
DOWNLOADS_FILE = BASE_DIR / "downloads.json"
SELECTORS_FILE = BASE_DIR / "selectors.json"
LOG_FILE = BASE_DIR / "dumper_ui_log.txt"

IDM_NATIVE_HOST_EXECUTABLE = r"C:\Program Files (x86)\Internet Download Manager\IDMMsgHost.exe"
IDM_SERVICE_NAME = "IDMWFP"
ADDON_MANIFEST_VERSION = 3
CLIENT_ID = 12
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0"
PLATFORM_INFO = "Mozilla/5.0"
FAKE_ORIGIN = f"moz-extension://{uuid.uuid4()}"
IDM_VERSION_STRING = "v6.43b1 Full"
IDM_VERSION_INT = 103481600
DOCUMENT_JS_TOKENS = (
    "XMLHttpRequest|open|send|setRequestHeader|upload|loadend|responseText|"
    "fetch|headers|body|text|json|getReader|pipeThrough|Headers|accept|*/*|"
    "application/vnd.t1c|Response|url|get|tee|read|blob|arrayBuffer|bytes|"
    "getOwnPropertyDescriptor|defineProperty"
)

FILE_CATEGORIES = {
    "General": {"extensions": ["plj", "tif", "tiff"], "default_dir": str(Path.home() / "Downloads")},
    "Compressed": {"extensions": ["7z", "ace", "apk", "arj", "bin", "bz2", "gz", "gzip", "img", "iso", "lzh", "r0*", "r1*", "ra", "rar", "sea", "sit", "sitx", "tar", "z", "zip"], "default_dir": str(Path.home() / "Downloads" / "Compressed")},
    "Documents": {"extensions": ["pdf", "pps", "ppt", "doc", "docx", "pptx", "xls", "xlsx"], "default_dir": str(Path.home() / "Downloads" / "Documents")},
    "Music": {"extensions": ["aac", "aif", "m4a", "mp3", "mpa", "wav", "wma", "ram"], "default_dir": str(Path.home() / "Downloads" / "Music")},
    "Programs": {"extensions": ["exe", "msi", "msu"], "default_dir": str(Path.home() / "Downloads" / "Programs")},
    "Video": {"extensions": ["3gp", "asf", "avi", "mkv", "mov", "mp4", "m4v", "mpe", "mpeg", "mpg", "ogv", "ogg", "qt", "rm", "rmvb", "webm", "wmv", "flv", "ts", "tsv"], "default_dir": str(Path.home() / "Downloads" / "Video")}
}

DUPLICATE_ACTIONS = [
    "Show a dialog and ask what to do.",
    "Add the duplicate with a numbered file name",
    "Add the duplicate and overwrite the existing file",
    "If existing file is complete, show download complete dialog; otherwise resume it."
]

def get_firefox_client_info():
    user32 = ctypes.windll.user32
    target_hwnd = None
    
    fg = user32.GetForegroundWindow()
    if fg:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(fg, buf, 256)
        if buf.value == "MozillaWindowClass":
            target_hwnd = fg

    if not target_hwnd:
        def enum_windows_proc(hwnd, lparam):
            nonlocal target_hwnd
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, buf, 256)
                if buf.value == "MozillaWindowClass":
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    if (rect.right - rect.left) > 300 and (rect.bottom - rect.top) > 300:
                        target_hwnd = hwnd
                        return False
            return True

        EnumWindowsCallback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(EnumWindowsCallback(enum_windows_proc), 0)

    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0

    if not target_hwnd:
        geom = screen.geometry()
        return geom.x(), geom.y(), geom.width(), geom.height(), dpr, None

    rect = wintypes.RECT()
    user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(target_hwnd, ctypes.byref(pt))

    logical_x = int(pt.x / dpr)
    logical_y = int(pt.y / dpr)
    logical_w = int((rect.right - rect.left) / dpr)
    logical_h = int((rect.bottom - rect.top) / dpr)

    return logical_x, logical_y, logical_w, logical_h, dpr, target_hwnd

def clean_idm_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    pattern = re.compile(r'^(?:idmdwnlmfv[1234567890]://|idmreg://)+', re.IGNORECASE)
    while pattern.match(url):
        url = pattern.sub('', url)
    return url

def parse_hls_master_playlist(url, title, headers=None):
    url = clean_idm_url(url)
    variants = []
    log_to_file(f"[HLS PARSER] Starting parse for master playlist: {url}")
    try:
        req_headers = {'User-Agent': BROWSER_USER_AGENT, 'Accept': '*/*'}
        if headers:
            req_headers.update(headers)

        req_headers['Accept-Encoding'] = 'gzip, deflate'
            
        req = urllib.request.Request(url, headers=req_headers)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            raw_bytes = resp.read()
            encoding = resp.headers.get('Content-Encoding', '').lower()
            
            if encoding == 'gzip' or raw_bytes[:2] == b'\x1f\x8b':
                content = gzip.decompress(raw_bytes).decode('utf-8', errors='ignore')
            elif encoding == 'deflate':
                content = zlib.decompress(raw_bytes).decode('utf-8', errors='ignore')
            else:
                content = raw_bytes.decode('utf-8', errors='ignore')

        log_to_file(f"[HLS PARSER] Playlist fetch HTTP status: 200")
        log_to_file(f"[HLS PARSER] Raw playlist content preview (first 400 chars):\n{content[:400]}")

        if "#EXTM3U" not in content:
            log_to_file(f"[HLS PARSER WARNING] '#EXTM3U' tag missing from content. Aborting playlist parse.")
            return variants

        lines = content.splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
                bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                
                res_label = f"{res_match.group(2)}P" if res_match else "HD"
                bw_kbps = f"{int(bw_match.group(1)) // 1000} kbps" if bw_match else ""

                for next_line in lines[i+1:]:
                    next_line = next_line.strip()
                    if next_line and not next_line.startswith("#"):
                        variant_url = urllib.parse.urljoin(url, next_line)
                        v_item = {
                            'url': variant_url,
                            'title': title,
                            'label': f"TS {res_label}",
                            'ext': 'TS',
                            'resolution': res_label,
                            'bandwidth_str': bw_kbps
                        }
                        variants.append(v_item)
                        log_to_file(f"[HLS PARSER] Found variant: {v_item}")
                        break
        log_to_file(f"[HLS PARSER] Finished parse. Total variants found: {len(variants)}")
    except Exception as e:
        log_to_file(f"[HLS PARSER ERROR] Failed to parse master playlist {url}: {e}\n{traceback.format_exc()}")
    return variants

def parse_idm_message(message_str):
    if not message_str or not message_str.startswith("MSG#"): return None
    parsed = {'sequence': 0, 'type': 0, 'origin': 0, 'flags': 0, 'args': [], 'params': {}, 'raw': message_str}
    try:
        content = message_str.strip("MSG#;").rstrip(); parts = content.split(',', 1)
        header_and_args_part = parts[0]; params_part = parts[1] if len(parts) > 1 else ""
        header_part, args_part = header_and_args_part.split(':', 1) if ':' in header_and_args_part else (header_and_args_part, "")
        header_fields = header_part.split('#'); parsed.update(sequence=int(header_fields[0]), type=int(header_fields[1]), origin=int(header_fields[2]), flags=int(header_fields[3]))
        if args_part: parsed['args'] = [int(arg) if arg.lstrip('-').isdigit() else arg for arg in args_part.split(':')]
        if params_part:
            param_pairs = re.split(r',(?=\-?\d+=)', params_part)
            for pair in param_pairs:
                if '=' in pair:
                    key_str, value_part = pair.split('=', 1); key = int(key_str)
                    if ':' in value_part and not re.match(r'^https?://', value_part):
                        try: length_str, value = value_part.split(':', 1); int(length_str); parsed['params'][key] = value
                        except ValueError: parsed['params'][key] = value_part
                    else:
                        try: parsed['params'][key] = int(value_part) if value_part.lstrip('-').isdigit() else float(value_part)
                        except (ValueError, TypeError): parsed['params'][key] = value_part
    except Exception as e: log_to_file(f"[PARSER ERROR] Failed to parse message: '{message_str}'. Error: {e}\n{traceback.format_exc()}"); return None
    return parsed

def stop_idm_service():
    if sys.platform == 'win32':
        try:
            result = subprocess.run(["sc", "stop", IDM_SERVICE_NAME], check=False, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode == 0 or "has not been started" in result.stderr:
                return True
            else:
                log_to_file(f"Failed to stop '{IDM_SERVICE_NAME}' service. SC.EXE stderr: {result.stderr.strip()}")
                return False
        except FileNotFoundError:
            log_to_file("Warning: 'sc.exe' command not found. Cannot manage IDM service.")
        except Exception as e:
            log_to_file(f"An error occurred while trying to stop '{IDM_SERVICE_NAME}' service: {e}")
    return True

def start_idm_service():
    if sys.platform == 'win32':
        try:
            result = subprocess.run(["sc", "start", IDM_SERVICE_NAME], check=False, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode == 0 or "already been started" in result.stderr:
                return True
            else:
                log_to_file(f"Failed to start '{IDM_SERVICE_NAME}' service. SC.EXE stderr: {result.stderr.strip()}")
                return False
        except FileNotFoundError:
            log_to_file("Warning: 'sc.exe' command not found. Cannot manage IDM service.")
        except Exception as e:
            log_to_file(f"An error occurred while trying to start '{IDM_SERVICE_NAME}' service: {e}")
    return True

def log_to_file(content):
    try:
        log_message = f"[{datetime.now()}] {content}\n"
        with open(LOG_FILE, 'a', encoding='utf-8') as f: f.write(log_message)
    except Exception as e: print(f"Logging failed: {e}")

def format_bytes(size):
    if size is None or size <= 0: return "Unknown"
    power = 1024; n = 0; power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(power_labels) - 1: size /= power; n += 1
    return f"{size:.3f} {power_labels[n]}" if n >= 2 else f"{size:.1f} {power_labels[n]}"

def parse_http_headers(header_string):
    headers = {}
    if not header_string or not isinstance(header_string, str): return headers
    lines = header_string.replace('\r\n', '\n').strip().split('\n')
    for line in lines:
        if ':' in line: key, value = line.split(':', 1); headers[key.strip()] = value.strip()
    return headers

def get_unique_filename(path):
    if not path.exists(): return path
    parent, stem, ext = path.parent, path.stem, path.suffix; counter = 2
    while True:
        new_path = parent / f"{stem} ({counter}){ext}"
        if not new_path.exists(): return new_path
        counter += 1

class SettingsManager(QObject):
    def __init__(self):
        super().__init__()
        self.settings = {}
        self.load_settings()

    def get_default_auto_download_types(self):
        all_exts = set()
        for cat in FILE_CATEGORIES.values():
            all_exts.update([ext.upper() for ext in cat['extensions']])
        return sorted(list(all_exts))

    def load_settings(self):
        default_settings = {
            "save_paths": {cat: data["default_dir"] for cat, data in FILE_CATEGORIES.items()},
            "temp_dir": str(Path.home() / "Downloads" / "Temp"),
            "single_part_write_mode": "Save to temporary folder first, then move upon completion",
            "max_connections": 32,
            "confirm_exit_on_active": True,
            "auto_download_types": self.get_default_auto_download_types(),
            "dont_auto_download_sites": ["*.update.microsoft.com", "download.windowsupdate.com", "*.download.windowsupdate.com", "siteseal.thawte.com", "ecom.cimetz.com", "*.voice2page.com", "localhost"],
            "dont_auto_download_addresses": ["http://toolbar.live.com/static/sm/*.bin", "https://*.gvt1.com/edgedl/widevine-cdm/*win*.zip?*", "http://*.vkontakte.ru/*.zip"],
            "use_dialog_for_cancelled": True,
            "panel_file_types": [
                "M3U8", "M3U", "MPD", "FLV", "MP3", "MP4", "M4V", "F4V", "M4A", 
                "MPG", "MPEG", "AVI", "WMV", "WMA", "WAV", "ASF", "RM", "OGG", 
                "OGV", "MOV", "3GP", "QT", "WEBM", "TS", "MKV", "AAC", "VTT", 
                "TTML", "TTML2", "DFXP", "SRT"
            ],
            "dont_show_panel_sites": ["*.gstatic.com", "media.tenor.co", "media*.giphy.com"],
            "dont_capture_from_players": False,
            "image_file_types": ["CSS", "JS", "JSON", "CRL", "OCSP", "APNG", "GIF", "ICO", "JPEG", "JPG", "PNG", "SVG", "WEBP"],
            "zip_file_types": ["JSON", "GZ", "Z", "BZ2", "BIN", "ZIP", "GZIP", "LZH"],
            "launch_on_startup": False,
            "clipboard_monitoring": False,
            "change_folder_last_selected": True,
            "set_creation_date_from_server": False,
            "show_start_dialog": True,
            "queue_only": False,
            "show_complete_dialog": True,
            "start_immediately": True,
            "show_queue_on_download_later": True,
            "show_queue_on_batch_close": True,
            "ignore_time_changes_on_resume": False,
            "duplicate_action": DUPLICATE_ACTIONS[0],
            "manual_user_agent": BROWSER_USER_AGENT,
            "connection_exceptions": [],
            "download_limits_enabled": False,
            "download_limit_mbytes": 200,
            "download_limit_hours": 5,
            "show_limit_warning": True,
            "use_browser_proxy_on_error": True,
            "proxy_mode": "none",
            "pac_address": "",
            "manual_proxy_host": "",
            "manual_proxy_port": "",
            "manual_proxy_user": "",
            "manual_proxy_pass": "",
            "proxy_protocols": {"http": True, "https": True, "ftp": False},
            "ftp_pasv": False,
            "sites_logins": [],
            "sounds": {
                "Download complete": {"enabled": False, "file": ""},
                "Download failed": {"enabled": False, "file": ""},
                "Queue processing started": {"enabled": False, "file": ""},
                "Queue processing stopped/finished": {"enabled": False, "file": ""}
            },
            "captured_browsers": {
                "Apple Safari": True,
                "Google Chrome": True,
                "Internet Explorer": True,
                "Microsoft Edge": True,
                "Microsoft Edge Legacy": True,
                "Mozilla Firefox": True,
                "Opera": True
            },
            "custom_browsers": [],
            "special_keys": {
                "prevent_enabled": True,
                "prevent_alt": False,
                "prevent_shift": False,
                "prevent_ctrl": False,
                "prevent_del": True,
                "force_enabled": False,
                "force_alt": False,
                "force_shift": False,
                "force_ctrl": False,
                "force_ins": False,
                "force_check_lmb": False,
                "prevent_aux_downloads": True
            },
            "browser_context_menu": {
                "chrome_download_with_idm": True,
                "chrome_download_all_with_idm": True,
                "firefox_download_with_idm": True,
                "firefox_download_all_with_idm": True
            },
            "download_panels_config": {
                "web_players_view": "full",
                "file_types_table": [
                    {"type": "FLV", "min_size": "", "checked": True},
                    {"type": "MP3", "min_size": "50.00 KB", "checked": True},
                    {"type": "MP4", "min_size": "", "checked": True},
                    {"type": "M4A", "min_size": "", "checked": True},
                    {"type": "MPG", "min_size": "", "checked": True},
                    {"type": "MPEG", "min_size": "", "checked": True},
                    {"type": "AVI", "min_size": "", "checked": True},
                    {"type": "WMV", "min_size": "", "checked": True},
                    {"type": "WMA", "min_size": "", "checked": True},
                    {"type": "WAV", "min_size": "", "checked": True},
                    {"type": "ASF", "min_size": "", "checked": True},
                    {"type": "RM", "min_size": "", "checked": True},
                    {"type": "OGG", "min_size": "", "checked": True},
                    {"type": "OGV", "min_size": "", "checked": True},
                    {"type": "MOV", "min_size": "", "checked": True},
                    {"type": "3GP", "min_size": "", "checked": True},
                    {"type": "QT", "min_size": "", "checked": True},
                    {"type": "WEBM", "min_size": "", "checked": True},
                    {"type": "TS", "min_size": "", "checked": True},
                    {"type": "MKV", "min_size": "", "checked": True},
                    {"type": "AAC", "min_size": "", "checked": True}
                ],
                "show_for_protected": False,
                "exceptions": "*.gstatic.com media.tenor.co media*.giphy.com",
                "selected_files_view": "full",
                "selected_files_mode": "any",
                "selected_files_sites": [
                    "rapidshare.com",
                    "megaupload.com",
                    "depositfiles.com",
                    "filefactory.com",
                    "mediafire.com",
                    "sendspace.com",
                    "uploading.com"
                ]
            },
            "dial_up_vpn": {
                "enabled": False,
                "connection": "",
                "username": "",
                "password": "",
                "save_password": True,
                "redial_attempts": 3,
                "redial_interval": 5,
                "hang_up_when_done": False
            },
            "speed_limiter": {
                "enabled": False,
                "speed_kbps": 10,
                "always_turn_on_startup": False
            },
            "url_list_columns": {
                "visible": ["File Name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last Try Date", "Description"],
                "widths": {"File Name": 260, "Q": 30, "Size": 80, "Status": 90, "Time left": 80, "Transfer rate": 100, "Last Try Date": 120, "Description": 120}
            },
            "queues": {
                "Main download queue": {"concurrent": 1, "start_at_enabled": False, "start_time": "23:00:00", "stop_at_enabled": False, "stop_time": "07:30:00"},
                "Synchronization queue": {"concurrent": 4, "start_at_enabled": False, "start_time": "23:00:00", "stop_at_enabled": False, "stop_time": "07:30:00"}
            },
            "double_click_action": "properties",
            "dark_mode": False,
            "categories_visible": True,
            "toolbar_size": "large"
        }
        try:
            if not SETTINGS_FILE.exists():
                self.settings = default_settings
            else:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f: self.settings = json.load(f)
            for key, value in default_settings.items():
                if key not in self.settings: self.settings[key] = value
        except Exception as e:
            log_to_file(f"Error loading settings: {e}, reverting to defaults."); self.settings = default_settings
        finally:
            self.save_settings()

    def save_settings(self):
        try:
            for path in self.settings.get("save_paths", {}).values():
                os.makedirs(Path(path), exist_ok=True)
            if self.settings.get("temp_dir"):
                os.makedirs(Path(self.settings["temp_dir"]), exist_ok=True)
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e: log_to_file(f"Error saving settings: {e}")

    def get_category_for_filename(self, filename):
        ext_match = re.search(r'\.([^.\\/]+)$', filename); ext = ext_match.group(1).lower() if ext_match else ""
        if not ext: return "General"
        for category, data in FILE_CATEGORIES.items():
            if category != "General":
                for cat_ext in data["extensions"]:
                    if "*" in cat_ext and re.match(cat_ext.replace('*', '.*'), ext): return category
                    elif ext == cat_ext: return category
        return "General"

    def get_path_for_filename(self, filename):
        category = self.get_category_for_filename(filename)
        return self.settings.get("save_paths", {}).get(category, self.settings.get("save_paths", {}).get("General", str(Path.home() / "Downloads")))

    def build_config_message_params(self):
        params = {}
        param_sources = {
            "13": IDM_VERSION_STRING,
            "23": DOCUMENT_JS_TOKENS,
            "1":  self.settings.get('auto_download_types', []),
            "3":  self.settings.get('panel_file_types', []),
            "9":  self.settings.get('dont_auto_download_sites', []),
            "19": self.settings.get('dont_auto_download_addresses', []),
            "2":  self.settings.get('image_file_types', []),
            "4":  self.settings.get('zip_file_types', []),
            "10": "application/download|application/force-download|application/x-msdownload|application/octet-stream|binary/octet-stream|text/plain|text/html",
            "11": self.settings.get('dont_show_panel_sites', []),
            "17": "*://*.internetdownloadmanager.com/autoreg.html?*|*://*.internetdownloadmanager.com/fillregform.html?*",
            "20": "TXT|HTM|HTML|CSS|JS|APNG|GIF|ICO|JPEG|JPG|PNG|SVG|WEBP",
            "21": "/*-internal-*/**",
        }

        for key, value in param_sources.items():
            if isinstance(value, list):
                if value:
                    params[key] = "|".join(value)
            else:
                params[key] = str(value)
                
        return params

class DownloadWorkerSignals(QObject):
    progress = pyqtSignal(int, int, float)
    finished = pyqtSignal(int, str, str)
    error = pyqtSignal(int, str)
    file_info = pyqtSignal(int, str, int)
    connection_status = pyqtSignal(int, object)

class DownloadWorker(QRunnable):
    def __init__(self, row, url, final_save_path, headers, max_connections=32, temp_dir=None, is_background=False, write_mode="Save to temporary folder first, then move upon completion"):
        super().__init__()
        self.row = row
        self.url = clean_idm_url(url)
        self.final_save_path = Path(final_save_path)
        self.headers = headers or {}
        self.max_connections = max(1, int(max_connections))
        self.temp_dir = Path(temp_dir) if temp_dir else Path.home() / "Downloads" / "Temp"
        self.is_background = is_background
        self.write_mode = write_mode
        self._commit_event = threading.Event()
        if not self.is_background:
            self._commit_event.set()

        self.signals = DownloadWorkerSignals()
        self.is_cancelled = False
        self.is_paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._active_conns = []
        self._conns_lock = threading.Lock()
        self.speed_limit_enabled = False
        self.speed_limit_kbps = 0
        self.supports_ranges = False

    def commit_download(self, final_save_path=None):
        if final_save_path:
            self.final_save_path = Path(final_save_path)
        self.is_background = False
        self._commit_event.set()

    def set_speed_limit(self, enabled, kbps):
        self.speed_limit_enabled = enabled
        self.speed_limit_kbps = max(1, kbps)

    def pause(self):
        self.is_paused = True
        self._pause_event.clear()

    def resume(self):
        self.is_paused = False
        self._pause_event.set()

    def _register_conn(self, conn):
        with self._conns_lock: self._active_conns.append(conn)

    def _unregister_conn(self, conn):
        with self._conns_lock:
            if conn in self._active_conns: self._active_conns.remove(conn)

    def cancel(self):
        self.is_cancelled = True
        self._pause_event.set()
        self._commit_event.set()
        with self._conns_lock:
            for conn in self._active_conns:
                try: conn.close()
                except Exception: pass
            self._active_conns.clear()

    def _cleanup_files(self, paths):
        for p in paths:
            try:
                if p.exists(): os.remove(p)
            except OSError: pass

    def _throttle(self, chunk_len, active_workers=1):
        if self.speed_limit_enabled and self.speed_limit_kbps > 0:
            target_bps = (self.speed_limit_kbps * 1024) / max(1, active_workers)
            if target_bps > 0:
                expected_sec = chunk_len / target_bps
                time.sleep(min(0.5, expected_sec))

    def run(self):
        clean_url = self.url.split('?')[0].lower()
        if clean_url.endswith('.m3u8') or 'm3u8' in clean_url:
            self._download_hls()
        else:
            self._download_regular()

    def _resolve_and_check_ranges(self):
        current_url = self.url
        redirect_count, max_redirects = 0, 10
        req_headers = dict(self.headers)
        if 'User-Agent' not in req_headers: req_headers['User-Agent'] = BROWSER_USER_AGENT
        if 'Accept' not in req_headers: req_headers['Accept'] = '*/*'
        req_headers['Accept-Encoding'] = 'identity'
        if self.max_connections > 1: req_headers['Range'] = 'bytes=0-0'

        while redirect_count < max_redirects:
            if self.is_cancelled: return None, 0, False, None, None
            parsed = urllib.parse.urlparse(current_url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            path = parsed.path + ('?' + parsed.query if parsed.query else '')
            req_headers['Host'] = host
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30) if parsed.scheme == 'https' else http.client.HTTPConnection(host, port, timeout=30)
            self._register_conn(conn)
            conn.request("GET", path, headers=req_headers)
            r = conn.getresponse()

            if 300 <= r.status < 400:
                redirect_count += 1
                loc = r.getheader('Location')
                self._unregister_conn(conn); conn.close()
                if not loc: raise Exception("Redirect response without Location header.")
                current_url = urllib.parse.urljoin(current_url, loc)
                continue

            if r.status == 206:
                cr = r.getheader('Content-Range', '')
                match = re.search(r'/(\d+)', cr)
                self._unregister_conn(conn); conn.close()
                self.supports_ranges = True
                return current_url, int(match.group(1)) if match else 0, True, None, None

            if r.status == 200:
                total_size = int(r.getheader('Content-Length', 0))
                self.supports_ranges = False
                return current_url, total_size, False, r, conn

            if r.status in (400, 403, 416) and 'Range' in req_headers:
                del req_headers['Range']
                self._unregister_conn(conn); conn.close()
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30) if parsed.scheme == 'https' else http.client.HTTPConnection(host, port, timeout=30)
                self._register_conn(conn)
                conn.request("GET", path, headers=req_headers)
                r = conn.getresponse()
                if r.status == 200:
                    total_size = int(r.getheader('Content-Length', 0))
                    self.supports_ranges = False
                    return current_url, total_size, False, r, conn
                else:
                    self._unregister_conn(conn); conn.close()
                    raise Exception(f"Server returned status {r.status} {r.reason}")

            self._unregister_conn(conn); conn.close()
            raise Exception(f"Server returned non-200 status: {r.status} {r.reason}")

        raise Exception(f"Exceeded maximum redirects ({max_redirects})")

    def _download_regular(self):
        try:
            final_url, total_size, supports_ranges, initial_resp, initial_conn = self._resolve_and_check_ranges()
            if self.is_cancelled:
                if initial_conn: self._unregister_conn(initial_conn); initial_conn.close()
                self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                return

            self.signals.file_info.emit(self.row, self.final_save_path.name, total_size)
            os.makedirs(self.final_save_path.parent, exist_ok=True)
            num_conns = min(self.max_connections, max(1, total_size // (256 * 1024))) if (supports_ranges and total_size > 0) else 1

            if num_conns > 1 and supports_ranges:
                try:
                    self._download_multi_part(final_url, total_size, num_conns)
                except Exception as e:
                    log_to_file(f"Multi-part download failed ({e}), falling back to single connection...")
                    if self.is_cancelled: return
                    self._download_single(final_url, total_size)
            else:
                self._download_single(final_url, total_size, initial_resp, initial_conn)

        except Exception as e:
            self.signals.error.emit(self.row, str(e))
        finally:
            if self.is_cancelled and self.final_save_path.exists():
                try: os.remove(self.final_save_path)
                except OSError: pass

    def _download_single(self, final_url, total_size, initial_resp=None, initial_conn=None):
        conn = initial_conn
        resp = initial_resp
        target_path = None
        use_temp = self.is_background or (self.write_mode != "Write directly to destination file")

        try:
            if use_temp:
                os.makedirs(self.temp_dir, exist_ok=True)
                session_id = uuid.uuid4().hex[:8]
                safe_name = re.sub(r'[\\/*?:"<>|]', '_', self.final_save_path.name)
                target_path = self.temp_dir / f"{safe_name}_{session_id}.part"
            else:
                os.makedirs(self.final_save_path.parent, exist_ok=True)
                target_path = self.final_save_path

            if not resp:
                parsed = urllib.parse.urlparse(final_url)
                host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
                path = parsed.path + ('?' + parsed.query if parsed.query else '')
                req_headers = dict(self.headers)
                req_headers['Host'] = host
                if 'User-Agent' not in req_headers: req_headers['User-Agent'] = BROWSER_USER_AGENT
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30) if parsed.scheme == 'https' else http.client.HTTPConnection(host, port, timeout=30)
                self._register_conn(conn)
                conn.request("GET", path, headers=req_headers)
                resp = conn.getresponse()
                if resp.status != 200: raise Exception(f"Server returned status {resp.status} {resp.reason}")
                if total_size <= 0:
                    total_size = int(resp.getheader('Content-Length', 0))
                    self.signals.file_info.emit(self.row, self.final_save_path.name, total_size)

            downloaded_bytes = 0
            start_time = time.time()
            last_progress = start_time

            conn_info = [{'num': 1, 'start': 0, 'end': total_size, 'downloaded': 0, 'status': "Receiving data..."}]
            self.signals.connection_status.emit(self.row, conn_info)

            with open(target_path, 'wb') as f:
                while True:
                    if self.is_cancelled:
                        self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                        return
                    self._pause_event.wait()
                    chunk = resp.read(16384)
                    if not chunk: break
                    f.write(chunk)
                    downloaded_bytes += len(chunk)
                    self._throttle(len(chunk), 1)

                    now = time.time()
                    if now - last_progress >= 0.25:
                        last_progress = now
                        elapsed = now - start_time
                        speed_mbps = ((downloaded_bytes / elapsed) * 8) / (1024 * 1024) if elapsed > 0 else 0
                        self.signals.progress.emit(self.row, downloaded_bytes, speed_mbps)
                        conn_info[0]['downloaded'] = downloaded_bytes
                        self.signals.connection_status.emit(self.row, conn_info)

            if self.is_background:
                self._commit_event.wait()
                if self.is_cancelled:
                    if target_path and target_path.exists():
                        try: os.remove(target_path)
                        except OSError: pass
                    self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                    return

            if use_temp and target_path and target_path.exists():
                os.makedirs(self.final_save_path.parent, exist_ok=True)
                shutil.move(str(target_path), str(self.final_save_path))

            conn_info[0]['status'] = "Disconnect."
            self.signals.connection_status.emit(self.row, conn_info)
            final_elapsed = time.time() - start_time
            final_speed = ((downloaded_bytes / final_elapsed) * 8) / (1024 * 1024) if final_elapsed > 0 else 0
            self.signals.progress.emit(self.row, downloaded_bytes, final_speed)
            self.signals.finished.emit(self.row, "Completed", str(self.final_save_path))
        finally:
            if conn:
                self._unregister_conn(conn)
                try: conn.close()
                except Exception: pass
            if self.is_cancelled and target_path and target_path.exists():
                try: os.remove(target_path)
                except OSError: pass

    def _download_multi_part(self, final_url, total_size, num_conns):
        os.makedirs(self.temp_dir, exist_ok=True)
        session_id = uuid.uuid4().hex[:8]
        safe_name = re.sub(r'[\\/*?:"<>|]', '_', self.final_save_path.name)
        part_files = [self.temp_dir / f"{safe_name}_{session_id}.part{i}" for i in range(num_conns)]

        part_size = total_size // num_conns
        byte_ranges = []
        conn_info = []
        for i in range(num_conns):
            start = i * part_size
            end = total_size - 1 if i == num_conns - 1 else (i + 1) * part_size - 1
            byte_ranges.append((start, end))
            conn_info.append({'num': i + 1, 'start': start, 'end': end, 'downloaded': 0, 'status': "Connecting..."})

        self.signals.connection_status.emit(self.row, conn_info)
        downloaded_per_part = [0] * num_conns
        start_time = time.time()
        lock = threading.Lock()
        last_progress_time = [start_time]
        has_error = [None]

        def _worker(part_idx, start_byte, end_byte, part_path):
            if self.is_cancelled or has_error[0]: return
            parsed = urllib.parse.urlparse(final_url)
            host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
            path = parsed.path + ('?' + parsed.query if parsed.query else '')

            part_headers = dict(self.headers)
            part_headers['Host'] = host
            part_headers['Range'] = f"bytes={start_byte}-{end_byte}"
            part_headers['Accept-Encoding'] = 'identity'
            if 'User-Agent' not in part_headers: part_headers['User-Agent'] = BROWSER_USER_AGENT

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = None

            try:
                with lock: conn_info[part_idx]['status'] = "Send GET..."
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30) if parsed.scheme == 'https' else http.client.HTTPConnection(host, port, timeout=30)
                self._register_conn(conn)
                conn.request("GET", path, headers=part_headers)
                resp = conn.getresponse()
                if resp.status not in (200, 206):
                    raise Exception(f"Part {part_idx + 1} failed: HTTP {resp.status} {resp.reason}")

                with lock: conn_info[part_idx]['status'] = "Receiving data..."

                with open(part_path, 'wb') as pf:
                    while not self.is_cancelled and not has_error[0]:
                        self._pause_event.wait()
                        chunk = resp.read(16384)
                        if not chunk: break
                        pf.write(chunk)
                        self._throttle(len(chunk), num_conns)
                        with lock:
                            downloaded_per_part[part_idx] += len(chunk)
                            conn_info[part_idx]['downloaded'] = downloaded_per_part[part_idx]
                            now = time.time()
                            if now - last_progress_time[0] >= 0.25:
                                last_progress_time[0] = now
                                total_downloaded = sum(downloaded_per_part)
                                elapsed = now - start_time
                                speed_mbps = ((total_downloaded / elapsed) * 8) / (1024 * 1024) if elapsed > 0 else 0
                                self.signals.progress.emit(self.row, total_downloaded, speed_mbps)
                                self.signals.connection_status.emit(self.row, list(conn_info))
                with lock: conn_info[part_idx]['status'] = "Disconnect."
            except Exception as e:
                with lock:
                    conn_info[part_idx]['status'] = "Error"
                    if not has_error[0] and not self.is_cancelled: has_error[0] = e
            finally:
                if conn:
                    self._unregister_conn(conn)
                    try: conn.close()
                    except Exception: pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_conns) as executor:
            futures = [executor.submit(_worker, i, byte_ranges[i][0], byte_ranges[i][1], part_files[i]) for i in range(num_conns)]
            concurrent.futures.wait(futures)

        if self.is_cancelled:
            self._cleanup_files(part_files)
            self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
            return

        if has_error[0]:
            self._cleanup_files(part_files)
            raise has_error[0]

        if self.is_background:
            self._commit_event.wait()
            if self.is_cancelled:
                self._cleanup_files(part_files)
                self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                return

        os.makedirs(self.final_save_path.parent, exist_ok=True)
        with open(self.final_save_path, 'wb') as outfile:
            for part_path in part_files:
                if self.is_cancelled:
                    self._cleanup_files(part_files)
                    self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                    return
                if part_path.exists():
                    with open(part_path, 'rb') as infile:
                        while True:
                            buf = infile.read(65536)
                            if not buf: break
                            outfile.write(buf)
                    try: os.remove(part_path)
                    except OSError: pass

        self.signals.connection_status.emit(self.row, conn_info)
        final_elapsed = time.time() - start_time
        final_speed = ((total_size / final_elapsed) * 8) / (1024 * 1024) if final_elapsed > 0 else 0
        self.signals.progress.emit(self.row, total_size, final_speed)
        self.signals.finished.emit(self.row, "Completed", str(self.final_save_path))

    def _download_hls(self):
        seg_files = []
        try:
            self.supports_ranges = True
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req_headers = dict(self.headers or {'User-Agent': BROWSER_USER_AGENT})
            if 'User-Agent' not in req_headers: req_headers['User-Agent'] = BROWSER_USER_AGENT
            if 'Accept' not in req_headers: req_headers['Accept'] = '*/*'
            req_headers['Accept-Encoding'] = 'gzip, deflate'

            def _fetch_and_decompress(request_obj):
                with urllib.request.urlopen(request_obj, timeout=15, context=ctx) as resp:
                    raw = resp.read()
                    enc = resp.headers.get('Content-Encoding', '').lower()
                    if enc == 'gzip' or raw[:2] == b'\x1f\x8b':
                        return gzip.decompress(raw).decode('utf-8', errors='ignore')
                    elif enc == 'deflate':
                        return zlib.decompress(raw).decode('utf-8', errors='ignore')
                    return raw.decode('utf-8', errors='ignore')

            try:
                req = urllib.request.Request(self.url, headers=req_headers)
                playlist_text = _fetch_and_decompress(req)
            except urllib.error.HTTPError as he:
                if he.code == 403 and 'Referer' in req_headers:
                    retry_headers = dict(req_headers)
                    del retry_headers['Referer']
                    req = urllib.request.Request(self.url, headers=retry_headers)
                    playlist_text = _fetch_and_decompress(req)
                else:
                    raise

            segment_urls = [urllib.parse.urljoin(self.url, l.strip()) for l in playlist_text.splitlines() if l.strip() and not l.startswith('#')]
            total_chunks = len(segment_urls)
            if total_chunks == 0: raise Exception("No video segment chunks found in HLS playlist.")

            os.makedirs(self.temp_dir, exist_ok=True)
            self.signals.file_info.emit(self.row, self.final_save_path.name, 0)

            session_id = uuid.uuid4().hex[:8]
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', self.final_save_path.name)
            seg_files = [self.temp_dir / f"{safe_name}_{session_id}_{i:05d}.tmp" for i in range(total_chunks)]

            num_workers = min(self.max_connections, total_chunks)
            conn_table_info = [{'num': i + 1, 'downloaded': 0, 'status': "Connecting..."} for i in range(num_workers)]
            chunk_states = [{'chunk_status': 'pending'} for _ in range(total_chunks)]
            self.signals.connection_status.emit(self.row, {
                'connections': list(conn_table_info),
                'chunks': list(chunk_states)
            })

            downloaded_bytes = 0
            completed_chunks = 0
            self.current_pct = 0.0
            start_time = time.time()
            lock = threading.Lock()
            last_progress_time = [start_time]
            has_error = [None]

            def _download_segment(idx, seg_url, seg_path):
                if self.is_cancelled or has_error[0]: return
                slot = idx % num_workers
                max_retries = 3

                for attempt in range(max_retries):
                    if self.is_cancelled or has_error[0]: return
                    conn = None
                    try:
                        with lock:
                            chunk_states[idx]['chunk_status'] = 'downloading'
                            conn_table_info[slot]['status'] = "Receiving data..."

                        seg_headers = dict(self.headers or {'User-Agent': BROWSER_USER_AGENT})
                        if 'User-Agent' not in seg_headers: seg_headers['User-Agent'] = BROWSER_USER_AGENT

                        try:
                            seg_req = urllib.request.Request(seg_url, headers=seg_headers)
                            conn = urllib.request.urlopen(seg_req, timeout=20, context=ctx)
                        except urllib.error.HTTPError as he:
                            if he.code == 403 and 'Referer' in seg_headers:
                                no_ref = dict(seg_headers); del no_ref['Referer']
                                seg_req = urllib.request.Request(seg_url, headers=no_ref)
                                conn = urllib.request.urlopen(seg_req, timeout=20, context=ctx)
                            else:
                                raise

                        self._register_conn(conn)
                        content_buf = bytearray()
                        while not self.is_cancelled and not has_error[0]:
                            self._pause_event.wait()
                            chunk = conn.read(16384)
                            if not chunk: break
                            content_buf.extend(chunk)
                            self._throttle(len(chunk), num_workers)
                            with lock:
                                nonlocal downloaded_bytes
                                downloaded_bytes += len(chunk)
                                conn_table_info[slot]['downloaded'] += len(chunk)

                        if content_buf.startswith(b'<!DOCTYPE') or content_buf.startswith(b'<html'):
                            raise Exception("Received HTML error block instead of video stream.")

                        with open(seg_path, 'wb') as sf:
                            sf.write(content_buf)

                        with lock:
                            nonlocal completed_chunks
                            completed_chunks += 1
                            chunk_states[idx]['chunk_status'] = 'done'
                            self.current_pct = (completed_chunks / total_chunks) * 100.0

                            now = time.time()
                            if now - last_progress_time[0] >= 0.25:
                                last_progress_time[0] = now
                                elapsed = now - start_time
                                speed_mbps = ((downloaded_bytes / elapsed) * 8) / (1024 * 1024) if elapsed > 0 else 0
                                self.signals.progress.emit(self.row, downloaded_bytes, speed_mbps)
                                self.signals.connection_status.emit(self.row, {
                                    'connections': list(conn_table_info),
                                    'chunks': list(chunk_states)
                                })
                        return

                    except Exception as e:
                        if conn:
                            self._unregister_conn(conn)
                            try: conn.close()
                            except Exception: pass
                        if attempt < max_retries - 1 and not self.is_cancelled:
                            time.sleep(0.5)
                            continue
                        else:
                            with lock:
                                conn_table_info[slot]['status'] = "Error"
                                if not has_error[0] and not self.is_cancelled:
                                    has_error[0] = e
                    finally:
                        if conn:
                            self._unregister_conn(conn)
                            try: conn.close()
                            except Exception: pass

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(_download_segment, i, url, seg_files[i]) for i, url in enumerate(segment_urls)]
                concurrent.futures.wait(futures)

            if self.is_cancelled:
                self._cleanup_files(seg_files)
                self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                return

            if has_error[0]:
                self._cleanup_files(seg_files)
                raise has_error[0]

            if self.is_background:
                self._commit_event.wait()
                if self.is_cancelled:
                    self._cleanup_files(seg_files)
                    self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                    return

            os.makedirs(self.final_save_path.parent, exist_ok=True)
            with open(self.final_save_path, 'wb') as final_f:
                for seg_path in seg_files:
                    if self.is_cancelled:
                        self._cleanup_files(seg_files)
                        if self.final_save_path.exists():
                            try: os.remove(self.final_save_path)
                            except OSError: pass
                        self.signals.finished.emit(self.row, "Cancelled", str(self.final_save_path))
                        return
                    if seg_path.exists():
                        with open(seg_path, 'rb') as sf:
                            while True:
                                chunk = sf.read(65536)
                                if not chunk: break
                                final_f.write(chunk)
                        try: os.remove(seg_path)
                        except OSError: pass

            self._cleanup_files(seg_files)
            final_elapsed = time.time() - start_time
            final_speed = ((downloaded_bytes / final_elapsed) * 8) / (1024 * 1024) if final_elapsed > 0 else 0
            self.current_pct = 100.0
            self.signals.progress.emit(self.row, downloaded_bytes, final_speed)
            self.signals.finished.emit(self.row, "Completed", str(self.final_save_path))

        except Exception as e:
            self._cleanup_files(seg_files)
            if self.final_save_path.exists():
                try: os.remove(self.final_save_path)
                except OSError: pass
            self.signals.error.emit(self.row, str(e))

class WebSocketServerThread(QThread):
    title_detected = pyqtSignal(str, str)
    tab_switched = pyqtSignal(str, int)
    video_rect_received = pyqtSignal(int, int, int, int, float)
    download_request_received = pyqtSignal(dict)
    video_detected = pyqtSignal(dict)
    video_stream_detected = pyqtSignal(dict)

    mode = 2

    def __init__(self, settings_manager, host="localhost", port=1001):
        super().__init__()
        self.settings_manager = settings_manager
        self.host, self.port, self.sequence_counter = host, port, 0
        self.loop, self.server = None, None

        self.video_context = {}
        self.graphql_data_store = {}
        self.pending_type13_messages = {}
        self.pending_video_chunks = {}
        self.initial_handshake_complete = asyncio.Event()

    async def _process_type13_message_mode0(self, websocket, parsed_message):
        args = parsed_message.get('args', [])
        params = parsed_message.get('params', {})
        tab_id_str = str(args[6]) if len(args) > 6 else "-1"
        element_ids = self.video_context.get(tab_id_str, [])
        
        if not element_ids:
            log_to_file(f"ERROR: Tried to process Type 13 for tab {tab_id_str}, but no element IDs were found in context.")
            self.video_stream_detected.emit(parsed_message)
            return

        self.video_stream_detected.emit(parsed_message)
        for element_id in element_ids:
            download_category = args[5]
            candidate_url = clean_idm_url(params.get(6))
            
            if download_category in [2, 3, 4] and candidate_url and ('.mp4' in candidate_url or '.m3u8' in candidate_url):
                log_to_file(f"-> Processing queued Type 13 (Cat {download_category}) for element {element_id} on tab {tab_id_str}.")
                response_args = [self.get_next_sequence(), 0, 0, int(time.time() * 1000), 1, 4, int(tab_id_str), 0, element_id]
                response_params = {
                    4: 'MP4', 6: candidate_url, 7: params.get(7),
                    31: 'Video - MP4', 50: params.get(50), 8: 5243140,
                    109: 1, 134: params.get(134, "unknown_video_id")
                }
                response_msg = self.build_idm_message(12, 2, 33, response_args, response_params)
                await self.send_and_log(websocket, response_msg)

    async def handle_video_panel_request(self, websocket, parsed_message):
        try:
            self.video_stream_detected.emit(parsed_message)
            args = parsed_message.get('args', [])
            params = parsed_message.get('params', {})

            if len(args) < 8: return
            tab_id = args[6]
            frame_id = args[7]
            candidate_url = clean_idm_url(params.get(6))
            page_url = clean_idm_url(params.get(7) or params.get(50))
            referrer_url = clean_idm_url(params.get(50) or params.get(7))

            if not candidate_url: return

            file_ext_match = re.search(r'\.([^./?]+)', candidate_url.split('?')[0])
            file_ext = file_ext_match.group(1).upper() if file_ext_match else "VIDEO"

            response_args = [
                parsed_message.get('sequence', self.get_next_sequence()),
                0, 0, int(time.time() * 1000), 1, 4, tab_id, frame_id, 0
            ]

            response_params = {
                4: file_ext,
                6: candidate_url,
                7: page_url,
                8: 1048836,
                16: 3,
                50: referrer_url,
                109: 1
            }

            response_msg = self.build_idm_message(12, 2, 33, response_args, response_params)
            await self.send_and_log(websocket, response_msg)
        except Exception as e:
            log_to_file(f"[PANEL HANDLER] Error: {e}\n{traceback.format_exc()}")

    async def _handle_facebook_video(self, websocket, tab_id_str, parsed_chunk):
        context_for_tab = self.video_context.get(tab_id_str)
        graphql_json = self.graphql_data_store.get(tab_id_str)

        if not context_for_tab or not graphql_json:
            return

        video_list = self.parse_facebook_graphql(graphql_json)
        if not video_list:
            return

        log_to_file(f"-> Distributing {len(video_list)} GraphQL download options to {len(context_for_tab)} elements on tab {tab_id_str}.")

        for video_element_id in context_for_tab:
            for video_info in video_list:
                template_params = parsed_chunk.get('params', {})
                response_args = [self.get_next_sequence(), 0, 0, int(time.time() * 1000), 1, 4, int(tab_id_str), 0, video_element_id]
                response_params = {
                    4: 'MP4', 6: video_info["url"], 7: template_params.get(7),
                    31: f"{video_info['quality']} - MP4", 50: template_params.get(50), 8: 5243140,
                    109: 1, 134: template_params.get(134, "unknown_video_id")
                }
                response_msg = self.build_idm_message(12, 2, 33, response_args, response_params)
                await self.send_and_log(websocket, response_msg)

        if tab_id_str in self.graphql_data_store: del self.graphql_data_store[tab_id_str]
        if tab_id_str in self.video_context: del self.video_context[tab_id_str]
        if tab_id_str in self.pending_video_chunks: del self.pending_video_chunks[tab_id_str]

    async def _process_pending_chunks(self, websocket, tab_id_str):
        if not (tab_id_str in self.pending_video_chunks and
                tab_id_str in self.graphql_data_store and
                tab_id_str in self.video_context):
            return

        video_list = self.parse_facebook_graphql(self.graphql_data_store[tab_id_str])
        if not video_list: return

        log_to_file(f"-> CONTEXT COMPLETE for tab '{tab_id_str}'. Processing {len(self.pending_video_chunks[tab_id_str])} pending chunk(s).")
        chunks_to_process = self.pending_video_chunks.pop(tab_id_str, [])
        for parsed_chunk in chunks_to_process:
            self.video_stream_detected.emit(parsed_chunk)
            await self._handle_facebook_video(websocket, tab_id_str, parsed_chunk)

    def parse_facebook_graphql(self, json_string):
        urls = []
        try:
            playable_urls = re.findall(r'"playable_url(?:_quality_hd)?":"(https?://[^"]+)"', json_string)
            progressive_urls = re.findall(r'"progressive_url":"(https?://[^"]+)"', json_string)
            all_found_urls = set(playable_urls + progressive_urls)
            seen_urls = set()
            for url in all_found_urls:
                url = url.replace('\\/', '/')
                if url not in seen_urls:
                    quality = "HD" if "hd" in url.lower() or "720" in url or "1080" in url else "SD"
                    urls.append({"url": url, "quality": quality})
                    seen_urls.add(url)
            urls.sort(key=lambda x: x['quality'], reverse=True)
            return urls
        except Exception as e:
            log_to_file(f"ERROR: General error in GraphQL parser: {e}"); return []

    def get_next_sequence(self): self.sequence_counter += 1; return self.sequence_counter

    async def send_and_log(self, websocket, message_str, is_large_payload=False):
        if websocket.state == State.CLOSED: log_to_file("Could not send message, connection was closed."); return
        try:
            if message_str.startswith("MSG#") and '#12#' in message_str:
                log_to_file(f"[SERVER-SEND] >>> Sending Type 12 Candidate.")
            await websocket.send(message_str.encode('latin-1'))
        except websockets.exceptions.ConnectionClosed as e: log_to_file(f"Connection closed during send: {e}")
        except Exception as e: log_to_file(f"Error during send: {e}\n{traceback.format_exc()}")

    def build_idm_message(self, msg_type, origin, flags, args=None, params=None):
        seq = self.get_next_sequence(); header = f"MSG#{seq}#{msg_type}#{origin}#{flags}"; message_parts = [header]
        if args: message_parts.append(":" + ":".join(map(str, args)))
        if params:
            param_strings = []
            for key, value in params.items():
                if isinstance(value, str): param_strings.append(f"{key}={len(value)}:{value}")
                elif isinstance(value, (int, float)): param_strings.append(f"{key}={value}")
                else: str_val = str(value); param_strings.append(f"{key}={len(str_val)}:{str_val}")
            message_parts.append("," + ",".join(param_strings))
        final_message = "".join(message_parts)
        message_parts.append(";"); return "".join(message_parts)

    async def send_initial_config(self, websocket):
        log_to_file("SENDER TASK: Starting initial configuration send.")
        handshake_response_msg = self.build_idm_message(
            3, 4, 0,
            [112, 94, 338, 87, 120, IDM_VERSION_INT, 12, 8468, 100],
            {"13": IDM_VERSION_STRING}
        )
        await self.send_and_log(websocket, handshake_response_msg)
        
        try:
            await asyncio.wait_for(self.initial_handshake_complete.wait(), timeout=2.5)
        except asyncio.TimeoutError:
            log_to_file("SENDER TASK: Handshake ack wait timed out, continuing...")

        if SELECTORS_FILE.exists():
            with open(SELECTORS_FILE, 'r', encoding='utf-8') as f: 
                selector_rules = json.load(f)
            for rule in selector_rules:
                if rule.get("enabled", True):
                    rule_args = [rule.get("id", 0), (1 if rule.get("id", 0) == 7 else 0)]
                    if 'params' in rule:
                        msg = self.build_idm_message(18, 2, 0, rule_args, rule.get("params", {}))
                        await self.send_and_log(websocket, msg, is_large_payload=len(msg) > 1000)
        else: 
            log_to_file(f"SENDER TASK: WARNING: '{SELECTORS_FILE.name}' not found.")
            
        config_params = self.settings_manager.build_config_message_params()
        config_msg = self.build_idm_message(4, 5, 352, [3, 6, IDM_VERSION_INT, 0], config_params)
        await self.send_and_log(websocket, config_msg, is_large_payload=True)

    async def handle_incoming_messages(self, websocket):
        async for raw_message in websocket:
            try:
                protocol_string = raw_message.decode('latin-1')
                parsed = parse_idm_message(protocol_string)
                if not parsed: continue

                msg_type = parsed.get('type')
                original_seq = parsed.get('sequence')
                flags = parsed.get('flags', 0)
                args = parsed.get('args', [])
                params = parsed.get('params', {})

                if msg_type == 9 and len(args) >= 6:
                    try:
                        left = int(args[1])
                        top = int(args[2])
                        right = int(args[3])
                        bottom = int(args[4])
                        zoom = float(args[5])
                        if (right - left) > 100 and (bottom - top) > 100:
                            self.video_rect_received.emit(left, top, right, bottom, zoom)
                    except Exception:
                        pass
                    continue

                if msg_type == 7 and len(args) >= 1:
                    active_tab_id = str(args[0])
                    toolbar_offset = int(args[2]) if len(args) > 2 else 155
                    self.tab_switched.emit(active_tab_id, toolbar_offset)
                    continue

                if msg_type == 23:
                    element_id = args[0] if args else None
                    tab_id_str = str(args[1]) if len(args) > 1 else None
                    if element_id is not None and tab_id_str is not None:
                        log_to_file(f"[SERVER-RECV] Video Element Info (Type 23). Element ID: {element_id}, Tab ID: {tab_id_str}")
                        self.video_context.setdefault(tab_id_str, []).append(element_id)
                        self.video_context[tab_id_str] = list(set(self.video_context[tab_id_str]))

                        if self.mode in [0, 2] and tab_id_str in self.pending_type13_messages:
                            pending_for_tab = self.pending_type13_messages.pop(tab_id_str)
                            for p_msg in pending_for_tab: await self._process_type13_message_mode0(websocket, p_msg)

                        if self.mode in [1, 2]: await self._process_pending_chunks(websocket, tab_id_str)

                    self.video_detected.emit({'args': args, 'params': params})
                    continue

                if msg_type == 21:
                    tab_id_str = str(args[1]) if len(args) > 1 else "-1"
                    title = params.get(110)
                    if title:
                        self.title_detected.emit(tab_id_str, title)

                if msg_type == 6:
                    ack_args = [original_seq, 17041986, 1.25, 1]
                    await self.send_and_log(websocket, self.build_idm_message(1, 2, 2, args=ack_args))
                    if not self.initial_handshake_complete.is_set(): self.initial_handshake_complete.set()
                    continue

                if msg_type == 14: self.download_request_received.emit(parsed)
                elif msg_type == 13 and len(args) > 7:
                    download_category = args[5]
                    if download_category in [1, 2, 3]:
                        ack_msg = self.build_idm_message(1, 2, 0, args=[original_seq, 3])
                        await self.send_and_log(websocket, ack_msg)
                        self.download_request_received.emit(parsed)
                    elif download_category == 4:
                        log_to_file(f"[SERVER-RECV] << Media Request (Type 13, Cat 4). Processing for panel.")
                        await self.handle_video_panel_request(websocket, parsed)
                    elif download_category == 5:
                        ack_msg = self.build_idm_message(1, 2, 0, args=[original_seq, 1])
                        await self.send_and_log(websocket, ack_msg)

                if self.mode == 0:
                    if msg_type == 13:
                        tab_id_str = str(args[6]) if len(args) > 6 else "-1"
                        if tab_id_str in self.video_context:
                            await self._process_type13_message_mode0(websocket, parsed)
                        else:
                            self.pending_type13_messages.setdefault(tab_id_str, []).append(parsed)
                        if flags & 1: await self.send_and_log(websocket, self.build_idm_message(1, 2, 0, [original_seq, 3]))

                elif self.mode == 1:
                    if msg_type == 13:
                        tab_id_str = str(args[6]) if len(args) > 6 else "-1"
                        self.pending_video_chunks.setdefault(tab_id_str, []).append(parsed)
                        await self.send_and_log(websocket, self.build_idm_message(1, 2, 0, [original_seq, 1]))
                    elif msg_type == 16 and params.get(115):
                        for tab_id, chunks in self.pending_video_chunks.items():
                            if any(c.get('args', [])[5] == 5 for c in chunks):
                                self.graphql_data_store[tab_id] = params[115]
                                await self._process_pending_chunks(websocket, tab_id)
                                break

                elif self.mode == 2:
                    if msg_type == 13:
                        tab_id_str = str(args[6]) if len(args) > 6 else "-1"
                        if tab_id_str in self.video_context: await self._process_type13_message_mode0(websocket, parsed)
                        self.pending_type13_messages.setdefault(tab_id_str, []).append(parsed)
                        if args[5] == 5: self.pending_video_chunks.setdefault(tab_id_str, []).append(parsed)
                        if flags & 1: await self.send_and_log(websocket, self.build_idm_message(1, 2, 0, [original_seq, 1]))
                    elif msg_type == 16 and params.get(115):
                        for tab_id, chunks in self.pending_video_chunks.items():
                            if any(c.get('args', [])[5] == 5 for c in chunks):
                                self.graphql_data_store[tab_id] = params[115]
                                await self._process_pending_chunks(websocket, tab_id)
                                break
                
                if flags & 1 and msg_type not in [13, 16]:
                    await self.send_and_log(websocket, self.build_idm_message(1, 2, 0, [original_seq]))

            except websockets.exceptions.ConnectionClosed as e: log_to_file(f"Connection closed: {e}"); break
            except Exception as e: log_to_file(f"Error handling message: {e}\n{traceback.format_exc()}")

    async def handler(self, websocket):
        log_to_file(f"--- WebSocket connection established from {websocket.remote_address} ---")
        self.initial_handshake_complete.clear()

        try:
            initial_message_raw = await websocket.recv()
            initial_message_parsed = parse_idm_message(initial_message_raw.decode('latin-1'))
            if initial_message_parsed and initial_message_parsed.get('type') == 2:
                log_to_file("Addon handshake validated. Starting sender and receiver tasks.")
                asyncio.create_task(self.send_initial_config(websocket))
                await self.handle_incoming_messages(websocket)
            else:
                log_to_file("Did not receive expected handshake message. Closing connection."); await websocket.close()
        except Exception as e:
            log_to_file(f"FATAL ERROR in connection handler for {websocket.remote_address}: {e}\n{traceback.format_exc()}")
        finally:
            log_to_file(f"--- Handler for {websocket.remote_address} is finishing. Connection is closing. ---")

    async def process_request(self, path, request_headers): return None
    async def start_server(self):
        self.server = await websockets.serve(self.handler, self.host, self.port, process_request=self.process_request, subprotocols=[websockets.Subprotocol("plugin.v3.internetdownloadmanager.com")], max_size=None)
        log_to_file(f"WebSocket server started on ws://{self.host}:{self.port}"); await self.server.wait_closed()
    def run(self):
        self.loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        try: self.loop.run_until_complete(self.start_server())
        except OSError as e: log_to_file(f"FATAL: Could not start WebSocket server. Port {self.port} may be in use. Error: {e}")
    def stop(self):
        if self.server and self.loop and self.loop.is_running(): log_to_file("Stopping WebSocket server..."); self.loop.call_soon_threadsafe(self.server.close)

class UnifiedSelectorFetcherSignals(QObject):
    status = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

class UnifiedSelectorFetcher(QRunnable):
    def __init__(self):
        super().__init__(); self.signals = UnifiedSelectorFetcherSignals(); self.is_cancelled = False; self.proc = None

    def cancel(self):
        self.signals.status.emit("Cancellation requested...")
        self.is_cancelled = True
        if self.proc and self.proc.returncode is None:
            self.signals.status.emit("Terminating native host subprocess...")
            self.proc.terminate()

    def _parse_and_structure_message(self, msg_str):
        if not msg_str or not msg_str.startswith("MSG#") or '#18#' not in msg_str:
            return None
        parsed = parse_idm_message(msg_str)
        if not parsed or parsed.get('type') != 18:
            return None

        domains = parsed['params'].get(101, 'unknown.com').split('|')[0].replace('~', '')
        rule = {
            "id": parsed['args'][0] if parsed['args'] else 0,
            "name": domains,
            "enabled": True,
            "params": {str(k): v for k, v in parsed['params'].items()}
        }
        return rule

    async def _fetch_wss(self):
        if self.is_cancelled: return None
        uri = f"ws://127.0.0.1:1001/?cid={CLIENT_ID}&rnd={int(time.time() * 1000)}"
        self.signals.status.emit("[Debug] WSS: Starting process.")
        log_to_file("[Debug] WSS: Starting process.")

        wait_seconds = 10
        self.signals.status.emit(f"[Debug] WSS: Waiting {wait_seconds}s for IDM service to initialize...")
        for i in range(wait_seconds):
            if self.is_cancelled: return None
            self.signals.status.emit(f"[Debug] WSS: Waiting... {wait_seconds - i}s")
            await asyncio.sleep(1)

        self.signals.status.emit(f"[Debug] WSS: Attempting connection to {uri}")
        log_to_file(f"[Debug] WSS: Attempting connection to {uri}")
        
        try:
            async with websockets.connect(
                uri, 
                subprotocols=[websockets.Subprotocol("plugin.v3.internetdownloadmanager.com")],
                origin=FAKE_ORIGIN,
                user_agent_header=BROWSER_USER_AGENT,
                open_timeout=5
            ) as ws:
                self.signals.status.emit("[Debug] WSS: Connection successful. Sending handshake.")
                log_to_file("[Debug] WSS: Connection successful.")
                
                lang_code = locale.getdefaultlocale()[0].replace('_', '-') if locale.getdefaultlocale()[0] else 'en-US'
                handshake_msg = f"MSG#1#2#5#1024:{':'.join(map(str, [109, 80, 1031, 0, 16845059, CLIENT_ID, 0, ADDON_MANIFEST_VERSION]))},{'112=' + str(len(BROWSER_USER_AGENT)) + ':' + BROWSER_USER_AGENT + ',' + '113=' + str(len(PLATFORM_INFO)) + ':' + PLATFORM_INFO + ',' + '125=2:{},' + '116=' + str(len(lang_code)) + ':' + lang_code};"
                log_to_file(f"[Debug] WSS: Sending handshake: {handshake_msg}")

                await ws.send(handshake_msg.encode('latin-1'))
                
                structured_selectors = []
                self.signals.status.emit("[Debug] WSS: Handshake sent. Receiving messages...")
                log_to_file("[Debug] WSS: Handshake sent. Receiving messages...")
                
                while True:
                    if self.is_cancelled: return None
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        msg_str = msg.decode('latin-1', errors='ignore').strip().strip('"')
                        log_to_file(f"[Debug] WSS: Received raw message: {msg_str}")
                        structured = self._parse_and_structure_message(msg_str)
                        if structured:
                            structured_selectors.append(structured)
                            self.signals.status.emit(f"[Debug] WSS: Parsed rule #{len(structured_selectors)} for {structured['name']}")
                    except asyncio.TimeoutError:
                        self.signals.status.emit("[Debug] WSS: Selector stream finished (timeout).")
                        log_to_file("[Debug] WSS: Selector stream finished (timeout).")
                        break
                return structured_selectors
        except Exception as e:
            self.signals.status.emit(f"[Debug] WSS: Fetch failed: {e}")
            log_to_file(f"[Debug] WSS: Fetch failed with exception: {e}\n{traceback.format_exc()}")
            return None

    async def _fetch_native(self):
        if self.is_cancelled: return None
        self.signals.status.emit(f"WSS failed. Falling back to Native Host.")
        log_to_file(f"WSS failed. Falling back to Native Host.")
        
        async def log_stderr(stream):
            while True:
                line = await stream.readline()
                if not line: break
                err_line = line.decode(errors='ignore').strip()
                self.signals.status.emit(f"[Debug] Native Host stderr: {err_line}")
                log_to_file(f"[Debug] Native Host stderr: {err_line}")

        try:
            self.proc = await asyncio.create_subprocess_exec(IDM_NATIVE_HOST_EXECUTABLE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            asyncio.create_task(log_stderr(self.proc.stderr))
            
            self.signals.status.emit("Native Host: Process started. Sending handshake...")
            log_to_file("Native Host: Process started. Sending handshake...")
            
            lang_code = locale.getdefaultlocale()[0].replace('_', '-') if locale.getdefaultlocale()[0] else 'en-US'
            handshake = f"MSG#1#2#5#1024:{':'.join(map(str, [109, 80, 1031, 0, 16845059, CLIENT_ID, 0, ADDON_MANIFEST_VERSION]))},{'112=' + str(len('Firefox/120.0')) + ':' + 'Firefox/120.0' + ',' + '113=' + str(len(PLATFORM_INFO)) + ':' + PLATFORM_INFO + ',' + '125=2:{},' + '116=' + str(len(lang_code)) + ':' + lang_code};"
            handshake_bytes = handshake.encode('utf-8')
            
            log_to_file(f"[Debug] Native Host: Sending handshake: {handshake}")
            self.proc.stdin.write(struct.pack('<I', len(handshake_bytes)) + handshake_bytes)
            await self.proc.stdin.drain()
            
            structured_selectors = []
            while True:
                if self.is_cancelled: break
                try:
                    length_bytes = await asyncio.wait_for(self.proc.stdout.readexactly(4), timeout=2.0)
                    message_length = struct.unpack('<I', length_bytes)[0]
                    message_bytes = await self.proc.stdout.readexactly(message_length)
                    json_encoded_string = message_bytes.decode('utf-8', errors='ignore')
                    msg_str = json.loads(json_encoded_string)
                    log_to_file(f"[Debug] Native Host: Received raw message: {msg_str}")
                    structured = self._parse_and_structure_message(msg_str)
                    if structured:
                        structured_selectors.append(structured)
                        self.signals.status.emit(f"[Debug] Native: Parsed rule #{len(structured_selectors)} for {structured['name']}")
                except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                    self.signals.status.emit("Native fetch finished.")
                    log_to_file("Native fetch finished.")
                    break
            return structured_selectors
        except Exception as e:
            if not self.is_cancelled:
                self.signals.status.emit(f"Native Host fetch failed: {e}")
                log_to_file(f"[Debug] Native Host: Fetch failed with exception: {e}\n{traceback.format_exc()}")
            return None
        finally:
            if self.proc and self.proc.returncode is None: self.proc.terminate(); await self.proc.wait()
            self.proc = None

    async def fetch_async(self):
        wss_results = await self._fetch_wss()
        if self.is_cancelled:
            self.signals.status.emit("Fetch cancelled by user.")
            return

        if wss_results is not None:
            self.signals.status.emit(f"Successfully fetched {len(wss_results)} selector rules via WSS.")
            self.signals.finished.emit(wss_results)
            return
        
        native_results = await self._fetch_native()
        if self.is_cancelled:
            self.signals.status.emit("Fetch cancelled by user.")
            return

        if native_results is not None:
            self.signals.status.emit(f"Successfully fetched {len(native_results)} selector rules via Native Host.")
            self.signals.finished.emit(native_results)
        else:
            if not self.is_cancelled:
                self.signals.error.emit("Failed to fetch selectors using both WSS and Native Host methods.")

    def run(self):
        if self.is_cancelled: return
        try: asyncio.run(self.fetch_async())
        except Exception as e:
            if not self.is_cancelled:
                self.signals.error.emit(f"Failed to run async selector fetcher: {e}")

class SettingsDialog(QDialog):
    def __init__(self, settings_manager, thread_pool, ws_server, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager; self.thread_pool = thread_pool; self.ws_server = ws_server
        self.setWindowTitle("PyIDM Configuration")
        self.setMinimumWidth(700); self.setMinimumHeight(600)
        self.layout = QVBoxLayout(self)
        self.tabs = QTabWidget(); self.layout.addWidget(self.tabs)
        self.fetcher_worker = None
        self.selector_rules = []

        self.setup_general_tab()
        self.setup_file_types_tab()
        self.setup_save_to_tab()
        self.setup_downloads_tab()
        self.setup_connection_tab()
        self.setup_proxy_tab()
        self.setup_sites_logins_tab()
        self.setup_dial_up_tab()
        self.setup_sounds_tab()
        self.setup_download_panels_tab()
        self.setup_selectors_tab()

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        self.ok_btn = QPushButton("OK")
        self.cancel_btn = QPushButton("Cancel")
        self.help_btn = QPushButton("Help")
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(self.ok_btn)
        buttons_layout.addWidget(self.cancel_btn)
        buttons_layout.addWidget(self.help_btn)
        self.layout.addLayout(buttons_layout)

    def create_header(self, title_text, icon_pixmap=None):
        widget = QWidget()
        h_layout = QHBoxLayout(widget)
        h_layout.setContentsMargins(0, 0, 0, 5)
        if icon_pixmap:
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            h_layout.addWidget(icon_label)
        title_label = QLabel(title_text)
        title_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #111111;")
        h_layout.addWidget(title_label)
        h_layout.addStretch()
        return widget

    def setup_general_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "General")
        layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Browser/System Integration", self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon).pixmap(32, 32)))

        integ_box = QHBoxLayout()
        integ_box.addWidget(QLabel("Advanced browser integration is enabled"))
        restart_btn = QPushButton("Restart")
        restart_btn.setFixedWidth(80)
        restart_btn.clicked.connect(self.restart_browser_integration)
        integ_box.addWidget(restart_btn)
        integ_box.addStretch()
        layout.addLayout(integ_box)

        self.startup_cb = QCheckBox("Launch PyIDM on startup")
        self.startup_cb.setChecked(self.settings_manager.settings.get("launch_on_startup", False))
        layout.addWidget(self.startup_cb)

        self.clipboard_cb = QCheckBox("Automatically start downloading of URLs placed to clipboard")
        self.clipboard_cb.setChecked(self.settings_manager.settings.get("clipboard_monitoring", False))
        layout.addWidget(self.clipboard_cb)

        browsers_group = QGroupBox("Capture downloads from the following browsers:")
        bg_layout = QVBoxLayout(browsers_group)
        self.browsers_list = QListWidget()

        saved_captured = self.settings_manager.settings.get("captured_browsers", {})
        for b_name in browser_integration.SUPPORTED_BROWSERS:
            item = QListWidgetItem(b_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            is_checked = saved_captured.get(b_name, True)
            item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            self.browsers_list.addItem(item)

        for custom in self.settings_manager.settings.get("custom_browsers", []):
            item = QListWidgetItem(custom.get("name", Path(custom.get("path", "")).name))
            item.setData(Qt.ItemDataRole.UserRole, custom.get("path", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if custom.get("enabled", True) else Qt.CheckState.Unchecked)
            self.browsers_list.addItem(item)

        self.browsers_list.itemChanged.connect(self.on_browser_item_toggled)
        bg_layout.addWidget(self.browsers_list)

        add_b_row = QHBoxLayout()
        add_b_row.addStretch()
        add_browser_btn = QPushButton("Add browser...")
        add_browser_btn.clicked.connect(self.add_custom_browser)
        add_b_row.addWidget(add_browser_btn)
        bg_layout.addLayout(add_b_row)

        layout.addWidget(browsers_group)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        keys_row = QHBoxLayout()
        keys_row.addWidget(QLabel("Customize keys to prevent or force downloading with PyIDM"))
        keys_row.addStretch()
        keys_btn = QPushButton("Keys...")
        keys_btn.setFixedWidth(90)
        keys_btn.clicked.connect(self.open_special_keys_dialog)
        keys_row.addWidget(keys_btn)
        layout.addLayout(keys_row)

        menu_row = QHBoxLayout()
        menu_row.addWidget(QLabel("Customize PyIDM menu items in context menu of browsers"))
        menu_row.addStretch()
        menu_edit_btn = QPushButton("Edit...")
        menu_edit_btn.setFixedWidth(90)
        menu_edit_btn.clicked.connect(self.open_context_menu_dialog)
        menu_row.addWidget(menu_edit_btn)
        layout.addLayout(menu_row)

        panels_row = QHBoxLayout()
        panels_row.addWidget(QLabel("Customize PyIDM Download panels in browsers"))
        panels_row.addStretch()
        panels_edit_btn = QPushButton("Edit...")
        panels_edit_btn.setFixedWidth(90)
        panels_edit_btn.clicked.connect(self.open_download_panels_dialog)
        panels_row.addWidget(panels_edit_btn)
        layout.addLayout(panels_row)

        layout.addStretch()

    def restart_browser_integration(self):
        log_to_file("[Browser Integration] Refreshing browser integration states...")
        for i in range(self.browsers_list.count()):
            item = self.browsers_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                browser_integration.install_browser_extension(item.text(), item.data(Qt.ItemDataRole.UserRole))
        QMessageBox.information(self, "Browser Integration", "Advanced browser integration restarted and extensions re-checked.")

    def on_browser_item_toggled(self, item):
        b_name = item.text()
        custom_path = item.data(Qt.ItemDataRole.UserRole)
        is_checked = item.checkState() == Qt.CheckState.Checked
        if is_checked:
            ok, msg = browser_integration.install_browser_extension(b_name, custom_path)
        else:
            ok, msg = browser_integration.uninstall_browser_extension(b_name, custom_path)
        log_to_file(f"[Browser Integration Toggle] {b_name} (checked={is_checked}): {msg}")

    def add_custom_browser(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Browser Executable", "", "Executables (*.exe);;All Files (*)")
        if file_path:
            p = Path(file_path)
            b_name = p.stem.capitalize()
            item = QListWidgetItem(b_name)
            item.setData(Qt.ItemDataRole.UserRole, file_path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.browsers_list.addItem(item)
            browser_integration.install_browser_extension(b_name, file_path)
            custom_list = self.settings_manager.settings.setdefault("custom_browsers", [])
            custom_list.append({"name": b_name, "path": file_path, "enabled": True})

    def open_special_keys_dialog(self):
        dlg = SpecialKeysDialog(self.settings_manager, self)
        dlg.exec()

    def open_context_menu_dialog(self):
        dlg = ContextMenuCustomizationDialog(self.settings_manager, self)
        dlg.exec()

    def open_download_panels_dialog(self):
        dlg = DownloadPanelsCustomizationDialog(self.settings_manager, self)
        dlg.exec()

    def setup_file_types_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "File types"); layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Downloaded file types", self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon).pixmap(32, 32)))

        layout.addWidget(QLabel("Automatically start downloading the following file types:"))
        self.auto_types_edit = QTextEdit()
        self.auto_types_edit.setFixedHeight(75)
        self.auto_types_edit.setPlainText(" ".join(self.settings_manager.settings.get('auto_download_types', [])))
        layout.addWidget(self.auto_types_edit)

        def_btn1 = QPushButton("Default")
        def_btn1.setFixedWidth(80)
        def_btn1.clicked.connect(lambda: self.auto_types_edit.setPlainText(" ".join(self.settings_manager.get_default_auto_download_types())))
        layout.addWidget(def_btn1, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(QLabel("Don't start downloading automatically from the following sites:"))
        self.dont_sites_edit = QTextEdit()
        self.dont_sites_edit.setFixedHeight(75)
        self.dont_sites_edit.setPlainText(" ".join(self.settings_manager.settings.get('dont_auto_download_sites', [])))
        layout.addWidget(self.dont_sites_edit)

        sites_footer = QHBoxLayout()
        sites_footer.addWidget(QLabel("(separate names by spaces)"))
        sites_footer.addStretch()
        def_btn2 = QPushButton("Default")
        def_btn2.setFixedWidth(80)
        def_btn2.clicked.connect(lambda: self.dont_sites_edit.setPlainText("*.update.microsoft.com download.windowsupdate.com *.download.windowsupdate.com siteseal.thawte.com ecom.cimetz.com *.voice2page.com localhost"))
        sites_footer.addWidget(def_btn2)
        layout.addLayout(sites_footer)

        layout.addWidget(QLabel("Don't start downloading automatically from the following addresses:"))
        self.edit_addr_btn = QPushButton("Edit list ...")
        self.edit_addr_btn.setFixedWidth(100)
        self.edit_addr_btn.clicked.connect(self.edit_addresses_dialog)
        layout.addWidget(self.edit_addr_btn)
        layout.addStretch()

    def edit_addresses_dialog(self):
        d = QDialog(self)
        d.setWindowTitle("Exceptions list")
        d.setMinimumSize(450, 300)
        l = QVBoxLayout(d)
        w = ListManagementWidget("Addresses (wildcards * supported):", self.settings_manager.settings.get('dont_auto_download_addresses', []))
        l.addWidget(w)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(lambda: (self.settings_manager.settings.__setitem__('dont_auto_download_addresses', w.get_items()), d.accept()))
        btns.rejected.connect(d.reject)
        l.addWidget(btns)
        d.exec()

    def setup_save_to_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "Save to"); layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Categories, file types, folders", self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon).pixmap(32, 32)))

        cat_group = QGroupBox("Save To...")
        cg_layout = QVBoxLayout(cat_group)

        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("Category"))
        self.category_combo = QComboBox()
        self.category_combo.addItems(list(FILE_CATEGORIES.keys()))
        cat_row.addWidget(self.category_combo, stretch=1)
        new_cat_btn = QPushButton("New")
        edit_cat_btn = QPushButton("Edit...")
        cat_row.addWidget(new_cat_btn)
        cat_row.addWidget(edit_cat_btn)
        cg_layout.addLayout(cat_row)

        cg_layout.addWidget(QLabel("Automatically put in category the following file types:"))
        self.cat_ext_edit = QLineEdit()
        self.cat_ext_edit.setReadOnly(True)
        cg_layout.addWidget(self.cat_ext_edit)

        cg_layout.addWidget(QLabel("Default download directory for category:"))
        dir_box = QHBoxLayout()
        self.cat_dir_edit = QLineEdit()
        browse_cat_btn = QPushButton("Browse")
        browse_cat_btn.clicked.connect(lambda: self.browse_folder(self.cat_dir_edit))
        dir_box.addWidget(self.cat_dir_edit)
        dir_box.addWidget(browse_cat_btn)
        cg_layout.addLayout(dir_box)

        self.change_folder_last_cb = QCheckBox("Change folder for category on last selected")
        self.change_folder_last_cb.setChecked(self.settings_manager.settings.get("change_folder_last_selected", True))
        cg_layout.addWidget(self.change_folder_last_cb)
        layout.addWidget(cat_group)

        def on_cat_changed(cat_name):
            data = FILE_CATEGORIES.get(cat_name, {"extensions": []})
            self.cat_ext_edit.setText(" ".join(data.get("extensions", [])))
            self.cat_dir_edit.setText(self.settings_manager.settings.get("save_paths", {}).get(cat_name, ""))

        self.category_combo.currentTextChanged.connect(on_cat_changed)
        on_cat_changed(self.category_combo.currentText())

        self.creation_date_cb = QCheckBox("Set file creation date as provided by the server")
        self.creation_date_cb.setChecked(self.settings_manager.settings.get("set_creation_date_from_server", False))
        layout.addWidget(self.creation_date_cb)

        temp_group = QGroupBox("Temporary directory")
        tg_layout = QVBoxLayout(temp_group)
        t_row = QHBoxLayout()
        self.temp_dir_edit = QLineEdit(self.settings_manager.settings.get("temp_dir", str(Path.home() / "Downloads" / "Temp")))
        browse_temp_btn = QPushButton("Browse")
        browse_temp_btn.clicked.connect(lambda: self.browse_folder(self.temp_dir_edit))
        t_row.addWidget(self.temp_dir_edit)
        t_row.addWidget(browse_temp_btn)
        tg_layout.addLayout(t_row)
        t_info = QLabel("Temporary directory is required for storing file parts during download.\nIf you have several physical drives on your computer, you should select different physical drives for temporary directory and \"Save To\" folders for faster assembling of downloaded files.")
        t_info.setWordWrap(True)
        t_info.setStyleSheet("color: #444444; font-size: 11px;")
        tg_layout.addWidget(t_info)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("For single-connection downloads:"))
        self.single_part_mode_combo = QComboBox()
        self.single_part_mode_combo.addItems([
            "Save to temporary folder first, then move upon completion",
            "Write directly to destination file"
        ])
        curr_mode = self.settings_manager.settings.get("single_part_write_mode", "Save to temporary folder first, then move upon completion")
        idx_m = self.single_part_mode_combo.findText(curr_mode)
        if idx_m >= 0: self.single_part_mode_combo.setCurrentIndex(idx_m)
        mode_row.addWidget(self.single_part_mode_combo)
        mode_row.addStretch()
        tg_layout.addLayout(mode_row)
        layout.addWidget(temp_group)
        layout.addStretch()

    def setup_downloads_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "Downloads")
        layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Default download settings", self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown).pixmap(32, 32)))

        self.show_start_dlg_cb = QCheckBox("Show start download dialog")
        self.show_start_dlg_cb.setChecked(self.settings_manager.settings.get("show_start_dialog", True))
        layout.addWidget(self.show_start_dlg_cb)

        queue_only_box = QHBoxLayout()
        queue_only_box.addSpacing(20)
        self.queue_only_cb = QCheckBox("Do not start downloading, only add files to the queue")
        self.queue_only_cb.setChecked(self.settings_manager.settings.get("queue_only", False))
        queue_only_box.addWidget(self.queue_only_cb)
        layout.addLayout(queue_only_box)

        self.show_complete_dlg_cb = QCheckBox("Show download complete dialog")
        self.show_complete_dlg_cb.setChecked(self.settings_manager.settings.get("show_complete_dialog", True))
        layout.addWidget(self.show_complete_dlg_cb)

        note_label = QLabel("Note: These settings don't relate to queue processing")
        note_label.setStyleSheet("color: #666666; font-size: 11px;")
        layout.addWidget(note_label)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine); sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)

        self.start_immediately_cb = QCheckBox("Start downloading immediately while displaying \"Download File Info\" dialog")
        self.start_immediately_cb.setChecked(self.settings_manager.settings.get("start_immediately", True))
        layout.addWidget(self.start_immediately_cb)

        self.show_queue_dl_later_cb = QCheckBox("Show queue selection panel on pressing \"Download Later\" button")
        self.show_queue_dl_later_cb.setChecked(self.settings_manager.settings.get("show_queue_on_download_later", True))
        layout.addWidget(self.show_queue_dl_later_cb)

        self.show_queue_batch_cb = QCheckBox("Show queue selection panel on closing batch download dialogs")
        self.show_queue_batch_cb.setChecked(self.settings_manager.settings.get("show_queue_on_batch_close", True))
        layout.addWidget(self.show_queue_batch_cb)

        self.ignore_time_changes_cb = QCheckBox("Ignore file modification time changes when resuming a download")
        self.ignore_time_changes_cb.setChecked(self.settings_manager.settings.get("ignore_time_changes_on_resume", False))
        layout.addWidget(self.ignore_time_changes_cb)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        layout.addWidget(QLabel("If a duplicate download link is added:"))
        self.duplicate_combo = QComboBox()
        self.duplicate_combo.addItems(DUPLICATE_ACTIONS)
        curr_dup = self.settings_manager.settings.get("duplicate_action", DUPLICATE_ACTIONS[0])
        idx = self.duplicate_combo.findText(curr_dup)
        if idx >= 0: self.duplicate_combo.setCurrentIndex(idx)
        layout.addWidget(self.duplicate_combo)

        layout.addWidget(QLabel("User-Agent for manually added downloads:"))
        self.user_agent_edit = QLineEdit(self.settings_manager.settings.get("manual_user_agent", BROWSER_USER_AGENT))
        layout.addWidget(self.user_agent_edit)
        layout.addStretch()

    def setup_connection_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "Connection")
        layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Connections and Limits", self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon).pixmap(32, 32)))

        max_group = QGroupBox("Max. connections number")
        mg_layout = QVBoxLayout(max_group)

        c_row = QHBoxLayout()
        c_row.addWidget(QLabel("Default max. conn. number"))
        self.connections_combo = QComboBox()
        self.connections_combo.setEditable(True)
        for opt in ["1", "2", "4", "8", "16", "32"]: self.connections_combo.addItem(opt)
        curr_conns = str(self.settings_manager.settings.get("max_connections", 32))
        idx = self.connections_combo.findText(curr_conns)
        if idx >= 0: self.connections_combo.setCurrentIndex(idx)
        else: self.connections_combo.setEditText(curr_conns)
        c_row.addWidget(self.connections_combo)
        c_row.addStretch()
        mg_layout.addLayout(c_row)

        mg_layout.addWidget(QLabel("Exceptions:"))
        exc_box = QHBoxLayout()
        self.exc_table = QTableWidget()
        self.exc_table.setColumnCount(2)
        self.exc_table.setHorizontalHeaderLabels(["Server", "Number"])
        self.exc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.exc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.exc_table.setColumnWidth(1, 80)
        self.exc_table.setFixedHeight(95)
        exc_box.addWidget(self.exc_table)

        exc_btn_layout = QVBoxLayout()
        new_exc_btn = QPushButton("New")
        del_exc_btn = QPushButton("Delete")
        edit_exc_btn = QPushButton("Edit")
        exc_btn_layout.addWidget(new_exc_btn)
        exc_btn_layout.addWidget(del_exc_btn)
        exc_btn_layout.addWidget(edit_exc_btn)
        exc_btn_layout.addStretch()
        exc_box.addLayout(exc_btn_layout)
        mg_layout.addLayout(exc_box)
        layout.addWidget(max_group)

        limits_group = QGroupBox()
        lg_layout = QVBoxLayout(limits_group)
        self.enable_limits_cb = QCheckBox("Download limits")
        self.enable_limits_cb.setChecked(self.settings_manager.settings.get("download_limits_enabled", False))
        lg_layout.addWidget(self.enable_limits_cb)

        lim_row = QHBoxLayout()
        lim_row.addWidget(QLabel("Download no more than"))
        self.limit_mb_spin = QSpinBox(); self.limit_mb_spin.setRange(1, 1000000); self.limit_mb_spin.setValue(self.settings_manager.settings.get("download_limit_mbytes", 200))
        lim_row.addWidget(self.limit_mb_spin)
        lim_row.addWidget(QLabel("MBytes every"))
        self.limit_hours_spin = QSpinBox(); self.limit_hours_spin.setRange(1, 1000); self.limit_hours_spin.setValue(self.settings_manager.settings.get("download_limit_hours", 5))
        lim_row.addWidget(self.limit_hours_spin)
        lim_row.addWidget(QLabel("hours"))
        lim_row.addStretch()
        lg_layout.addLayout(lim_row)

        self.limit_warning_cb = QCheckBox("Show warning before stopping downloads")
        self.limit_warning_cb.setChecked(self.settings_manager.settings.get("show_limit_warning", True))
        lg_layout.addWidget(self.limit_warning_cb)
        layout.addWidget(limits_group)
        layout.addStretch()

    def setup_proxy_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "Proxy / Socks")
        layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Proxy / socks configuration", self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon).pixmap(32, 32)))

        self.browser_proxy_cb = QCheckBox("Use proxy/socks from a browser's request if there are download errors for the downloads intercepted from the browser")
        self.browser_proxy_cb.setChecked(self.settings_manager.settings.get("use_browser_proxy_on_error", True))
        layout.addWidget(self.browser_proxy_cb)

        self.proxy_btn_group = QButtonGroup(self)
        self.rb_no_proxy = QRadioButton("No proxy/socks")
        self.rb_system_proxy = QRadioButton("Use system settings")
        self.rb_pac_proxy = QRadioButton("Use automatic configuration script")
        self.rb_manual_proxy = QRadioButton("Manual proxy/socks configuration")
        self.proxy_btn_group.addButton(self.rb_no_proxy)
        self.proxy_btn_group.addButton(self.rb_system_proxy)
        self.proxy_btn_group.addButton(self.rb_pac_proxy)
        self.proxy_btn_group.addButton(self.rb_manual_proxy)

        mode = self.settings_manager.settings.get("proxy_mode", "none")
        if mode == "system": self.rb_system_proxy.setChecked(True)
        elif mode == "pac": self.rb_pac_proxy.setChecked(True)
        elif mode == "manual": self.rb_manual_proxy.setChecked(True)
        else: self.rb_no_proxy.setChecked(True)

        layout.addWidget(self.rb_no_proxy)
        layout.addWidget(self.rb_system_proxy)
        layout.addWidget(self.rb_pac_proxy)

        pac_layout = QHBoxLayout()
        pac_layout.addSpacing(20)
        pac_layout.addWidget(QLabel("Address"))
        self.pac_address_edit = QLineEdit(self.settings_manager.settings.get("pac_address", ""))
        pac_layout.addWidget(self.pac_address_edit)
        layout.addLayout(pac_layout)

        layout.addWidget(self.rb_manual_proxy)
        man_layout = QFormLayout()
        man_layout.setContentsMargins(20, 0, 0, 0)
        self.proxy_host_edit = QLineEdit(self.settings_manager.settings.get("manual_proxy_host", ""))
        self.proxy_port_edit = QLineEdit(str(self.settings_manager.settings.get("manual_proxy_port", "")))
        self.proxy_user_edit = QLineEdit(self.settings_manager.settings.get("manual_proxy_user", ""))
        self.proxy_pass_edit = QLineEdit(self.settings_manager.settings.get("manual_proxy_pass", ""))
        self.proxy_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)

        h_row = QHBoxLayout(); h_row.addWidget(self.proxy_host_edit, stretch=2); h_row.addWidget(QLabel("Port")); h_row.addWidget(self.proxy_port_edit, stretch=1)
        u_row = QHBoxLayout(); u_row.addWidget(self.proxy_user_edit); u_row.addWidget(QLabel("Password")); u_row.addWidget(self.proxy_pass_edit)
        man_layout.addRow("Proxy address", h_row)
        man_layout.addRow("UserName", u_row)
        layout.addLayout(man_layout)

        self.ftp_pasv_cb = QCheckBox("Use FTP in PASV mode")
        self.ftp_pasv_cb.setChecked(self.settings_manager.settings.get("ftp_pasv", False))
        layout.addWidget(self.ftp_pasv_cb)
        layout.addStretch()

    def setup_sites_logins_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "Sites Logins")
        layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("User names and passwords for servers/sites", self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView).pixmap(32, 32)))

        self.sites_table = QTableWidget()
        self.sites_table.setColumnCount(3)
        self.sites_table.setHorizontalHeaderLabels(["Site/path", "User", "Password"])
        self.sites_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sites_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.sites_table.setColumnWidth(1, 140)
        self.sites_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.sites_table.setColumnWidth(2, 120)
        layout.addWidget(self.sites_table)

        btn_row = QHBoxLayout()
        new_site_btn = QPushButton("New")
        edit_site_btn = QPushButton("Edit")
        del_site_btn = QPushButton("Remove")
        btn_row.addWidget(new_site_btn)
        btn_row.addWidget(edit_site_btn)
        btn_row.addWidget(del_site_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def setup_dial_up_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "Dial Up / VPN")
        layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Dial Up / VPN Networking", self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon).pixmap(32, 32)))

        dial_data = self.settings_manager.settings.get("dial_up_vpn", {})
        self.dial_enabled_cb = QCheckBox("Use Windows Dial Up / VPN networking")
        self.dial_enabled_cb.setChecked(dial_data.get("enabled", False))
        layout.addWidget(self.dial_enabled_cb)

        form_group = QGroupBox("Connection parameters")
        f_layout = QFormLayout(form_group)

        self.dial_conn_combo = QComboBox()
        self.dial_conn_combo.addItems(["(Default Dial-Up Connection)", "VPN Connection 1", "Broadband PPPoE"])
        if dial_data.get("connection"):
            self.dial_conn_combo.setCurrentText(dial_data["connection"])

        self.dial_user_edit = QLineEdit(dial_data.get("username", ""))
        self.dial_pass_edit = QLineEdit(dial_data.get("password", ""))
        self.dial_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.dial_save_pass_cb = QCheckBox("Save password")
        self.dial_save_pass_cb.setChecked(dial_data.get("save_password", True))

        self.dial_redial_spin = QSpinBox()
        self.dial_redial_spin.setRange(1, 99)
        self.dial_redial_spin.setValue(dial_data.get("redial_attempts", 3))

        self.dial_interval_spin = QSpinBox()
        self.dial_interval_spin.setRange(1, 300)
        self.dial_interval_spin.setValue(dial_data.get("redial_interval", 5))

        self.dial_hangup_cb = QCheckBox("Hang up connection when download completes")
        self.dial_hangup_cb.setChecked(dial_data.get("hang_up_when_done", False))

        f_layout.addRow("Connection:", self.dial_conn_combo)
        f_layout.addRow("User name:", self.dial_user_edit)
        f_layout.addRow("Password:", self.dial_pass_edit)
        f_layout.addRow("", self.dial_save_pass_cb)
        f_layout.addRow("Redial attempts:", self.dial_redial_spin)
        f_layout.addRow("Redial interval (sec):", self.dial_interval_spin)
        f_layout.addRow("", self.dial_hangup_cb)

        layout.addWidget(form_group)

        def toggle_dial_fields(checked):
            form_group.setEnabled(checked)

        self.dial_enabled_cb.toggled.connect(toggle_dial_fields)
        toggle_dial_fields(self.dial_enabled_cb.isChecked())
        layout.addStretch()

    def setup_sounds_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "Sounds")
        layout = QVBoxLayout(tab)
        layout.addWidget(self.create_header("Sound settings", self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(32, 32)))

        layout.addWidget(QLabel("Select sounds for PyIDM events"))
        self.sounds_table = QTableWidget()
        self.sounds_table.setColumnCount(2)
        self.sounds_table.setHorizontalHeaderLabels(["Event", "Sound file"])
        self.sounds_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.sounds_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        events = ["Download complete", "Download failed", "Queue processing started", "Queue processing stopped/finished"]
        self.sounds_table.setRowCount(len(events))
        for i, ev in enumerate(events):
            item = QTableWidgetItem(ev)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.sounds_table.setItem(i, 0, item)
            self.sounds_table.setItem(i, 1, QTableWidgetItem(""))
        layout.addWidget(self.sounds_table)

        btn_row = QHBoxLayout()
        browse_sound_btn = QPushButton("Browse...")
        play_sound_btn = QPushButton("Play")
        btn_row.addWidget(browse_sound_btn)
        btn_row.addWidget(play_sound_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def setup_download_panels_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "Download Panels"); layout = QVBoxLayout(tab)
        self.panel_types_widget = ListManagementWidget("Show download panel for the following file types:", self.settings_manager.settings['panel_file_types']); layout.addWidget(self.panel_types_widget)
        self.dont_capture_cb = QCheckBox("Don't capture downloads from web-players automatically"); self.dont_capture_cb.setChecked(self.settings_manager.settings['dont_capture_from_players']); layout.addWidget(self.dont_capture_cb)
        self.dont_show_panel_widget = ListManagementWidget("Don't show download panel for the following sites (wildcards * supported):", self.settings_manager.settings['dont_show_panel_sites']); layout.addWidget(self.dont_show_panel_widget)

    def setup_selectors_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "Selectors"); layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal); layout.addWidget(splitter)

        left_widget = QWidget(); left_layout = QVBoxLayout(left_widget)
        self.site_list = QListWidget(); self.site_list.currentItemChanged.connect(self.on_site_selected)
        left_layout.addWidget(QLabel("Sites")); left_layout.addWidget(self.site_list)
        splitter.addWidget(left_widget)

        right_widget = QWidget(); right_layout = QVBoxLayout(right_widget)
        self.param_table = QTableWidget(); self.param_table.setColumnCount(2)
        self.param_table.setHorizontalHeaderLabels(["Parameter ID", "Value"])
        self.param_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        right_layout.addWidget(QLabel("Selector Parameters")); right_layout.addWidget(self.param_table)
        splitter.addWidget(right_widget)
        splitter.setSizes([200, 500])

        btn_layout = QHBoxLayout()
        self.fetch_button = QPushButton("Fetch from IDM"); self.fetch_button.clicked.connect(self.start_selector_fetch)
        self.save_button = QPushButton("Save Selectors"); self.save_button.clicked.connect(self.save_selectors_from_ui)
        btn_layout.addWidget(self.fetch_button); btn_layout.addWidget(self.save_button)
        layout.addLayout(btn_layout)
        
        self.status_label = QLabel("Ready."); layout.addWidget(self.status_label)
        self.load_selectors_to_ui()

    def load_selectors_to_ui(self):
        self.site_list.clear()
        self.param_table.setRowCount(0)
        self.selector_rules = []
        if SELECTORS_FILE.exists():
            try:
                with open(SELECTORS_FILE, 'r', encoding='utf-8') as f:
                    self.selector_rules = json.load(f)
                for rule in self.selector_rules:
                    item = QListWidgetItem(rule.get("name", "Unknown Rule"))
                    item.setData(Qt.ItemDataRole.UserRole, rule)
                    self.site_list.addItem(item)
            except Exception as e:
                self.status_label.setText(f"Error loading selectors: {e}")
                self.create_default_selectors_file()
    
    def on_site_selected(self, current, previous):
        if previous: self.save_current_table_to_rule(previous)
        if not current:
            self.param_table.setRowCount(0)
            return
        
        rule = current.data(Qt.ItemDataRole.UserRole)
        params = rule.get("params", {})
        self.param_table.setRowCount(len(params))
        for i, (key, value) in enumerate(sorted(params.items(), key=lambda item: int(item[0]))):
            self.param_table.setItem(i, 0, QTableWidgetItem(str(key)))
            self.param_table.setItem(i, 1, QTableWidgetItem(str(value)))
        self.param_table.item(0,0).setFlags(self.param_table.item(0,0).flags() & ~Qt.ItemFlag.ItemIsEditable)

    def save_current_table_to_rule(self, item):
        if not item: return
        rule = item.data(Qt.ItemDataRole.UserRole)
        new_params = {}
        for i in range(self.param_table.rowCount()):
            key = self.param_table.item(i, 0).text()
            value = self.param_table.item(i, 1).text()
            new_params[key] = value
        rule["params"] = new_params
    
    def save_selectors_from_ui(self):
        current_item = self.site_list.currentItem()
        if current_item:
            self.save_current_table_to_rule(current_item)
        
        try:
            with open(SELECTORS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.selector_rules, f, indent=2)
            self.status_label.setText(f"Selectors saved to '{SELECTORS_FILE.name}'. Restart to apply.")
        except Exception as e:
            self.status_label.setText(f"ERROR: Could not save selectors: {e}")

    def start_selector_fetch(self):
        self.fetch_button.setEnabled(False); self.save_button.setEnabled(False)
        self.status_label.setText("Stopping local server to free port..."); QApplication.processEvents()
        self.ws_server.stop(); self.ws_server.wait()
        
        self.status_label.setText("Starting IDM service for fetching..."); QApplication.processEvents()
        if not start_idm_service(): self.on_fetch_error("Failed to start IDM service."); self._on_fetch_complete(); return
        
        self.fetcher_worker = UnifiedSelectorFetcher()
        self.fetcher_worker.signals.status.connect(self.status_label.setText)
        self.fetcher_worker.signals.finished.connect(self.on_fetch_finished)
        self.fetcher_worker.signals.error.connect(self.on_fetch_error)
        self.thread_pool.start(self.fetcher_worker)

    def _on_fetch_complete(self):
        self.status_label.setText("Fetch process finished. Cleaning up..."); QApplication.processEvents()
        stop_idm_service()
        self.ws_server.start()
        self.fetch_button.setEnabled(True); self.save_button.setEnabled(True)
        self.status_label.setText("Ready.")
        self.fetcher_worker = None

    def on_fetch_error(self, message):
        self.status_label.setText(f"ERROR: {message}"); QMessageBox.warning(self, "Fetch Error", message)
        self._on_fetch_complete()

    def on_fetch_finished(self, structured_selectors_list):
        self.status_label.setText(f"SUCCESS: Fetched {len(structured_selectors_list)} selector rules.")
        self.selector_rules = sorted(structured_selectors_list, key=lambda x: x.get('id', 0))
        self.site_list.clear()
        for rule in self.selector_rules:
            item = QListWidgetItem(rule.get("name", "Unknown Rule"))
            item.setData(Qt.ItemDataRole.UserRole, rule)
            self.site_list.addItem(item)
        self.save_selectors_from_ui()
        self._on_fetch_complete()

    def create_default_selectors_file(self):
        if not SELECTORS_FILE.exists():
            with open(SELECTORS_FILE, 'w') as f: json.dump([], f)

    def reject(self):
        if self.fetcher_worker: self.fetcher_worker.cancel()
        super().reject()

    def showEvent(self, event):
        self.is_minimized_to_tray = False
        super().showEvent(event)

    def closeEvent(self, event):
        self.is_minimized_to_tray = False
        super().closeEvent(event)

    def browse_folder(self, le):
        directory = QFileDialog.getExistingDirectory(self, "Select Folder", le.text())
        if directory: le.setText(directory)

    def accept(self):
        s = self.settings_manager.settings
        s["launch_on_startup"] = self.startup_cb.isChecked()
        s["clipboard_monitoring"] = self.clipboard_cb.isChecked()
        s["auto_download_types"] = self.auto_types_edit.toPlainText().split()
        s["dont_auto_download_sites"] = self.dont_sites_edit.toPlainText().split()

        cur_cat = self.category_combo.currentText()
        if cur_cat: s["save_paths"][cur_cat] = self.cat_dir_edit.text()
        s["change_folder_last_selected"] = self.change_folder_last_cb.isChecked()
        s["set_creation_date_from_server"] = self.creation_date_cb.isChecked()
        s["temp_dir"] = self.temp_dir_edit.text().strip()
        s["single_part_write_mode"] = self.single_part_mode_combo.currentText()

        s["show_start_dialog"] = self.show_start_dlg_cb.isChecked()
        s["queue_only"] = self.queue_only_cb.isChecked()
        s["show_complete_dialog"] = self.show_complete_dlg_cb.isChecked()
        s["start_immediately"] = self.start_immediately_cb.isChecked()
        s["show_queue_on_download_later"] = self.show_queue_dl_later_cb.isChecked()
        s["show_queue_on_batch_close"] = self.show_queue_batch_cb.isChecked()
        s["ignore_time_changes_on_resume"] = self.ignore_time_changes_cb.isChecked()
        s["duplicate_action"] = self.duplicate_combo.currentText()
        s["manual_user_agent"] = self.user_agent_edit.text().strip()

        try:
            conns = int(self.connections_combo.currentText().strip())
            s["max_connections"] = max(1, conns)
        except ValueError: s["max_connections"] = 32

        s["download_limits_enabled"] = self.enable_limits_cb.isChecked()
        s["download_limit_mbytes"] = self.limit_mb_spin.value()
        s["download_limit_hours"] = self.limit_hours_spin.value()
        s["show_limit_warning"] = self.limit_warning_cb.isChecked()

        s["use_browser_proxy_on_error"] = self.browser_proxy_cb.isChecked()
        if self.rb_system_proxy.isChecked(): s["proxy_mode"] = "system"
        elif self.rb_pac_proxy.isChecked(): s["proxy_mode"] = "pac"
        elif self.rb_manual_proxy.isChecked(): s["proxy_mode"] = "manual"
        else: s["proxy_mode"] = "none"

        s["pac_address"] = self.pac_address_edit.text().strip()
        s["manual_proxy_host"] = self.proxy_host_edit.text().strip()
        s["manual_proxy_port"] = self.proxy_port_edit.text().strip()
        s["manual_proxy_user"] = self.proxy_user_edit.text().strip()
        s["manual_proxy_pass"] = self.proxy_pass_edit.text().strip()
        s["ftp_pasv"] = self.ftp_pasv_cb.isChecked()

        s["panel_file_types"] = self.panel_types_widget.get_items()
        s["dont_capture_from_players"] = self.dont_capture_cb.isChecked()
        s["dont_show_panel_sites"] = self.dont_show_panel_widget.get_items()

        cap_dict = {}
        cust_list = []
        for i in range(self.browsers_list.count()):
            it = self.browsers_list.item(i)
            name = it.text()
            custom_path = it.data(Qt.ItemDataRole.UserRole)
            is_ch = it.checkState() == Qt.CheckState.Checked
            if custom_path:
                cust_list.append({"name": name, "path": custom_path, "enabled": is_ch})
            else:
                cap_dict[name] = is_ch
        s["captured_browsers"] = cap_dict
        s["custom_browsers"] = cust_list

        s["dial_up_vpn"] = {
            "enabled": self.dial_enabled_cb.isChecked(),
            "connection": self.dial_conn_combo.currentText(),
            "username": self.dial_user_edit.text().strip(),
            "password": self.dial_pass_edit.text().strip(),
            "save_password": self.dial_save_pass_cb.isChecked(),
            "redial_attempts": self.dial_redial_spin.value(),
            "redial_interval": self.dial_interval_spin.value(),
            "hang_up_when_done": self.dial_hangup_cb.isChecked()
        }

        self.settings_manager.save_settings()
        super().accept()

class ListManagementWidget(QWidget):
    def __init__(self, title, items, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        self.layout.addWidget(QLabel(title))
        self.list_widget = QListWidget()
        self.list_widget.addItems(items)
        self.layout.addWidget(self.list_widget)
        
        button_layout = QHBoxLayout()
        add_button = QPushButton("Add"); add_button.clicked.connect(self.add_item)
        edit_button = QPushButton("Edit"); edit_button.clicked.connect(self.edit_item)
        remove_button = QPushButton("Remove"); remove_button.clicked.connect(self.remove_item)
        button_layout.addWidget(add_button); button_layout.addWidget(edit_button); button_layout.addWidget(remove_button)
        self.layout.addLayout(button_layout)

    def add_item(self):
        text, ok = QInputDialog.getText(self, "Add Item", "Enter new item:")
        if ok and text: self.list_widget.addItem(text)

    def edit_item(self):
        selected = self.list_widget.currentItem()
        if selected:
            text, ok = QInputDialog.getText(self, "Edit Item", "Edit item:", QLineEdit.EchoMode.Normal, selected.text())
            if ok and text: selected.setText(text)

    def remove_item(self):
        selected = self.list_widget.currentItem()
        if selected: self.list_widget.takeItem(self.list_widget.row(selected))
    
    def get_items(self):
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

class PanelPreviewButton(QFrame):
    def __init__(self, mode="full_video", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.setStyleSheet("""
            PanelPreviewButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d5e6f6, stop:1 #9ec2e6);
                border: 1px solid #6b95be;
                border-radius: 3px;
            }
            QLabel {
                font-family: "Segoe UI", sans-serif;
                font-size: 11px;
                color: #0b3c68;
                font-weight: 600;
            }
            QFrame.sub_btn {
                background: #c3dcf5;
                border: 1px solid #7da6cf;
                border-radius: 2px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        play_lbl = QLabel("▶")
        play_lbl.setStyleSheet("color: #109419; font-size: 11px;")
        layout.addWidget(play_lbl)

        if self.mode == "full_video":
            self.setFixedSize(185, 25)
            layout.addWidget(QLabel("Download this video"))
            q_box = QLabel(" ? ")
            q_box.setStyleSheet("border: 1px solid #7da6cf; background: #cce3fa; border-radius: 2px; color: #222222; font-size: 10px;")
            x_box = QLabel(" ✕ ")
            x_box.setStyleSheet("border: 1px solid #7da6cf; background: #cce3fa; border-radius: 2px; color: #555555; font-size: 10px;")
            layout.addWidget(q_box)
            layout.addWidget(x_box)
        elif self.mode == "full_links":
            self.setFixedSize(185, 25)
            layout.addWidget(QLabel("Download with PyIDM"))
            q_box = QLabel(" ? ")
            q_box.setStyleSheet("border: 1px solid #7da6cf; background: #cce3fa; border-radius: 2px; color: #222222; font-size: 10px;")
            x_box = QLabel(" ✕ ")
            x_box.setStyleSheet("border: 1px solid #7da6cf; background: #cce3fa; border-radius: 2px; color: #555555; font-size: 10px;")
            layout.addWidget(q_box)
            layout.addWidget(x_box)
        else:
            self.setFixedSize(45, 25)
            x_box = QLabel(" ✕ ")
            x_box.setStyleSheet("border: 1px solid #7da6cf; background: #cce3fa; border-radius: 2px; color: #555555; font-size: 10px;")
            layout.addWidget(x_box)

class DownloadPanelExceptionsDialog(QDialog):
    def __init__(self, initial_text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download panel exceptions")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Don't show PyIDM download panel for the following sites:"))
        self.edit = QTextEdit()
        self.edit.setFixedHeight(75)
        self.edit.setPlainText(initial_text)
        layout.addWidget(self.edit)
        layout.addWidget(QLabel("Separate names by spaces. You may use asterisk as a wildcard pattern"))
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_text(self):
        return self.edit.toPlainText().strip()

class DownloadPanelsCustomizationDialog(QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Customize PyIDM Download panels in browsers")
        self.setMinimumSize(560, 480)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.setup_web_players_tab()
        self.setup_selected_files_tab()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def setup_web_players_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "For web-players")
        l = QVBoxLayout(tab)

        l.addWidget(QLabel("PyIDM can show its Download panel on a web-player in a browser when PyIDM detects a multimedia request\nfrom the web-player"))
        l.addWidget(QLabel("Panel view:"))

        self.wp_mode_group = QButtonGroup(self)
        self.wp_full_rb = QRadioButton("Full mode")
        self.wp_mini_rb = QRadioButton("Mini mode")
        self.wp_mode_group.addButton(self.wp_full_rb)
        self.wp_mode_group.addButton(self.wp_mini_rb)

        cfg = self.settings_manager.settings.get("download_panels_config", {})
        if cfg.get("web_players_view", "full") == "mini":
            self.wp_mini_rb.setChecked(True)
        else:
            self.wp_full_rb.setChecked(True)

        row_full = QHBoxLayout()
        row_full.addWidget(self.wp_full_rb)
        row_full.addSpacing(20)
        row_full.addWidget(PanelPreviewButton("full_video"))
        row_full.addStretch()
        l.addLayout(row_full)

        row_mini = QHBoxLayout()
        row_mini.addWidget(self.wp_mini_rb)
        row_mini.addSpacing(20)
        row_mini.addWidget(PanelPreviewButton("mini"))
        row_mini.addStretch()
        l.addLayout(row_mini)

        l.addWidget(QLabel("Show PyIDM download panel for the following file types:"))
        mid_row = QHBoxLayout()

        self.types_table = QTableWidget()
        self.types_table.setColumnCount(2)
        self.types_table.setHorizontalHeaderLabels(["File Type", "Minimum size"])
        self.types_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.types_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.types_table.setColumnWidth(1, 100)

        saved_rows = cfg.get("file_types_table", [])
        self.types_table.setRowCount(len(saved_rows))
        for i, row_data in enumerate(saved_rows):
            item_t = QTableWidgetItem(row_data.get("type", ""))
            item_t.setFlags(item_t.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item_t.setCheckState(Qt.CheckState.Checked if row_data.get("checked", True) else Qt.CheckState.Unchecked)
            self.types_table.setItem(i, 0, item_t)
            self.types_table.setItem(i, 1, QTableWidgetItem(row_data.get("min_size", "")))

        mid_row.addWidget(self.types_table, stretch=1)

        side_btns = QVBoxLayout()
        add_btn = QPushButton("Add")
        min_size_btn = QPushButton("Minimum size")
        check_all_btn = QPushButton("Check All")
        clear_all_btn = QPushButton("Clear All")
        exceptions_btn = QPushButton("Exceptions")

        add_btn.clicked.connect(self.add_file_type)
        min_size_btn.clicked.connect(self.edit_min_size)
        check_all_btn.clicked.connect(lambda: self.set_all_checked(True))
        clear_all_btn.clicked.connect(lambda: self.set_all_checked(False))
        exceptions_btn.clicked.connect(self.open_exceptions_dialog)

        side_btns.addWidget(add_btn)
        side_btns.addWidget(min_size_btn)
        side_btns.addWidget(check_all_btn)
        side_btns.addWidget(clear_all_btn)
        side_btns.addWidget(exceptions_btn)
        side_btns.addStretch()
        mid_row.addLayout(side_btns)
        l.addLayout(mid_row)

        note_lbl = QLabel("Note: If you uncheck a file type on the list above and if the file type is in the list on \"Options->File Types\"\ntab, downloads of this file type are captured by PyIDM automatically and won't be played in the web-player. If\nyou want to prevent it, check the box below:")
        note_lbl.setStyleSheet("font-size: 11px; color: #333333;")
        l.addWidget(note_lbl)

        self.dont_cap_cb = QCheckBox("Don't capture downloads from web-players automatically")
        self.dont_cap_cb.setChecked(self.settings_manager.settings.get("dont_capture_from_players", False))
        l.addWidget(self.dont_cap_cb)

        self.show_protected_cb = QCheckBox("Show download panel for protected content\nwhich PyIDM may not download")
        self.show_protected_cb.setChecked(cfg.get("show_for_protected", False))
        l.addWidget(self.show_protected_cb)

    def setup_selected_files_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "For selected files")
        l = QVBoxLayout(tab)

        l.addWidget(QLabel("PyIDM can show Download panel on a web-page when you select a text that contains download links."))
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine); sep1.setFrameShadow(QFrame.Shadow.Sunken)
        l.addWidget(sep1)

        l.addWidget(QLabel("Panel view:"))
        self.sf_mode_group = QButtonGroup(self)
        self.sf_full_rb = QRadioButton("Full mode")
        self.sf_mini_rb = QRadioButton("Mini mode")
        self.sf_mode_group.addButton(self.sf_full_rb)
        self.sf_mode_group.addButton(self.sf_mini_rb)

        cfg = self.settings_manager.settings.get("download_panels_config", {})
        if cfg.get("selected_files_view", "full") == "mini":
            self.sf_mini_rb.setChecked(True)
        else:
            self.sf_full_rb.setChecked(True)

        r1 = QHBoxLayout(); r1.addWidget(self.sf_full_rb); r1.addSpacing(20); r1.addWidget(PanelPreviewButton("full_links")); r1.addStretch()
        r2 = QHBoxLayout(); r2.addWidget(self.sf_mini_rb); r2.addSpacing(20); r2.addWidget(PanelPreviewButton("mini")); r2.addStretch()
        l.addLayout(r1); l.addLayout(r2)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setFrameShadow(QFrame.Shadow.Sunken)
        l.addWidget(sep2)

        self.rule_group = QButtonGroup(self)
        self.rb_any = QRadioButton("Show Download panel for any selected links")
        self.rb_none = QRadioButton("Don't show Download panel for selected links")
        self.rb_sites = QRadioButton("Show Download panel for selected links for the following sites only:")
        self.rule_group.addButton(self.rb_any)
        self.rule_group.addButton(self.rb_none)
        self.rule_group.addButton(self.rb_sites)

        sf_mode = cfg.get("selected_files_mode", "any")
        if sf_mode == "none": self.rb_none.setChecked(True)
        elif sf_mode == "sites_only": self.rb_sites.setChecked(True)
        else: self.rb_any.setChecked(True)

        l.addWidget(self.rb_any)
        l.addWidget(self.rb_none)
        l.addWidget(self.rb_sites)

        sites_box = QHBoxLayout()
        self.sf_sites_list = QListWidget()
        for site in cfg.get("selected_files_sites", []):
            self.sf_sites_list.addItem(site)
        sites_box.addWidget(self.sf_sites_list, stretch=1)

        side_b = QVBoxLayout()
        add_s_btn = QPushButton("Add")
        del_s_btn = QPushButton("Delete")
        add_s_btn.clicked.connect(self.add_sf_site)
        del_s_btn.clicked.connect(self.del_sf_site)
        side_b.addWidget(add_s_btn)
        side_b.addWidget(del_s_btn)
        side_b.addStretch()
        sites_box.addLayout(side_b)

        l.addLayout(sites_box)

        def on_rule_changed():
            en = self.rb_sites.isChecked()
            self.sf_sites_list.setEnabled(en)
            add_s_btn.setEnabled(en)
            del_s_btn.setEnabled(en)

        self.rb_any.toggled.connect(on_rule_changed)
        self.rb_none.toggled.connect(on_rule_changed)
        self.rb_sites.toggled.connect(on_rule_changed)
        on_rule_changed()

    def add_file_type(self):
        text, ok = QInputDialog.getText(self, "Add file type", "Enter file type extension (e.g. MKV):")
        if ok and text.strip():
            ext = text.strip().upper()
            row = self.types_table.rowCount()
            self.types_table.insertRow(row)
            item = QTableWidgetItem(ext)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.types_table.setItem(row, 0, item)
            self.types_table.setItem(row, 1, QTableWidgetItem(""))

    def edit_min_size(self):
        r = self.types_table.currentRow()
        if r >= 0:
            cur = self.types_table.item(r, 1).text() if self.types_table.item(r, 1) else ""
            text, ok = QInputDialog.getText(self, "Minimum size", "Enter minimum file size (e.g. 50.00 KB):", text=cur)
            if ok:
                self.types_table.setItem(r, 1, QTableWidgetItem(text.strip()))

    def set_all_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.types_table.rowCount()):
            it = self.types_table.item(r, 0)
            if it: it.setCheckState(state)

    def open_exceptions_dialog(self):
        cur = self.settings_manager.settings.get("download_panels_config", {}).get("exceptions", "")
        dlg = DownloadPanelExceptionsDialog(cur, self)
        if dlg.exec():
            cfg = self.settings_manager.settings.setdefault("download_panels_config", {})
            cfg["exceptions"] = dlg.get_text()
            self.settings_manager.settings["dont_show_panel_sites"] = dlg.get_text().split()

    def add_sf_site(self):
        text, ok = QInputDialog.getText(self, "Add site", "Enter site address:")
        if ok and text.strip():
            self.sf_sites_list.addItem(text.strip())

    def del_sf_site(self):
        it = self.sf_sites_list.currentItem()
        if it: self.sf_sites_list.takeItem(self.sf_sites_list.row(it))

    def save_and_accept(self):
        cfg = self.settings_manager.settings.setdefault("download_panels_config", {})
        cfg["web_players_view"] = "mini" if self.wp_mini_rb.isChecked() else "full"

        table_data = []
        active_exts = []
        for r in range(self.types_table.rowCount()):
            t_item = self.types_table.item(r, 0)
            s_item = self.types_table.item(r, 1)
            t_val = t_item.text() if t_item else ""
            s_val = s_item.text() if s_item else ""
            is_ch = (t_item.checkState() == Qt.CheckState.Checked) if t_item else False
            table_data.append({"type": t_val, "min_size": s_val, "checked": is_ch})
            if is_ch: active_exts.append(t_val)

        cfg["file_types_table"] = table_data
        cfg["show_for_protected"] = self.show_protected_cb.isChecked()
        self.settings_manager.settings["dont_capture_from_players"] = self.dont_cap_cb.isChecked()
        self.settings_manager.settings["panel_file_types"] = active_exts

        cfg["selected_files_view"] = "mini" if self.sf_mini_rb.isChecked() else "full"
        if self.rb_none.isChecked():
            cfg["selected_files_mode"] = "none"
        elif self.rb_sites.isChecked():
            cfg["selected_files_mode"] = "sites_only"
        else:
            cfg["selected_files_mode"] = "any"

        cfg["selected_files_sites"] = [self.sf_sites_list.item(i).text() for i in range(self.sf_sites_list.count())]
        self.settings_manager.save_settings()
        self.accept()

class ContextMenuCustomizationDialog(QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Customize PyIDM menu items in context menu of browsers")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        saved = self.settings_manager.settings.get("browser_context_menu", {})

        tab1 = QWidget()
        self.tabs.addTab(tab1, "Chrome, Edge, IE")
        l1 = QVBoxLayout(tab1)
        l1.addWidget(QLabel("Selected menu items will appear in browser context menu:"))
        self.chrome_dl_cb = QCheckBox("Download with PyIDM")
        self.chrome_dl_all_cb = QCheckBox("Download all links with PyIDM")
        self.chrome_dl_cb.setChecked(saved.get("chrome_download_with_idm", True))
        self.chrome_dl_all_cb.setChecked(saved.get("chrome_download_all_with_idm", True))
        l1.addWidget(self.chrome_dl_cb)
        l1.addWidget(self.chrome_dl_all_cb)
        l1.addStretch()

        tab2 = QWidget()
        self.tabs.addTab(tab2, "Firefox and other Mozilla based")
        l2 = QVBoxLayout(tab2)
        l2.addWidget(QLabel("Selected menu items will appear in browser context menu:"))
        self.ff_dl_cb = QCheckBox("Download with PyIDM")
        self.ff_dl_all_cb = QCheckBox("Download all links with PyIDM")
        self.ff_dl_cb.setChecked(saved.get("firefox_download_with_idm", True))
        self.ff_dl_all_cb.setChecked(saved.get("firefox_download_all_with_idm", True))
        l2.addWidget(self.ff_dl_cb)
        l2.addWidget(self.ff_dl_all_cb)
        l2.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def save_and_accept(self):
        s = self.settings_manager.settings.setdefault("browser_context_menu", {})
        s["chrome_download_with_idm"] = self.chrome_dl_cb.isChecked()
        s["chrome_download_all_with_idm"] = self.chrome_dl_all_cb.isChecked()
        s["firefox_download_with_idm"] = self.ff_dl_cb.isChecked()
        s["firefox_download_all_with_idm"] = self.ff_dl_all_cb.isChecked()
        self.settings_manager.save_settings()
        self.accept()

class SpecialKeysDialog(QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Using special keys")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)

        cfg = self.settings_manager.settings.get("special_keys", {})

        self.prevent_cb = QCheckBox("Use the following key(s) to prevent downloading with PyIDM for any links:")
        self.prevent_cb.setChecked(cfg.get("prevent_enabled", True))
        layout.addWidget(self.prevent_cb)

        p_keys_row = QHBoxLayout()
        p_keys_row.addSpacing(25)
        self.p_alt = QCheckBox("Alt"); self.p_alt.setChecked(cfg.get("prevent_alt", False))
        self.p_shift = QCheckBox("Shift"); self.p_shift.setChecked(cfg.get("prevent_shift", False))
        self.p_ctrl = QCheckBox("Ctrl"); self.p_ctrl.setChecked(cfg.get("prevent_ctrl", False))
        self.p_del = QCheckBox("Del"); self.p_del.setChecked(cfg.get("prevent_del", True))
        p_keys_row.addWidget(self.p_alt); p_keys_row.addWidget(self.p_shift); p_keys_row.addWidget(self.p_ctrl); p_keys_row.addWidget(self.p_del)
        p_keys_row.addStretch()
        layout.addLayout(p_keys_row)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine); sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)

        self.force_cb = QCheckBox("Use the following key(s) to force downloading with PyIDM for any links:")
        self.force_cb.setChecked(cfg.get("force_enabled", False))
        layout.addWidget(self.force_cb)

        f_keys_row = QHBoxLayout()
        f_keys_row.addSpacing(25)
        self.f_alt = QCheckBox("Alt"); self.f_alt.setChecked(cfg.get("force_alt", False))
        self.f_shift = QCheckBox("Shift"); self.f_shift.setChecked(cfg.get("force_shift", False))
        self.f_ctrl = QCheckBox("Ctrl"); self.f_ctrl.setChecked(cfg.get("force_ctrl", False))
        self.f_ins = QCheckBox("Ins"); self.f_ins.setChecked(cfg.get("force_ins", False))
        f_keys_row.addWidget(self.f_alt); f_keys_row.addWidget(self.f_shift); f_keys_row.addWidget(self.f_ctrl); f_keys_row.addWidget(self.f_ins)
        f_keys_row.addStretch()
        layout.addLayout(f_keys_row)

        n1_lbl = QLabel("Note: Sometimes after you click on a download link, a web page will load telling that the\ndownload starts in X seconds. In this case you will need to uncheck the following option to\nforce downloading with PyIDM.")
        n1_lbl.setStyleSheet("font-size: 11px; color: #444444;")
        layout.addWidget(n1_lbl)

        f_sub_row = QHBoxLayout()
        f_sub_row.addSpacing(20)
        self.force_lmb_cb = QCheckBox("Check for left mouse button clicked on a link along with the special\nkey(s) pressed to force downloading the link")
        self.force_lmb_cb.setChecked(cfg.get("force_check_lmb", False))
        f_sub_row.addWidget(self.force_lmb_cb)
        layout.addLayout(f_sub_row)

        n2_lbl = QLabel("Note 2:\nSometimes when you click on a link, your browser requests other files AT THE SAME TIME\nlike Java scripts, web pages, pictures, etc. When you press and hold down a special key, PyIDM\nintercepts these files, and the browser may not request the necessary file because it will not\nrun a Javascript, which has been intercepted by PyIDM erroneously. If you want to intercept\nand download only the necessary files with PyIDM, please turn on the option below:")
        n2_lbl.setStyleSheet("font-size: 11px; color: #444444;")
        layout.addWidget(n2_lbl)

        p_sub_row = QHBoxLayout()
        p_sub_row.addSpacing(20)
        self.prevent_aux_cb = QCheckBox("While holding down a special key DO NOT take over the downloads\nwhich are web-pages, pictures, scripts and etc.")
        self.prevent_aux_cb.setChecked(cfg.get("prevent_aux_downloads", True))
        p_sub_row.addWidget(self.prevent_aux_cb)
        layout.addLayout(p_sub_row)

        def sync_enable_states():
            pe = self.prevent_cb.isChecked()
            self.p_alt.setEnabled(pe); self.p_shift.setEnabled(pe); self.p_ctrl.setEnabled(pe); self.p_del.setEnabled(pe)
            fe = self.force_cb.isChecked()
            self.f_alt.setEnabled(fe); self.f_shift.setEnabled(fe); self.f_ctrl.setEnabled(fe); self.f_ins.setEnabled(fe)
            self.force_lmb_cb.setEnabled(fe)

        self.prevent_cb.toggled.connect(sync_enable_states)
        self.force_cb.toggled.connect(sync_enable_states)
        sync_enable_states()

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def save_and_accept(self):
        cfg = self.settings_manager.settings.setdefault("special_keys", {})
        cfg["prevent_enabled"] = self.prevent_cb.isChecked()
        cfg["prevent_alt"] = self.p_alt.isChecked()
        cfg["prevent_shift"] = self.p_shift.isChecked()
        cfg["prevent_ctrl"] = self.p_ctrl.isChecked()
        cfg["prevent_del"] = self.p_del.isChecked()
        cfg["force_enabled"] = self.force_cb.isChecked()
        cfg["force_alt"] = self.f_alt.isChecked()
        cfg["force_shift"] = self.f_shift.isChecked()
        cfg["force_ctrl"] = self.f_ctrl.isChecked()
        cfg["force_ins"] = self.f_ins.isChecked()
        cfg["force_check_lmb"] = self.force_lmb_cb.isChecked()
        cfg["prevent_aux_downloads"] = self.prevent_aux_cb.isChecked()
        self.settings_manager.save_settings()
        self.accept()

class FileConflictDialog(QDialog):
    def __init__(self, filename, new_filename, parent=None):
        super().__init__(parent); self.setWindowTitle("File Exists"); self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.layout = QVBoxLayout(self); self.layout.addWidget(QLabel(f"The file '{filename}' already exists. What would you like to do?"))
        self.buttons = QDialogButtonBox(self); self.overwrite_button = self.buttons.addButton("Overwrite", QDialogButtonBox.ButtonRole.AcceptRole)
        rename_text = new_filename.name;
        if len(rename_text) > 40: rename_text = rename_text[:37] + "..."
        self.rename_button = self.buttons.addButton(f"Rename as {rename_text}", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel); self.layout.addWidget(self.buttons)
        self.buttons.clicked.connect(self.handle_button_click); self.choice = "cancel"

    def handle_button_click(self, button):
        if button == self.overwrite_button: self.choice = "overwrite"; self.accept()
        elif button == self.rename_button: self.choice = "rename"; self.accept()
        elif button == self.cancel_button: self.choice = "cancel"; self.reject()

class DownloadDialog(QDialog):
    def __init__(self, download_data, settings_manager, parent=None):
        super().__init__(parent); self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint); self.settings_manager=settings_manager;self.download_data=download_data;self.url=download_data['params'].get(6, 'N/A')
        self.filename_edit = QLineEdit(Path(unquote(self.url.split('?')[0])).name or "download"); self.category=self.settings_manager.get_category_for_filename(self.filename_edit.text());default_path=self.settings_manager.get_path_for_filename(self.filename_edit.text());self.setWindowTitle("Download File Info");self.setMinimumWidth(600)
        layout=QFormLayout(self);self.url_label=QLineEdit(self.url);self.url_label.setReadOnly(True)
        self.path_edit=QLineEdit(default_path);browse_button=QPushButton("Browse...");browse_button.clicked.connect(self.browse_path);path_layout=QHBoxLayout();path_layout.addWidget(self.path_edit);path_layout.addWidget(browse_button)
        self.remember_checkbox=QCheckBox(f"Remember this location for {self.category} files"); layout.addRow("URL:", self.url_label);layout.addRow("Save to:",path_layout);layout.addRow("File name:",self.filename_edit);layout.addRow("",self.remember_checkbox)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel,Qt.Orientation.Horizontal,self);self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start Download");self.buttons.accepted.connect(self.accept_pressed);self.buttons.rejected.connect(self.reject);layout.addRow(self.buttons); self.result_details = None

    def browse_path(self):
        directory=QFileDialog.getExistingDirectory(self,"Select Download Folder",self.path_edit.text())
        if directory:self.path_edit.setText(directory)
    def accept_pressed(self):
        save_dir, filename = self.path_edit.text(), self.filename_edit.text()
        if not save_dir or not filename: QMessageBox.warning(self, "Invalid Input", "Save directory and filename cannot be empty."); return
        final_path = Path(save_dir) / filename
        if final_path.exists():
            new_path = get_unique_filename(final_path); conflict_dialog = FileConflictDialog(final_path.name, new_path, self)
            if conflict_dialog.exec():
                if conflict_dialog.choice == "overwrite": pass
                elif conflict_dialog.choice == "rename": final_path = new_path
                else: return
            else: return
        if self.remember_checkbox.isChecked(): self.settings_manager.settings["save_paths"][self.category] = self.path_edit.text(); self.settings_manager.save_settings()
        self.result_details = {'url': self.url, 'final_save_path': str(final_path), 'headers': self.download_data.get('headers', {})}; super().accept()

def format_time(seconds):
    if seconds is None or seconds < 0 or seconds > 86400 * 30: return "Unknown"
    s = int(seconds)
    hours, remainder = divmod(s, 3600)
    minutes, sec = divmod(remainder, 60)
    if hours > 0: return f"{hours} hr {minutes} min {sec} sec"
    if minutes > 0: return f"{minutes} min {sec} sec"
    return f"{sec} sec"

class ConnectionBarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.conn_data = []
        self.total_size = 0
        self.setStyleSheet("background-color: #f7f9fa; border: 1px solid #7a9ec2;")

    def update_data(self, conn_data, total_size):
        self.conn_data = conn_data
        self.total_size = total_size
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor("#e8edf2"))

        if not self.conn_data:
            painter.setPen(QColor("#5b7a99"))
            painter.drawRect(0, 0, w - 1, h - 1)
            return

        is_chunk_based = any('chunk_status' in item for item in self.conn_data)

        if is_chunk_based:
            total_items = len(self.conn_data)
            seg_w = w / max(1, total_items)
            for i, item in enumerate(self.conn_data):
                status = item.get('chunk_status', 'pending')
                x = int(i * seg_w)
                part_w = max(1, int((i + 1) * seg_w) - x)

                if status == 'done':
                    grad = QLinearGradient(0, 1, 0, h - 2)
                    grad.setColorAt(0.0, QColor("#2979ff"))
                    grad.setColorAt(0.5, QColor("#1565c0"))
                    grad.setColorAt(1.0, QColor("#0d47a1"))
                    painter.fillRect(x, 1, part_w, h - 2, grad)
                elif status == 'downloading':
                    grad = QLinearGradient(0, 1, 0, h - 2)
                    grad.setColorAt(0.0, QColor("#80d8ff"))
                    grad.setColorAt(0.5, QColor("#00b0ff"))
                    grad.setColorAt(1.0, QColor("#0091ea"))
                    painter.fillRect(x, 1, part_w, h - 2, grad)
        else:
            if self.total_size > 0:
                for item in self.conn_data:
                    start = item.get('start', 0)
                    end = item.get('end', 0)
                    downloaded = item.get('downloaded', 0)
                    status = item.get('status', '')
                    if end <= start: continue

                    x1 = int((start / self.total_size) * w)
                    x2 = int((end / self.total_size) * w)
                    part_w = max(1, x2 - x1)
                    painter.fillRect(x1, 1, part_w, h - 2, QColor("#dfe6ed"))

                    dl_end = min(end, start + downloaded)
                    dl_w = int(((dl_end - start) / self.total_size) * w)
                    if dl_w > 0:
                        grad = QLinearGradient(0, 1, 0, h - 2)
                        if status == "Receiving data...":
                            grad.setColorAt(0.0, QColor("#4fa5f5"))
                            grad.setColorAt(0.5, QColor("#1c6ec7"))
                            grad.setColorAt(1.0, QColor("#0d47a1"))
                        else:
                            grad.setColorAt(0.0, QColor("#1976d2"))
                            grad.setColorAt(1.0, QColor("#0d47a1"))
                        painter.fillRect(x1, 1, dl_w, h - 2, grad)

                    painter.setPen(QColor("#7a9ec2"))
                    painter.drawLine(x1, 0, x1, h)

        painter.setPen(QColor("#5b7a99"))
        painter.drawRect(0, 0, w - 1, h - 1)

class DownloadProgressTitleBar(QWidget):
    def __init__(self, parent_dialog):
        super().__init__(parent_dialog)
        self.dialog = parent_dialog
        self.setFixedHeight(30)
        self.setStyleSheet("""
            QWidget { background-color: #f2f2f2; }
            QLabel { font-family: "Segoe UI", sans-serif; font-size: 12px; color: #111111; padding-left: 4px; }
            QPushButton {
                border: none;
                font-family: "Segoe UI", sans-serif;
                font-size: 12px;
                background-color: transparent;
                width: 34px;
                height: 28px;
            }
            QPushButton:hover { background-color: #e2e2e2; }
            QPushButton#closeBtn:hover { background-color: #e81123; color: white; }
            QPushButton#trayBtn {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                border-radius: 2px;
                margin: 3px 2px;
                width: 26px;
                height: 22px;
            }
            QPushButton#trayBtn:hover { background-color: #2ecc71; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 2, 0)
        layout.setSpacing(2)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown).pixmap(16, 16))
        layout.addWidget(self.icon_label)

        self.title_label = QLabel("Download progress")
        layout.addWidget(self.title_label, stretch=1)

        self.tray_btn = QPushButton("➔")
        self.tray_btn.setObjectName("trayBtn")
        self.tray_btn.setToolTip("Minimize to system tray")

        self.min_btn = QPushButton("🗕")
        self.min_btn.setToolTip("Minimize")

        self.max_btn = QPushButton("🗖")
        self.max_btn.setToolTip("Maximize")

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setToolTip("Close")

        layout.addWidget(self.tray_btn)
        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        self.tray_btn.clicked.connect(self.dialog.minimize_to_tray)
        self.min_btn.clicked.connect(self.dialog.showMinimized)
        self.max_btn.clicked.connect(self.dialog.toggle_maximize)
        self.close_btn.clicked.connect(self.dialog.close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dialog._drag_pos = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self.dialog, '_drag_pos') and self.dialog._drag_pos:
            self.dialog.move(event.globalPosition().toPoint() - self.dialog._drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dialog.toggle_maximize()

class DownloadProgressDialog(QDialog):
    status_changed = pyqtSignal(int, str)

    def __init__(self, row, download_item, worker, settings_manager, tray_icon=None, parent=None):
        super().__init__(parent)
        self.row = row
        self.download_item = download_item
        self.worker = worker
        self.settings_manager = settings_manager
        self.tray_icon = tray_icon
        self.start_timestamp = time.time()
        self.total_size = download_item.get('total_size', 0)
        self.downloaded_bytes = 0
        self._resize_edge = None
        self._drag_pos = None
        self.is_minimized_to_tray = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setMouseTracking(True)
        self.setMinimumSize(560, 480)
        self.setStyleSheet("""
            QDialog {
                border: 1px solid #7a9ec2;
                background-color: #f7f9fa;
            }
        """)

        self.init_ui()
        self.setup_connections()
        self.update_title_text(f"0% {self.download_item.get('filename', 'download')}")

    def update_title_text(self, text):
        self.setWindowTitle(text)
        if hasattr(self, 'title_bar'):
            self.title_bar.title_label.setText(text)

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.title_bar.max_btn.setText("🗖")
        else:
            self.showMaximized()
            self.title_bar.max_btn.setText("🗗")

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(4)

        self.title_bar = DownloadProgressTitleBar(self)
        main_layout.addWidget(self.title_bar, stretch=0)

        self.tab_widget = QTabWidget()
        self.tab_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        status_tab = QWidget()
        s_layout = QVBoxLayout(status_tab)
        s_layout.setContentsMargins(8, 6, 8, 6)
        s_layout.setSpacing(4)

        self.url_label = QLineEdit(self.download_item.get('url', ''))
        self.url_label.setReadOnly(True)
        self.url_label.setStyleSheet("background: transparent; border: none; color: #333333;")
        s_layout.addWidget(self.url_label)

        f_layout = QFormLayout()
        f_layout.setContentsMargins(0, 4, 0, 4)
        f_layout.setVerticalSpacing(4)
        self.status_val = QLabel("Receiving data...")
        self.status_val.setStyleSheet("color: #0000cc; font-weight: bold;")
        self.duration_val = QLabel("0 sec")
        self.downloaded_val = QLabel("0 B ( 0 % )")
        self.transfer_rate_val = QLabel("0.000 MB/sec")
        self.time_left_val = QLabel("Calculating...")
        self.resume_val = QLabel("Yes" if getattr(self.worker, 'supports_ranges', False) else "No")

        f_layout.addRow("Status", self.status_val)
        f_layout.addRow("Duration", self.duration_val)
        f_layout.addRow("Downloaded", self.downloaded_val)
        f_layout.addRow("Transfer rate", self.transfer_rate_val)
        f_layout.addRow("Time left", self.time_left_val)
        f_layout.addRow("Resume capability", self.resume_val)
        s_layout.addLayout(f_layout)
        self.tab_widget.addTab(status_tab, "Download status")

        limiter_tab = QWidget()
        l_layout = QVBoxLayout(limiter_tab)
        l_form = QFormLayout()
        self.limiter_rate_val = QLabel("0.000 MB/sec")
        l_form.addRow("Transfer rate", self.limiter_rate_val)
        self.use_limiter_cb = QCheckBox("Use Speed Limiter")
        self.limit_speed_spin = QSpinBox()
        self.limit_speed_spin.setRange(1, 1000000)
        self.limit_speed_spin.setValue(100)
        self.limit_speed_spin.setSuffix(" KBytes/sec")
        self.limit_speed_spin.setEnabled(False)
        self.use_limiter_cb.toggled.connect(self.limit_speed_spin.setEnabled)
        self.use_limiter_cb.toggled.connect(self.apply_speed_limit)
        self.limit_speed_spin.valueChanged.connect(self.apply_speed_limit)

        self.remember_limiter_cb = QCheckBox("Remember Speed Limiter settings for this file on download stop/resume")
        hide_tab_btn1 = QPushButton("Hide tab")
        hide_tab_btn1.setFixedWidth(80)
        hide_tab_btn1.clicked.connect(lambda: self.tab_widget.setCurrentIndex(0))

        l_layout.addLayout(l_form)
        l_layout.addWidget(self.use_limiter_cb)
        limit_input_layout = QHBoxLayout()
        limit_input_layout.addWidget(QLabel("Maximum download speed:"))
        limit_input_layout.addWidget(self.limit_speed_spin)
        limit_input_layout.addStretch()
        l_layout.addLayout(limit_input_layout)
        l_layout.addWidget(self.remember_limiter_cb)
        btn_box1 = QHBoxLayout()
        btn_box1.addStretch()
        btn_box1.addWidget(hide_tab_btn1)
        l_layout.addLayout(btn_box1)
        self.tab_widget.addTab(limiter_tab, "Speed Limiter")

        options_tab = QWidget()
        o_layout = QVBoxLayout(options_tab)
        self.save_to_label = QLabel(f"Save To: {self.download_item.get('final_path', '')}")
        self.save_to_label.setStyleSheet("font-weight: 500;")
        self.show_complete_dialog_cb = QCheckBox("Show download complete dialog")
        self.show_complete_dialog_cb.setChecked(self.settings_manager.settings.get("show_complete_dialog", True))

        o_separator = QFrame(); o_separator.setFrameShape(QFrame.Shape.HLine); o_separator.setFrameShadow(QFrame.Shadow.Sunken)
        self.unavail_label = QLabel("These settings are unavailable when \"Show download complete dialog\" is turned on")
        self.unavail_label.setStyleSheet("color: #777777; font-size: 11px;")

        self.exit_app_cb = QCheckBox("Exit PyIDM when done")
        self.shutdown_cb = QCheckBox("Turn off computer when done")
        self.shutdown_action_combo = QComboBox()
        self.shutdown_action_combo.addItems(["Shut down", "Restart", "Sleep", "Hibernate"])
        self.force_terminate_cb = QCheckBox("Force processes to terminate")

        def toggle_completion_options():
            enabled = not self.show_complete_dialog_cb.isChecked()
            self.exit_app_cb.setEnabled(enabled)
            self.shutdown_cb.setEnabled(enabled)
            self.shutdown_action_combo.setEnabled(enabled and self.shutdown_cb.isChecked())
            self.force_terminate_cb.setEnabled(enabled and self.shutdown_cb.isChecked())

        self.show_complete_dialog_cb.toggled.connect(toggle_completion_options)
        self.shutdown_cb.toggled.connect(toggle_completion_options)
        toggle_completion_options()

        hide_tab_btn2 = QPushButton("Hide tab")
        hide_tab_btn2.setFixedWidth(80)
        hide_tab_btn2.clicked.connect(lambda: self.tab_widget.setCurrentIndex(0))

        o_layout.addWidget(self.save_to_label)
        o_layout.addWidget(self.show_complete_dialog_cb)
        o_layout.addWidget(o_separator)
        o_layout.addWidget(self.unavail_label)
        o_layout.addWidget(self.exit_app_cb)
        shut_layout = QHBoxLayout()
        shut_layout.addWidget(self.shutdown_cb)
        shut_layout.addWidget(self.shutdown_action_combo)
        shut_layout.addStretch()
        o_layout.addLayout(shut_layout)
        o_layout.addWidget(self.force_terminate_cb)
        btn_box2 = QHBoxLayout()
        btn_box2.addStretch()
        btn_box2.addWidget(hide_tab_btn2)
        o_layout.addLayout(btn_box2)
        self.tab_widget.addTab(options_tab, "Options on completion")

        main_layout.addWidget(self.tab_widget, stretch=0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(22)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #7a9ec2;
                background-color: #e6ebf0;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4bd838, stop:0.5 #28a71a, stop:1 #1c7e12);
            }
        """)
        main_layout.addWidget(self.progress_bar, stretch=0)

        btn_row = QHBoxLayout()
        self.toggle_details_btn = QPushButton("<< Hide details")
        self.toggle_details_btn.clicked.connect(self.toggle_details)
        btn_row.addWidget(self.toggle_details_btn)
        btn_row.addStretch()

        self.pause_resume_btn = QPushButton("Pause")
        self.pause_resume_btn.setFixedWidth(85)
        self.pause_resume_btn.clicked.connect(self.toggle_pause_resume)
        btn_row.addWidget(self.pause_resume_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedWidth(85)
        self.cancel_btn.clicked.connect(self.cancel_download)
        btn_row.addWidget(self.cancel_btn)
        main_layout.addLayout(btn_row)

        self.details_container = QWidget()
        self.details_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        d_layout = QVBoxLayout(self.details_container)
        d_layout.setContentsMargins(6, 4, 6, 6)
        d_layout.setSpacing(4)

        d_layout.addWidget(QLabel("Start positions and download progress by connections"))
        self.conn_bar = ConnectionBarWidget()
        d_layout.addWidget(self.conn_bar)

        self.conn_table = QTableWidget()
        self.conn_table.verticalHeader().setVisible(False)
        self.conn_table.setColumnCount(3)
        self.conn_table.setHorizontalHeaderLabels(["N.", "Downloaded", "Info"])
        self.conn_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.conn_table.setColumnWidth(0, 42)
        self.conn_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.conn_table.setColumnWidth(1, 130)
        self.conn_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.conn_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.conn_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.conn_table.setStyleSheet("QTableWidget { background-color: #fafbfc; alternate-background-color: #f0f3f6; }")
        self.conn_table.setAlternatingRowColors(True)
        d_layout.addWidget(self.conn_table, stretch=1)

        main_layout.addWidget(self.details_container, stretch=1)

    def _get_edge(self, pt):
        margin = 8
        w = self.width()
        h = self.height()
        left = pt.x() <= margin
        right = pt.x() >= w - margin
        top = pt.y() <= margin
        bottom = pt.y() >= h - margin

        if top and left: return 'top_left'
        if top and right: return 'top_right'
        if bottom and left: return 'bottom_left'
        if bottom and right: return 'bottom_right'
        if left: return 'left'
        if right: return 'right'
        if top: return 'top'
        if bottom: return 'bottom'
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._get_edge(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._drag_start_geo = self.geometry()
                self._drag_start_pos = event.globalPosition().toPoint()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if not event.buttons():
            edge = self._get_edge(pos)
            if edge in ('top', 'bottom'): self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif edge in ('left', 'right'): self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif edge in ('top_left', 'bottom_right'): self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif edge in ('top_right', 'bottom_left'): self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else: self.unsetCursor()
            event.accept()
        elif event.buttons() == Qt.MouseButton.LeftButton and self._resize_edge:
            diff = event.globalPosition().toPoint() - self._drag_start_pos
            geo = QRect(self._drag_start_geo)
            min_w = self.minimumWidth()
            min_h = self.minimumHeight()

            if 'right' in self._resize_edge:
                geo.setWidth(max(min_w, self._drag_start_geo.width() + diff.x()))
            if 'bottom' in self._resize_edge:
                geo.setHeight(max(min_h, self._drag_start_geo.height() + diff.y()))
            if 'left' in self._resize_edge:
                new_w = max(min_w, self._drag_start_geo.width() - diff.x())
                geo.setLeft(self._drag_start_geo.right() - new_w)
            if 'top' in self._resize_edge:
                new_h = max(min_h, self._drag_start_geo.height() - diff.y())
                geo.setTop(self._drag_start_geo.bottom() - new_h)

            self.setGeometry(geo)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edge = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def setup_connections(self):
        self.worker.signals.file_info.connect(self.on_file_info)
        self.worker.signals.progress.connect(self.on_progress)
        self.worker.signals.finished.connect(self.on_finished)
        self.worker.signals.error.connect(self.on_error)
        self.worker.signals.connection_status.connect(self.on_connection_status)

    def apply_speed_limit(self):
        enabled = self.use_limiter_cb.isChecked()
        kbps = self.limit_speed_spin.value()
        self.worker.set_speed_limit(enabled, kbps)

    def toggle_pause_resume(self):
        if not self.worker.is_paused:
            self.worker.pause()
            self.download_item['status'] = 'Paused'
            self.download_item['speed'] = 0
            self.pause_resume_btn.setText("Start")
            self.status_val.setText("Paused")
            self.status_val.setStyleSheet("color: #cc6600; font-weight: bold;")
            self.status_changed.emit(self.row, "Paused")
        else:
            self.worker.resume()
            self.download_item['status'] = 'Downloading'
            self.pause_resume_btn.setText("Pause")
            self.status_val.setText("Receiving data...")
            self.status_val.setStyleSheet("color: #0000cc; font-weight: bold;")
            self.status_changed.emit(self.row, "Downloading")

    def cancel_download(self):
        self.is_minimized_to_tray = False
        self.worker.cancel()
        self.download_item['status'] = 'Cancelled'
        self.download_item['progress'] = 0
        self.download_item['speed'] = 0
        self.status_val.setText("Cancelled")
        self.progress_bar.setValue(0)
        self.downloaded_val.setText("0 B ( 0 % )")
        self.transfer_rate_val.setText("0.000 MB/sec")
        self.pause_resume_btn.setText("Start")
        self.pause_resume_btn.setEnabled(False)
        self.status_changed.emit(self.row, "Cancelled")
        self.close()

    def toggle_details(self):
        if self.details_container.isVisible():
            self.details_container.hide()
            self.toggle_details_btn.setText(">> Show details")
            self.adjustSize()
        else:
            self.details_container.show()
            self.toggle_details_btn.setText("<< Hide details")

    def minimize_to_tray(self):
        self.is_minimized_to_tray = True
        self.hide()
        if self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                APP_NAME,
                f"Downloading '{self.download_item.get('filename')}' in background.",
                QSystemTrayIcon.MessageIcon.Information, 1500
            )

    def on_file_info(self, row, filename, total_size):
        if row != self.row: return
        self.total_size = total_size
        self.download_item['filename'] = filename
        self.download_item['total_size'] = total_size
        self.save_to_label.setText(f"Save To: {self.download_item.get('final_path', '')}")

        can_resume = getattr(self.worker, 'supports_ranges', False)
        self.resume_val.setText("Yes" if can_resume else "No")
        self.resume_val.setStyleSheet("color: green;" if can_resume else "color: red;")
        self.progress_bar.setRange(0, 100)

    def on_progress(self, row, downloaded, speed_mbps):
        if row != self.row: return
        self.downloaded_bytes = downloaded
        elapsed = time.time() - self.start_timestamp
        self.duration_val.setText(format_time(elapsed))

        pct = 0.0
        if hasattr(self.worker, 'current_pct'):
            pct = self.worker.current_pct
        elif self.total_size > 0:
            pct = min(100.0, (downloaded / self.total_size) * 100.0)

        self.progress_bar.setValue(int(pct))
        self.downloaded_val.setText(f"{format_bytes(downloaded)} ( {pct:.2f} % )")

        if pct > 0:
            est_total_time = (elapsed / pct) * 100.0
            time_left = max(0.0, est_total_time - elapsed)
            self.time_left_val.setText(format_time(time_left))
        else:
            self.time_left_val.setText("Calculating...")

        rate_str = f"{speed_mbps:.3f} MB/sec"
        self.transfer_rate_val.setText(rate_str)
        self.limiter_rate_val.setText(rate_str)
        self.update_title_text(f"{int(pct)}% {self.download_item.get('filename', 'download')}")

    def on_connection_status(self, row, conn_data):
        if row != self.row or not conn_data: return

        if isinstance(conn_data, dict):
            connections = conn_data.get('connections', [])
            chunks = conn_data.get('chunks', [])
            self.conn_bar.update_data(chunks, self.total_size)
        else:
            connections = conn_data
            self.conn_bar.update_data(conn_data, self.total_size)

        if self.conn_table.rowCount() != len(connections):
            self.conn_table.setRowCount(len(connections))
            for i in range(len(connections)):
                c = connections[i]
                self.conn_table.setItem(i, 0, QTableWidgetItem(str(c.get('num', i + 1))))
                self.conn_table.setItem(i, 1, QTableWidgetItem(format_bytes(c.get('downloaded', 0))))
                self.conn_table.setItem(i, 2, QTableWidgetItem(str(c.get('status', ''))))
        else:
            for i, c in enumerate(connections):
                item_dl = self.conn_table.item(i, 1)
                item_st = self.conn_table.item(i, 2)
                if item_dl: item_dl.setText(format_bytes(c.get('downloaded', 0)))
                if item_st: item_st.setText(str(c.get('status', '')))

    def on_finished(self, row, status, final_path):
        if row != self.row: return
        self.status_val.setText(status)
        self.pause_resume_btn.setEnabled(False)
        self.cancel_btn.setText("Close")
        if status == "Completed":
            self.progress_bar.setValue(100)
            self.update_title_text(f"100% {self.download_item.get('filename', 'download')}")
            if self.show_complete_dialog_cb.isChecked():
                self.close()
                comp_dialog = DownloadCompleteDialog(self.download_item, None)
                comp_dialog.exec()
            else:
                self.close()

            if self.exit_app_cb.isChecked():
                QApplication.quit()
            elif self.shutdown_cb.isChecked():
                action = self.shutdown_action_combo.currentText()
                force = " /f" if self.force_terminate_cb.isChecked() else ""
                if sys.platform == 'win32':
                    if action == "Shut down": subprocess.run(f"shutdown /s /t 30{force}", shell=True)
                    elif action == "Restart": subprocess.run(f"shutdown /r /t 30{force}", shell=True)
                    elif action == "Hibernate": subprocess.run(f"shutdown /h", shell=True)

    def on_error(self, row, message):
        if row != self.row: return
        self.status_val.setText("Error")
        self.status_val.setStyleSheet("color: red; font-weight: bold;")

class DownloadCompleteDialog(QDialog):
    def __init__(self, download_item, parent=None):
        super().__init__(parent)
        self.download_item = download_item
        self.setWindowTitle("Download complete")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(450)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info_box = QFormLayout()
        info_box.addRow("File:", QLabel(self.download_item.get('filename', '')))
        info_box.addRow("Size:", QLabel(format_bytes(self.download_item.get('total_size', 0))))
        info_box.addRow("Save to:", QLabel(self.download_item.get('final_path', '')))
        layout.addLayout(info_box)

        btn_layout = QHBoxLayout()
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(self.open_file)
        open_folder_btn = QPushButton("Open folder")
        open_folder_btn.clicked.connect(self.open_folder)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        btn_layout.addWidget(open_btn)
        btn_layout.addWidget(open_folder_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def open_file(self):
        p = Path(self.download_item.get('final_path', ''))
        if p.exists():
            if platform.system() == "Windows": os.startfile(p)
            elif platform.system() == "Darwin": subprocess.run(["open", str(p)])
            else: subprocess.run(["xdg-open", str(p)])
        self.accept()

    def open_folder(self):
        p = Path(self.download_item.get('final_path', ''))
        folder = p.parent
        if folder.exists():
            if platform.system() == "Windows": subprocess.run(f'explorer /select,"{p}"', shell=True)
            elif platform.system() == "Darwin": subprocess.run(["open", "-R", str(p)])
            else: subprocess.run(["xdg-open", str(folder)])
        self.accept()

class DuplicateDialog(QDialog):
    def __init__(self, filename, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicate download link")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.selected_choice = "cancel"

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"The download file '{filename}' already exists or is being downloaded.\nWhat would you like to do?"))

        self.btn_numbered = QPushButton("Add with a numbered file name")
        self.btn_overwrite = QPushButton("Overwrite existing file")
        self.btn_cancel = QPushButton("Cancel")

        self.btn_numbered.clicked.connect(lambda: self.set_choice("numbered"))
        self.btn_overwrite.clicked.connect(lambda: self.set_choice("overwrite"))
        self.btn_cancel.clicked.connect(lambda: self.set_choice("cancel"))

        layout.addWidget(self.btn_numbered)
        layout.addWidget(self.btn_overwrite)
        layout.addWidget(self.btn_cancel)

    def set_choice(self, choice):
        self.selected_choice = choice
        self.accept()

class AddUrlDialog(QDialog):
    def __init__(self, default_url="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enter new address to download")
        self.setFixedWidth(500)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit(default_url)
        form.addRow("Address:", self.url_edit)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_url(self):
        return self.url_edit.text().strip()

class BatchDownloadDialog(QDialog):
    def __init__(self, initial_urls=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add batch download")
        self.setMinimumSize(550, 350)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter download addresses (one URL per line):"))
        self.text_edit = QTextEdit()
        if initial_urls:
            self.text_edit.setPlainText("\n".join(initial_urls))
        layout.addWidget(self.text_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_urls(self):
        lines = self.text_edit.toPlainText().strip().splitlines()
        return [l.strip() for l in lines if l.strip()]

class SpeedLimiterSettingsDialog(QDialog):
    def __init__(self, current_limit=10, startup_check=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Speed Limiter settings")
        self.setFixedWidth(450)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        row = QHBoxLayout()
        row.addWidget(QLabel("Maximum download speed for one file"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 1000000)
        self.spin.setValue(current_limit)
        row.addWidget(self.spin)
        row.addWidget(QLabel("KBytes/sec"))
        row.addStretch()
        layout.addLayout(row)

        note = QLabel("Note: Internet servers may break connection when the speed is too limited! Thus it's not recommended to use Speed Limiter to download from servers that do not support \"resume\" feature.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #333333; font-size: 11px;")
        layout.addWidget(note)

        self.startup_cb = QCheckBox("Always turn on Speed Limiter on IDM startup")
        self.startup_cb.setChecked(startup_check)
        layout.addWidget(self.startup_cb)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_settings(self):
        return self.spin.value(), self.startup_cb.isChecked()

class CustomizeToolbarDialog(QDialog):
    def __init__(self, available, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Customize Toolbar")
        self.setFixedSize(520, 340)
        layout = QVBoxLayout(self)

        mid_layout = QHBoxLayout()
        left_box = QVBoxLayout()
        left_box.addWidget(QLabel("Available toolbar buttons:"))
        self.avail_list = QListWidget()
        self.avail_list.addItems(available)
        left_box.addWidget(self.avail_list)
        mid_layout.addLayout(left_box)

        mid_btns = QVBoxLayout()
        mid_btns.addStretch()
        self.add_btn = QPushButton("Add ->")
        self.rem_btn = QPushButton("<- Remove")
        mid_btns.addWidget(self.add_btn)
        mid_btns.addWidget(self.rem_btn)
        mid_btns.addStretch()
        mid_layout.addLayout(mid_btns)

        right_box = QVBoxLayout()
        right_box.addWidget(QLabel("Current toolbar buttons:"))
        self.curr_list = QListWidget()
        self.curr_list.addItems(current)
        right_box.addWidget(self.curr_list)
        mid_layout.addLayout(right_box)

        side_btns = QVBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        self.reset_btn = QPushButton("Reset")
        self.up_btn = QPushButton("Move Up")
        self.down_btn = QPushButton("Move Down")

        side_btns.addWidget(close_btn)
        side_btns.addWidget(self.reset_btn)
        side_btns.addStretch()
        side_btns.addWidget(self.up_btn)
        side_btns.addWidget(self.down_btn)
        mid_layout.addLayout(side_btns)

        layout.addLayout(mid_layout)

        self.add_btn.clicked.connect(self._add_item)
        self.rem_btn.clicked.connect(self._remove_item)
        self.up_btn.clicked.connect(self._move_up)
        self.down_btn.clicked.connect(self._move_down)

    def _add_item(self):
        it = self.avail_list.currentItem()
        if it: self.curr_list.addItem(it.text())
    def _remove_item(self):
        it = self.curr_list.currentItem()
        if it: self.curr_list.takeItem(self.curr_list.row(it))
    def _move_up(self):
        r = self.curr_list.currentRow()
        if r > 0:
            it = self.curr_list.takeItem(r)
            self.curr_list.insertItem(r - 1, it)
            self.curr_list.setCurrentRow(r - 1)
    def _move_down(self):
        r = self.curr_list.currentRow()
        if r >= 0 and r < self.curr_list.count() - 1:
            it = self.curr_list.takeItem(r)
            self.curr_list.insertItem(r + 1, it)
            self.curr_list.setCurrentRow(r + 1)
    def get_current(self):
        return [self.curr_list.item(i).text() for i in range(self.curr_list.count())]

class CustomizeColumnsDialog(QDialog):
    ALL_COLUMNS = ["File Name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last Try Date", "Description", "Date Added", "Save To", "Referer", "Parent web page"]

    def __init__(self, visible_cols, widths_dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Columns")
        self.setFixedSize(450, 420)
        self.widths_dict = dict(widths_dict)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Check the columns that you would like visible in this list. Use the\nMove Up and Move Down buttons to reorder the columns however\nyou like."))

        mid = QHBoxLayout()
        self.col_list = QListWidget()

        ordered = [c for c in visible_cols if c in self.ALL_COLUMNS] + [c for c in self.ALL_COLUMNS if c not in visible_cols]
        for c in ordered:
            item = QListWidgetItem(c)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c in visible_cols else Qt.CheckState.Unchecked)
            self.col_list.addItem(item)
        mid.addWidget(self.col_list, stretch=1)

        side_layout = QVBoxLayout()
        self.btn_up = QPushButton("Move Up")
        self.btn_down = QPushButton("Move Down")
        self.btn_show = QPushButton("Show")
        self.btn_hide = QPushButton("Hide")
        self.btn_reset = QPushButton("Reset")

        side_layout.addWidget(self.btn_up)
        side_layout.addWidget(self.btn_down)
        side_layout.addWidget(self.btn_show)
        side_layout.addWidget(self.btn_hide)
        side_layout.addStretch()
        side_layout.addWidget(self.btn_reset)
        mid.addLayout(side_layout)
        layout.addLayout(mid)

        w_row = QHBoxLayout()
        w_row.addStretch()
        w_row.addWidget(QLabel("The selected column should be"))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(20, 2000)
        self.width_spin.setValue(100)
        w_row.addWidget(self.width_spin)
        w_row.addWidget(QLabel("pixels wide"))
        layout.addLayout(w_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.btn_up.clicked.connect(self._move_up)
        self.btn_down.clicked.connect(self._move_down)
        self.btn_show.clicked.connect(lambda: self._set_selected_checked(True))
        self.btn_hide.clicked.connect(lambda: self._set_selected_checked(False))
        self.btn_reset.clicked.connect(self._reset)
        self.col_list.currentItemChanged.connect(self._on_col_selected)
        self.width_spin.valueChanged.connect(self._on_width_changed)

        if self.col_list.count() > 0:
            self.col_list.setCurrentRow(0)

    def _on_col_selected(self, current, prev):
        if current:
            name = current.text()
            w = self.widths_dict.get(name, 100)
            self.width_spin.setValue(w)

    def _on_width_changed(self, val):
        cur = self.col_list.currentItem()
        if cur:
            self.widths_dict[cur.text()] = val

    def _set_selected_checked(self, checked):
        cur = self.col_list.currentItem()
        if cur:
            cur.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _move_up(self):
        r = self.col_list.currentRow()
        if r > 0:
            it = self.col_list.takeItem(r)
            self.col_list.insertItem(r - 1, it)
            self.col_list.setCurrentRow(r - 1)

    def _move_down(self):
        r = self.col_list.currentRow()
        if r >= 0 and r < self.col_list.count() - 1:
            it = self.col_list.takeItem(r)
            self.col_list.insertItem(r + 1, it)
            self.col_list.setCurrentRow(r + 1)

    def _reset(self):
        default_vis = ["File Name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last Try Date", "Description"]
        for i in range(self.col_list.count()):
            it = self.col_list.item(i)
            it.setCheckState(Qt.CheckState.Checked if it.text() in default_vis else Qt.CheckState.Unchecked)

    def get_results(self):
        visible = []
        for i in range(self.col_list.count()):
            it = self.col_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                visible.append(it.text())
        return visible, self.widths_dict

class FilePropertiesDialog(QDialog):
    def __init__(self, download_item, parent=None):
        super().__init__(parent)
        self.download_item = download_item
        self.setWindowTitle("File Properties")
        self.setFixedWidth(520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon).pixmap(32, 32))
        top_row.addWidget(icon_lbl)

        filename = download_item.get('filename', 'Unknown')
        name_lbl = QLabel(filename)
        name_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
        top_row.addWidget(name_lbl, stretch=1)
        layout.addLayout(top_row)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        ext = Path(filename).suffix.lstrip('.').upper()
        type_str = f"{ext} File" if ext else "Binary Data"
        form.addRow("Type:", QLabel(type_str))

        status_str = download_item.get('status', 'Unknown')
        form.addRow("Status:", QLabel(status_str))

        size_bytes = download_item.get('total_size', 0)
        form.addRow("Size:", QLabel(f"{format_bytes(size_bytes)} ({size_bytes} Bytes)" if size_bytes > 0 else "Unknown"))

        save_row = QHBoxLayout()
        final_path = download_item.get('final_path', '')
        self.save_edit = QLineEdit(final_path)
        self.save_edit.setReadOnly(True)
        if not Path(final_path).exists() and status_str == 'Completed':
            self.save_edit.setText("The file has been moved.")
            self.save_edit.setStyleSheet("color: #0044cc; font-weight: bold;")
        save_row.addWidget(self.save_edit, stretch=1)
        
        move_btn = QPushButton("Move")
        move_btn.setFixedWidth(70)
        move_btn.clicked.connect(self._move_file)
        save_row.addWidget(move_btn)
        form.addRow("Save To:", save_row)

        self.address_edit = QLineEdit(download_item.get('url', ''))
        self.address_edit.setReadOnly(True)
        form.addRow("Address:", self.address_edit)

        self.desc_edit = QLineEdit(download_item.get('description', ''))
        form.addRow("Description:", self.desc_edit)

        layout.addLayout(form)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        page_form = QFormLayout()
        layout.addWidget(QLabel("The web page from which this file was obtained:"))
        referer_url = download_item.get('headers', {}).get('Referer', download_item.get('url', ''))
        self.referer_link = QLabel(f'<a href="{referer_url}">{referer_url}</a>')
        self.referer_link.setOpenExternalLinks(True)
        self.referer_link.setWordWrap(True)
        layout.addWidget(self.referer_link)

        self.referer_edit = QLineEdit(referer_url)
        self.referer_edit.setReadOnly(True)
        page_form.addRow("Referer:", self.referer_edit)

        self.login_edit = QLineEdit()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        page_form.addRow("Login", self.login_edit)
        page_form.addRow("Password", self.pass_edit)
        layout.addLayout(page_form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(self._open_file)
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._save_and_accept)
        btn_row.addWidget(open_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _move_file(self):
        cur_path = Path(self.download_item.get('final_path', ''))
        dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder", str(cur_path.parent))
        if dest_dir:
            dest_file = Path(dest_dir) / cur_path.name
            try:
                if cur_path.exists():
                    shutil.move(str(cur_path), str(dest_file))
                self.download_item['final_path'] = str(dest_file)
                self.save_edit.setText(str(dest_file))
                self.save_edit.setStyleSheet("")
            except Exception as e:
                QMessageBox.warning(self, "Move Failed", f"Could not move file:\n{e}")

    def _open_file(self):
        p = Path(self.download_item.get('final_path', ''))
        if p.exists():
            if platform.system() == "Windows": os.startfile(p)
            elif platform.system() == "Darwin": subprocess.run(["open", str(p)])
            else: subprocess.run(["xdg-open", str(p)])
            self.accept()
        else:
            QMessageBox.warning(self, "File Not Found", f"The file '{p}' does not exist on disk.")

    def _save_and_accept(self):
        self.download_item['description'] = self.desc_edit.text().strip()
        self.accept()

class SchedulerDialog(QDialog):
    queue_started = pyqtSignal(str)
    queue_stopped = pyqtSignal(str)

    def __init__(self, settings_manager, downloads_list, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.downloads = downloads_list
        self.setWindowTitle("Scheduler")
        self.resize(740, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.queues_cfg = self.settings_manager.settings.setdefault("queues", {
            "Main download queue": {"concurrent": 1, "start_at_enabled": False, "start_time": "23:00:00", "stop_at_enabled": False, "stop_time": "07:30:00", "retries": 10, "hang_up": False, "exit_idm": False, "turn_off": False, "shutdown_action": "Shut down", "force": False},
            "Synchronization queue": {"concurrent": 4, "start_at_enabled": False, "start_time": "23:00:00", "stop_at_enabled": False, "stop_time": "07:30:00", "retries": 10, "hang_up": False, "exit_idm": False, "turn_off": False, "shutdown_action": "Shut down", "force": False}
        })

        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, stretch=1)

        # Left panel: Queues selection list
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_header = QHBoxLayout()
        q_icon = QLabel()
        q_icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveCDIcon).pixmap(24, 24))
        left_header.addWidget(q_icon)
        left_header.addWidget(QLabel("Queues"))
        left_header.addStretch()
        left_layout.addLayout(left_header)

        self.queue_tree = QTreeWidget()
        self.queue_tree.setHeaderHidden(True)
        self.queue_tree.currentItemChanged.connect(self._on_tree_selected)
        left_layout.addWidget(self.queue_tree)

        q_btns = QHBoxLayout()
        self.new_q_btn = QPushButton("New queue")
        self.del_q_btn = QPushButton("Delete")
        self.new_q_btn.clicked.connect(self._add_new_queue)
        self.del_q_btn.clicked.connect(self._delete_queue)
        q_btns.addWidget(self.new_q_btn)
        q_btns.addWidget(self.del_q_btn)
        left_layout.addLayout(q_btns)
        splitter.addWidget(left_widget)

        # Right panel: Tab widget for queues or limits
        self.right_container = QWidget()
        self.right_layout = QVBoxLayout(self.right_container)
        self.right_layout.setContentsMargins(4, 0, 0, 0)

        self.header_title = QLabel("Main download queue")
        self.header_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_title.setStyleSheet("font-weight: 500; font-size: 12px; color: #111111;")
        self.right_layout.addWidget(self.header_title)

        self.queue_tabs = QTabWidget()
        self.right_layout.addWidget(self.queue_tabs)

        self._setup_schedule_tab()
        self._setup_files_tab()
        self._setup_limits_page()

        splitter.addWidget(self.right_container)
        splitter.setSizes([200, 520])

        # Bottom control buttons
        bottom_box = QHBoxLayout()
        self.start_now_btn = QPushButton("Start now")
        self.stop_btn = QPushButton("Stop")
        self.apply_btn = QPushButton("Apply")
        self.close_btn = QPushButton("Close")

        self.start_now_btn.clicked.connect(self._start_current_queue)
        self.stop_btn.clicked.connect(self._stop_current_queue)
        self.apply_btn.clicked.connect(self._apply_settings)
        self.close_btn.clicked.connect(self.accept)

        bottom_box.addWidget(self.start_now_btn)
        bottom_box.addWidget(self.stop_btn)
        bottom_box.addStretch()
        bottom_box.addWidget(self.apply_btn)
        bottom_box.addWidget(self.close_btn)
        main_layout.addLayout(bottom_box)

        self._populate_tree()

    def _setup_schedule_tab(self):
        self.schedule_tab = QWidget()
        layout = QVBoxLayout(self.schedule_tab)

        type_row = QHBoxLayout()
        self.rb_onetime = QRadioButton("One-time downloading")
        self.rb_periodic = QRadioButton("Periodic synchronization")
        self.rb_onetime.setChecked(True)
        type_row.addWidget(self.rb_onetime)
        type_row.addWidget(self.rb_periodic)
        type_row.addStretch()
        layout.addLayout(type_row)

        self.startup_dl_cb = QCheckBox("Start download on IDM startup")
        layout.addWidget(self.startup_dl_cb)

        start_row = QHBoxLayout()
        self.start_at_cb = QCheckBox("Start download at")
        self.start_time_edit = QTimeEdit()
        self.start_time_edit.setTime(QTime(23, 0))
        start_row.addWidget(self.start_at_cb)
        start_row.addWidget(self.start_time_edit)
        start_row.addStretch()
        layout.addLayout(start_row)

        periodic_box = QHBoxLayout()
        periodic_box.addSpacing(20)
        self.periodic_cb = QCheckBox("Start again every")
        self.periodic_hrs = QSpinBox(); self.periodic_hrs.setRange(0, 72); self.periodic_hrs.setValue(2)
        self.periodic_mins = QSpinBox(); self.periodic_mins.setRange(0, 59); self.periodic_mins.setValue(0)
        periodic_box.addWidget(self.periodic_cb)
        periodic_box.addWidget(self.periodic_hrs)
        periodic_box.addWidget(QLabel("hours"))
        periodic_box.addWidget(self.periodic_mins)
        periodic_box.addWidget(QLabel("min"))
        periodic_box.addStretch()
        layout.addLayout(periodic_box)

        days_box = QHBoxLayout()
        days_box.addSpacing(20)
        self.day_cbs = []
        for d in ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]:
            cb = QCheckBox(d)
            cb.setChecked(True)
            self.day_cbs.append(cb)
        
        c1, c2, c3 = QVBoxLayout(), QVBoxLayout(), QVBoxLayout()
        c1.addWidget(self.day_cbs[0]); c1.addWidget(self.day_cbs[1]); c1.addWidget(self.day_cbs[2])
        c2.addWidget(self.day_cbs[3]); c2.addWidget(self.day_cbs[4]); c2.addWidget(self.day_cbs[5])
        c3.addWidget(self.day_cbs[6]); c3.addStretch()
        days_box.addLayout(c1); days_box.addLayout(c2); days_box.addLayout(c3); days_box.addStretch()
        layout.addLayout(days_box)

        stop_row = QHBoxLayout()
        self.stop_at_cb = QCheckBox("Stop download at")
        self.stop_time_edit = QTimeEdit()
        self.stop_time_edit.setTime(QTime(7, 30))
        stop_row.addWidget(self.stop_at_cb)
        stop_row.addWidget(self.stop_time_edit)
        stop_row.addStretch()
        layout.addLayout(stop_row)

        retries_row = QHBoxLayout()
        self.retries_cb = QCheckBox("Number of retries for each file if downloading failed :")
        self.retries_spin = QSpinBox()
        self.retries_spin.setValue(10)
        retries_row.addWidget(self.retries_cb)
        retries_row.addWidget(self.retries_spin)
        retries_row.addStretch()
        layout.addLayout(retries_row)

        open_file_row = QHBoxLayout()
        self.open_file_cb = QCheckBox("Open the following file when done:")
        self.open_file_edit = QLineEdit()
        self.browse_file_btn = QPushButton("...")
        self.browse_file_btn.setFixedWidth(30)
        self.browse_file_btn.clicked.connect(self._browse_done_file)
        open_file_row.addWidget(self.open_file_cb)
        open_file_row.addWidget(self.open_file_edit)
        open_file_row.addWidget(self.browse_file_btn)
        layout.addLayout(open_file_row)

        self.exit_app_cb = QCheckBox("Exit Internet Download Manager when done")
        layout.addWidget(self.exit_app_cb)

        turn_off_row = QHBoxLayout()
        self.turn_off_cb = QCheckBox("Turn off computer when done")
        self.turn_off_combo = QComboBox()
        self.turn_off_combo.addItems(["Shut down", "Restart", "Sleep", "Hibernate"])
        self.force_term_cb = QCheckBox("Force processes to terminate")
        turn_off_row.addWidget(self.turn_off_cb)
        turn_off_row.addWidget(self.turn_off_combo)
        turn_off_row.addStretch()
        layout.addLayout(turn_off_row)
        layout.addWidget(self.force_term_cb)
        layout.addStretch()

        self.queue_tabs.addTab(self.schedule_tab, "Schedule")

    def _browse_done_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select file to open upon completion")
        if f: self.open_file_edit.setText(f)

    def _setup_files_tab(self):
        self.files_tab = QWidget()
        layout = QVBoxLayout(self.files_tab)

        sim_row = QHBoxLayout()
        sim_row.addWidget(QLabel("Download"))
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 100)
        self.concurrent_spin.setValue(1)
        sim_row.addWidget(self.concurrent_spin)
        sim_row.addWidget(QLabel("files at the same time"))
        sim_row.addStretch()
        layout.addLayout(sim_row)

        self.queue_files_table = QTableWidget()
        self.queue_files_table.setColumnCount(4)
        self.queue_files_table.setHorizontalHeaderLabels(["File Name", "Size", "Status", "Time left"])
        self.queue_files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_files_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.queue_files_table)

        btn_row = QHBoxLayout()
        self.btn_move_down = QPushButton("🠇")
        self.btn_move_up = QPushButton("🠅")
        self.btn_remove_file = QPushButton("✕")
        self.btn_move_down.setFixedWidth(35)
        self.btn_move_up.setFixedWidth(35)
        self.btn_remove_file.setFixedWidth(35)
        self.btn_remove_file.setStyleSheet("color: red; font-weight: bold;")
        self.btn_move_up.clicked.connect(self._move_file_up)
        self.btn_move_down.clicked.connect(self._move_file_down)
        self.btn_remove_file.clicked.connect(self._remove_file_from_queue)

        btn_row.addWidget(self.btn_move_down)
        btn_row.addWidget(self.btn_move_up)
        btn_row.addWidget(self.btn_remove_file)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.queue_tabs.addTab(self.files_tab, "Files in the queue")

    def _setup_limits_page(self):
        self.limits_widget = QWidget()
        layout = QVBoxLayout(self.limits_widget)
        layout.setContentsMargins(15, 20, 15, 20)

        self.lim_enable_cb = QCheckBox("Download limits")
        self.lim_enable_cb.setChecked(self.settings_manager.settings.get("download_limits_enabled", False))
        layout.addWidget(self.lim_enable_cb)

        lim_row = QHBoxLayout()
        lim_row.addWidget(QLabel("Download no more than"))
        self.lim_mb_spin = QSpinBox(); self.lim_mb_spin.setRange(1, 1000000)
        self.lim_mb_spin.setValue(self.settings_manager.settings.get("download_limit_mbytes", 200))
        lim_row.addWidget(self.lim_mb_spin)
        lim_row.addWidget(QLabel("MBytes"))
        lim_row.addStretch()
        layout.addLayout(lim_row)

        every_row = QHBoxLayout()
        every_row.addSpacing(30)
        every_row.addWidget(QLabel("every"))
        self.lim_hours_spin = QSpinBox(); self.lim_hours_spin.setRange(1, 1000)
        self.lim_hours_spin.setValue(self.settings_manager.settings.get("download_limit_hours", 5))
        every_row.addWidget(self.lim_hours_spin)
        every_row.addWidget(QLabel("hours"))
        every_row.addStretch()
        layout.addLayout(every_row)

        self.lim_warn_cb = QCheckBox("Show warning before stopping downloads")
        self.lim_warn_cb.setChecked(self.settings_manager.settings.get("show_limit_warning", True))
        layout.addWidget(self.lim_warn_cb)
        layout.addStretch()

    def _populate_tree(self):
        self.queue_tree.clear()
        for q_name in self.queues_cfg.keys():
            item = QTreeWidgetItem([q_name])
            item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
            self.queue_tree.addTopLevelItem(item)

        limits_item = QTreeWidgetItem(["Download limits"])
        limits_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
        self.queue_tree.addTopLevelItem(limits_item)

        if self.queue_tree.topLevelItemCount() > 0:
            self.queue_tree.setCurrentItem(self.queue_tree.topLevelItem(0))

    def _on_tree_selected(self, current, previous):
        if not current: return
        name = current.text(0)
        if name == "Download limits":
            self.header_title.setText("Download limits")
            self.queue_tabs.hide()
            if self.limits_widget.parent() is None:
                self.right_layout.addWidget(self.limits_widget)
            self.limits_widget.show()
        else:
            self.header_title.setText(name)
            self.limits_widget.hide()
            self.queue_tabs.show()
            self._load_queue_data(name)

    def _load_queue_data(self, queue_name):
        cfg = self.queues_cfg.get(queue_name, {})
        self.concurrent_spin.setValue(cfg.get("concurrent", 1))
        self.start_at_cb.setChecked(cfg.get("start_at_enabled", False))
        if cfg.get("start_time"):
            self.start_time_edit.setTime(QTime.fromString(cfg["start_time"], "HH:mm:ss"))
        self.stop_at_cb.setChecked(cfg.get("stop_at_enabled", False))
        if cfg.get("stop_time"):
            self.stop_time_edit.setTime(QTime.fromString(cfg["stop_time"], "HH:mm:ss"))
        self.exit_app_cb.setChecked(cfg.get("exit_idm", False))
        self.turn_off_cb.setChecked(cfg.get("turn_off", False))

        self.queue_files_table.setRowCount(0)
        q_files = [d for d in self.downloads if d.get('queue') == queue_name]
        for f in q_files:
            r = self.queue_files_table.rowCount()
            self.queue_files_table.insertRow(r)
            self.queue_files_table.setItem(r, 0, QTableWidgetItem(f.get('filename', '')))
            self.queue_files_table.setItem(r, 1, QTableWidgetItem(format_bytes(f.get('total_size', 0))))
            self.queue_files_table.setItem(r, 2, QTableWidgetItem(f.get('status', '')))
            self.queue_files_table.setItem(r, 3, QTableWidgetItem("---"))
            self.queue_files_table.item(r, 0).setData(Qt.ItemDataRole.UserRole, f)

    def _move_file_up(self):
        r = self.queue_files_table.currentRow()
        if r > 0:
            item_data = self.queue_files_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            self.queue_files_table.removeRow(r)
            self.queue_files_table.insertRow(r - 1)
            self.queue_files_table.setItem(r - 1, 0, QTableWidgetItem(item_data.get('filename', '')))
            self.queue_files_table.setItem(r - 1, 1, QTableWidgetItem(format_bytes(item_data.get('total_size', 0))))
            self.queue_files_table.setItem(r - 1, 2, QTableWidgetItem(item_data.get('status', '')))
            self.queue_files_table.setItem(r - 1, 3, QTableWidgetItem("---"))
            self.queue_files_table.item(r - 1, 0).setData(Qt.ItemDataRole.UserRole, item_data)
            self.queue_files_table.setCurrentCell(r - 1, 0)

    def _move_file_down(self):
        r = self.queue_files_table.currentRow()
        if r >= 0 and r < self.queue_files_table.rowCount() - 1:
            item_data = self.queue_files_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            self.queue_files_table.removeRow(r)
            self.queue_files_table.insertRow(r + 1)
            self.queue_files_table.setItem(r + 1, 0, QTableWidgetItem(item_data.get('filename', '')))
            self.queue_files_table.setItem(r + 1, 1, QTableWidgetItem(format_bytes(item_data.get('total_size', 0))))
            self.queue_files_table.setItem(r + 1, 2, QTableWidgetItem(item_data.get('status', '')))
            self.queue_files_table.setItem(r + 1, 3, QTableWidgetItem("---"))
            self.queue_files_table.item(r + 1, 0).setData(Qt.ItemDataRole.UserRole, item_data)
            self.queue_files_table.setCurrentCell(r + 1, 0)

    def _remove_file_from_queue(self):
        r = self.queue_files_table.currentRow()
        if r >= 0:
            data = self.queue_files_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            if data:
                data['queue'] = ""
            self.queue_files_table.removeRow(r)

    def _add_new_queue(self):
        text, ok = QInputDialog.getText(self, "Create new queue", "Queue name:")
        if ok and text.strip():
            q_name = text.strip()
            if q_name not in self.queues_cfg:
                self.queues_cfg[q_name] = {"concurrent": 1, "start_at_enabled": False, "start_time": "23:00:00", "stop_at_enabled": False, "stop_time": "07:30:00"}
                self._populate_tree()

    def _delete_queue(self):
        cur = self.queue_tree.currentItem()
        if cur:
            name = cur.text(0)
            if name in ("Main download queue", "Synchronization queue", "Download limits"):
                QMessageBox.warning(self, "Cannot delete", f"'{name}' cannot be removed.")
                return
            del self.queues_cfg[name]
            self._populate_tree()

    def _apply_settings(self):
        cur = self.queue_tree.currentItem()
        if cur and cur.text(0) != "Download limits":
            q_name = cur.text(0)
            cfg = self.queues_cfg.setdefault(q_name, {})
            cfg["concurrent"] = self.concurrent_spin.value()
            cfg["start_at_enabled"] = self.start_at_cb.isChecked()
            cfg["start_time"] = self.start_time_edit.time().toString("HH:mm:ss")
            cfg["stop_at_enabled"] = self.stop_at_cb.isChecked()
            cfg["stop_time"] = self.stop_time_edit.time().toString("HH:mm:ss")
            cfg["exit_idm"] = self.exit_app_cb.isChecked()
            cfg["turn_off"] = self.turn_off_cb.isChecked()
        
        s = self.settings_manager.settings
        s["download_limits_enabled"] = self.lim_enable_cb.isChecked()
        s["download_limit_mbytes"] = self.lim_mb_spin.value()
        s["download_limit_hours"] = self.lim_hours_spin.value()
        s["show_limit_warning"] = self.lim_warn_cb.isChecked()
        self.settings_manager.save_settings()

    def _start_current_queue(self):
        cur = self.queue_tree.currentItem()
        if cur and cur.text(0) != "Download limits":
            self._apply_settings()
            self.queue_started.emit(cur.text(0))

    def _stop_current_queue(self):
        cur = self.queue_tree.currentItem()
        if cur and cur.text(0) != "Download limits":
            self.queue_stopped.emit(cur.text(0))

class FindDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find")
        self.setFixedWidth(360)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.find_edit = QLineEdit()
        form.addRow("Find what:", self.find_edit)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Find Next")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_search_text(self):
        return self.find_edit.text().strip()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setGeometry(100, 100, 960, 620)
        self.setWindowIcon(QIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)))
        if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
        self.settings_manager = SettingsManager()
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(max(32, QThreadPool.globalInstance().maxThreadCount()))

        self.downloads = []
        self.history_on_load = []
        self.workers = {}
        self.tab_titles = {}
        self.active_progress_dialogs = {}
        
        self.video_streams = {}
        self.tab_rects = {}
        self.video_tab_id = "-1"
        self.current_active_tab = "-1"
        self.toolbar_offset = 155
        self.measured_video_rect = None

        self.active_queues = set()
        self.speed_limiter_active = self.settings_manager.settings.get("speed_limiter", {}).get("enabled", False)
        self.last_search_query = ""
        self.current_selected_category = "All Downloads"

        self.setup_tray_icon()
        self.overlay = VideoOverlay(self.settings_manager)
        self.overlay.download_requested.connect(self.on_overlay_download)
        self.ws_server = WebSocketServerThread(self.settings_manager)
        self.ws_server.download_request_received.connect(self.on_download_request)
        self.ws_server.video_stream_detected.connect(self.on_video_stream_detected)
        self.ws_server.title_detected.connect(self.on_title_detected)
        self.ws_server.tab_switched.connect(self.on_tab_switched)
        self.ws_server.video_rect_received.connect(self.on_video_rect_received)
        self.ws_server.start()
        
        self.setup_ui()
        self.setup_toolbar()
        self.setup_menu()
        self.load_download_history()
        self.table.itemSelectionChanged.connect(self.update_toolbar_state)
        self.update_toolbar_state()

        if self.settings_manager.settings.get("dark_mode", False):
            self._apply_dark_mode(True)

    def update_toolbar_state(self):
        selected_rows = sorted(list(set(item.row() for item in self.table.selectedItems())))
        has_selection = len(selected_rows) > 0

        has_downloading = False
        has_resumable = False
        for r in selected_rows:
            if r < len(self.downloads):
                st = self.downloads[r].get('status', '')
                if st == 'Downloading':
                    has_downloading = True
                elif st in ('Paused', 'Queued', 'Cancelled', 'Error'):
                    has_resumable = True

        any_active = any(d.get('status') in ('Downloading', 'Queued') for d in self.downloads)
        any_completed = any(d.get('status') == 'Completed' for d in self.downloads)

        # 1. Resume / Pause button toggling & enabling
        if has_downloading:
            self.act_resume.setText("Pause")
            self.act_resume.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.act_resume.setEnabled(True)
        elif has_resumable:
            self.act_resume.setText("Resume")
            self.act_resume.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
            self.act_resume.setEnabled(True)
        else:
            self.act_resume.setText("Resume")
            self.act_resume.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
            self.act_resume.setEnabled(False)

        # 2. Stop selected
        self.act_stop.setEnabled(has_downloading)

        # 3. Stop All
        self.act_stop_all.setEnabled(any_active)

        # 4. Delete selected
        self.act_delete.setEnabled(has_selection)

        # 5. Delete Completed
        self.act_delete_completed.setEnabled(any_completed)

        # 6. Stop Queue button
        if hasattr(self, 'stop_queue_btn'):
            self.stop_queue_btn.setEnabled(len(self.active_queues) > 0 or any_active)

    def action_resume_or_pause_selected(self):
        rows = sorted(list(set(item.row() for item in self.table.selectedItems())))
        for r in rows:
            if r < len(self.downloads):
                st = self.downloads[r].get('status', '')
                if self.act_resume.text() == "Pause" and st == 'Downloading':
                    self.toggle_pause_row(r)
                elif self.act_resume.text() == "Resume" and st in ('Paused', 'Queued', 'Cancelled', 'Error'):
                    if st in ('Cancelled', 'Error'):
                        self.restart_row_download(r)
                    else:
                        self.toggle_pause_row(r)
        self.update_toolbar_state()

    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.tray_icon.activated.connect(self.on_tray_activated)
        if self.settings_manager.settings.get("tray_icon_style", "3D style") != "Don't show":
            self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.Context):
            menu = self.create_tray_menu()
            menu.exec(QCursor.pos())
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._bring_to_front()

    def create_tray_menu(self):
        menu = QMenu()
        title_item = menu.addAction(f"{APP_NAME} v{VERSION}")
        title_item.setEnabled(False)
        menu.addSeparator()

        restore_app = menu.addAction("Show PyIDM")
        restore_app.triggered.connect(self._bring_to_front)
        menu.addSeparator()

        has_minimized = False
        for row, dlg in list(self.active_progress_dialogs.items()):
            if dlg and getattr(dlg, 'is_minimized_to_tray', False) and row < len(self.downloads):
                item = self.downloads[row]
                status = item.get('status', '')
                if status in ('Downloading', 'Paused', 'Queued'):
                    has_minimized = True
                    pct = dlg.progress_bar.value()
                    fname = item.get('filename', 'Download')
                    act = menu.addAction(f"{pct}% {fname} [{status}]")
                    act.triggered.connect(lambda _, d=dlg: self.restore_progress_dialog(d))

        if not has_minimized:
            no_act = menu.addAction("No minimized downloads")
            no_act.setEnabled(False)

        menu.addSeparator()
        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self.close)
        return menu

    def restore_progress_dialog(self, dlg):
        dlg.is_minimized_to_tray = False
        dlg.showNormal()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def setup_ui(self):
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self.splitter)

        self.sidebar_widget = QWidget()
        sidebar_layout = QVBoxLayout(self.sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        side_header = QWidget()
        side_header_layout = QHBoxLayout(side_header)
        side_header_layout.setContentsMargins(6, 4, 4, 4)
        cat_title = QLabel("Categories")
        cat_title.setStyleSheet("font-weight: bold; color: #333333;")
        close_cat_btn = QPushButton("✕")
        close_cat_btn.setFixedSize(16, 16)
        close_cat_btn.setStyleSheet("border: none; color: #666666;")
        close_cat_btn.clicked.connect(self.toggle_categories_sidebar)
        side_header_layout.addWidget(cat_title)
        side_header_layout.addStretch()
        side_header_layout.addWidget(close_cat_btn)
        sidebar_layout.addWidget(side_header)

        self.category_tree = QTreeWidget()
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setStyleSheet("QTreeWidget { border: 1px solid #dcdcdc; }")
        self._populate_category_tree()
        self.category_tree.currentItemChanged.connect(self._on_category_changed)
        sidebar_layout.addWidget(self.category_tree)

        self.splitter.addWidget(self.sidebar_widget)

        self.table = QTableWidget()
        self._setup_table_columns()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        self.table.itemDoubleClicked.connect(self._handle_table_double_click)
        self.splitter.addWidget(self.table)

        self.splitter.setSizes([180, 780])

        if not self.settings_manager.settings.get("categories_visible", True):
            self.sidebar_widget.hide()

    def _setup_table_columns(self):
        col_cfg = self.settings_manager.settings.get("url_list_columns", {})
        visible_cols = col_cfg.get("visible", ["File Name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last Try Date", "Description"])
        widths = col_cfg.get("widths", {})

        self.current_visible_columns = visible_cols
        self.table.setColumnCount(len(visible_cols))
        self.table.setHorizontalHeaderLabels(visible_cols)

        for idx, col in enumerate(visible_cols):
            w = widths.get(col, 100)
            self.table.setColumnWidth(idx, w)

        if "File Name" in visible_cols:
            fn_idx = visible_cols.index("File Name")
            self.table.horizontalHeader().setSectionResizeMode(fn_idx, QHeaderView.ResizeMode.Stretch)

    def _populate_category_tree(self):
        self.category_tree.clear()
        
        all_item = QTreeWidgetItem(["All Downloads"])
        all_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon))
        for cat in FILE_CATEGORIES.keys():
            sub = QTreeWidgetItem([cat])
            sub.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
            all_item.addChild(sub)
        self.category_tree.addTopLevelItem(all_item)

        unfin_item = QTreeWidgetItem(["Unfinished"])
        unfin_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon))
        for cat in FILE_CATEGORIES.keys():
            sub = QTreeWidgetItem([cat])
            sub.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
            unfin_item.addChild(sub)
        self.category_tree.addTopLevelItem(unfin_item)

        fin_item = QTreeWidgetItem(["Finished"])
        fin_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        for cat in FILE_CATEGORIES.keys():
            sub = QTreeWidgetItem([cat])
            sub.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
            fin_item.addChild(sub)
        self.category_tree.addTopLevelItem(fin_item)

        grab_item = QTreeWidgetItem(["Grabber projects"])
        grab_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.category_tree.addTopLevelItem(grab_item)

        queues_root = QTreeWidgetItem(["Queues"])
        queues_root.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DriveCDIcon))
        for q_name in self.settings_manager.settings.get("queues", {}).keys():
            q_sub = QTreeWidgetItem([q_name])
            q_sub.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
            queues_root.addChild(q_sub)
        self.category_tree.addTopLevelItem(queues_root)

        all_item.setExpanded(True)
        unfin_item.setExpanded(True)
        fin_item.setExpanded(True)
        queues_root.setExpanded(True)

    def _on_category_changed(self, current, previous):
        if not current: return
        self.current_selected_category = current.text(0)
        self._filter_table_view()

    def _filter_table_view(self):
        cat = self.current_selected_category
        parent = self.category_tree.currentItem().parent() if self.category_tree.currentItem() else None
        parent_text = parent.text(0) if parent else ""

        for r in range(self.table.rowCount()):
            if r >= len(self.downloads): continue
            item = self.downloads[r]
            show = True

            if cat == "All Downloads":
                show = True
            elif cat == "Unfinished":
                show = item.get('status') in ("Downloading", "Paused", "Queued", "Error", "Cancelled")
            elif cat == "Finished":
                show = item.get('status') == "Completed"
            elif parent_text == "All Downloads":
                file_cat = self.settings_manager.get_category_for_filename(item.get('filename', ''))
                show = (file_cat == cat)
            elif parent_text == "Unfinished":
                file_cat = self.settings_manager.get_category_for_filename(item.get('filename', ''))
                show = (file_cat == cat) and (item.get('status') in ("Downloading", "Paused", "Queued", "Error", "Cancelled"))
            elif parent_text == "Finished":
                file_cat = self.settings_manager.get_category_for_filename(item.get('filename', ''))
                show = (file_cat == cat) and (item.get('status') == "Completed")
            elif parent_text == "Queues" or cat in self.settings_manager.settings.get("queues", {}):
                show = (item.get('queue') == cat)

            self.table.setRowHidden(r, not show)

    def setup_toolbar(self):
        self.toolbar = QToolBar("Main Toolbar")
        self.toolbar.setMovable(False)
        self.toolbar.setStyleSheet("""
            QToolBar {
                background: #fbfbfb;
                border-bottom: 1px solid #dcdcdc;
                spacing: 4px;
                padding: 3px 4px;
            }
            QToolButton {
                font-family: "Segoe UI", sans-serif;
                font-size: 11px;
                min-width: 62px;
                padding: 2px 2px 3px 2px;
                border: 1px solid transparent;
                border-radius: 2px;
            }
            QToolButton:hover {
                background: #e5f1fb;
                border-color: #a0c5e8;
            }
            QToolButton:disabled {
                color: #a0a0a0;
            }
            /* Split button arrow compartment */
            QToolButton[popupMode="1"] {
                min-width: 80px;
                padding-right: 18px;
            }
            QToolButton::menu-button {
                width: 16px;
                border: none;
                border-left: 1px solid #dcdcdc;
            }
            QToolButton::menu-button:hover {
                background: #d5e6f5;
            }
        """)

        size_mode = self.settings_manager.settings.get("toolbar_size", "large")
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon if size_mode == "large" else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        icon_dim = 28 if size_mode == "large" else 18
        self.toolbar.setIconSize(QSize(icon_dim, icon_dim))

        self.act_add_url = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder), "Add URL", self)
        self.act_add_url.triggered.connect(self.action_add_url)

        self.act_resume = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight), "Resume", self)
        self.act_resume.triggered.connect(self.action_resume_or_pause_selected)

        self.act_stop = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop), "Stop", self)
        self.act_stop.triggered.connect(self.action_stop_selected)

        self.act_stop_all = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton), "Stop All", self)
        self.act_stop_all.triggered.connect(self.action_stop_all)

        self.act_delete = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "Delete", self)
        self.act_delete.triggered.connect(self.action_delete_selected)

        self.act_delete_completed = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogDiscardButton), "Delete Co...", self)
        self.act_delete_completed.triggered.connect(self.action_delete_all_completed)

        self.act_options = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView), "Options", self)
        self.act_options.triggered.connect(self.show_settings_dialog)

        self.act_scheduler = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView), "Scheduler", self)
        self.act_scheduler.triggered.connect(self.action_open_scheduler)

        self.toolbar.addAction(self.act_add_url)
        self.toolbar.addAction(self.act_resume)
        self.toolbar.addAction(self.act_stop)
        self.toolbar.addAction(self.act_stop_all)
        self.toolbar.addAction(self.act_delete)
        self.toolbar.addAction(self.act_delete_completed)
        self.toolbar.addAction(self.act_options)
        self.toolbar.addAction(self.act_scheduler)

        # Start Queue Split Button (Button starts Main Queue, Arrow chooses queue)
        self.start_queue_btn = QToolButton()
        self.start_queue_btn.setText("Start Qu...")
        self.start_queue_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.start_queue_btn.setToolButtonStyle(self.toolbar.toolButtonStyle())
        self.start_queue_btn.setIconSize(self.toolbar.iconSize())
        self.start_queue_btn.setMinimumWidth(80)
        self.start_queue_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        start_q_menu = QMenu(self.start_queue_btn)
        act_sq_main = start_q_menu.addAction("Start Main download queue")
        act_sq_sync = start_q_menu.addAction("Start Synchronization queue")
        act_sq_main.triggered.connect(lambda: self.start_queue("Main download queue"))
        act_sq_sync.triggered.connect(lambda: self.start_queue("Synchronization queue"))
        self.start_queue_btn.setMenu(start_q_menu)
        self.start_queue_btn.clicked.connect(lambda: self.start_queue("Main download queue"))
        self.toolbar.addWidget(self.start_queue_btn)

        # Stop Queue Split Button (Button stops Main Queue, Arrow chooses queue)
        self.stop_queue_btn = QToolButton()
        self.stop_queue_btn.setText("Stop Qu...")
        self.stop_queue_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
        self.stop_queue_btn.setToolButtonStyle(self.toolbar.toolButtonStyle())
        self.stop_queue_btn.setIconSize(self.toolbar.iconSize())
        self.stop_queue_btn.setMinimumWidth(80)
        self.stop_queue_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        stop_q_menu = QMenu(self.stop_queue_btn)
        act_tq_main = stop_q_menu.addAction("Stop Main download queue")
        act_tq_sync = stop_q_menu.addAction("Stop Synchronization queue")
        act_tq_main.triggered.connect(lambda: self.stop_queue("Main download queue"))
        act_tq_sync.triggered.connect(lambda: self.stop_queue("Synchronization queue"))
        self.stop_queue_btn.setMenu(stop_q_menu)
        self.stop_queue_btn.clicked.connect(lambda: self.stop_queue("Main download queue"))
        self.toolbar.addWidget(self.stop_queue_btn)

        self.addToolBar(self.toolbar)

    def setup_menu(self):
        mb = self.menuBar()

        tasks_menu = mb.addMenu("&Tasks")
        tasks_menu.addAction("Add new download", self.action_add_url)
        tasks_menu.addAction("Add batch download", self.action_add_batch)
        tasks_menu.addAction("Add batch download from clipboard", self.action_batch_from_clipboard)
        tasks_menu.addSeparator()

        export_menu = tasks_menu.addMenu("Export")
        export_menu.addAction("To IDM export file", self.action_export_idm_file)
        export_menu.addAction("To text file", self.action_export_text_file)

        import_menu = tasks_menu.addMenu("Import")
        import_menu.addAction("From IDM export file", self.action_import_idm_file)
        import_menu.addAction("From text file", self.action_import_text_file)
        tasks_menu.addSeparator()
        tasks_menu.addAction("Exit", self.close)

        file_menu = mb.addMenu("&File")
        file_menu.addAction("Stop Download", self.action_stop_selected)
        file_menu.addAction("Remove", self.action_delete_selected)
        file_menu.addAction("Download Now", self.action_resume_selected)
        file_menu.addAction("Redownload", self.action_redownload_selected)

        dl_menu = mb.addMenu("&Downloads")
        dl_menu.addAction("Pause All", self.action_stop_all)
        dl_menu.addAction("Stop All", self.action_stop_all)
        dl_menu.addAction("Delete All Completed", self.action_delete_all_completed)
        dl_menu.addSeparator()
        dl_menu.addAction("Find (Ctrl-F)", "Ctrl+F", self.action_find)
        dl_menu.addAction("Find Next (F3)", "F3", self.action_find_next)
        dl_menu.addSeparator()
        dl_menu.addAction("Scheduler", self.action_open_scheduler)

        sq_submenu = dl_menu.addMenu("Start queue")
        sq_submenu.addAction("Start Main download queue", lambda: self.start_queue("Main download queue"))
        sq_submenu.addAction("Start Synchronization queue", lambda: self.start_queue("Synchronization queue"))

        tq_submenu = dl_menu.addMenu("Stop queue")
        tq_submenu.addAction("Stop Main download queue", lambda: self.stop_queue("Main download queue"))
        tq_submenu.addAction("Stop Synchronization queue", lambda: self.stop_queue("Synchronization queue"))

        lim_submenu = dl_menu.addMenu("Speed Limiter")
        self.act_lim_on = lim_submenu.addAction("Turn on", lambda: self.set_speed_limiter_active(True))
        self.act_lim_off = lim_submenu.addAction("Turn off", lambda: self.set_speed_limiter_active(False))
        lim_submenu.addAction("Settings...", self.open_speed_limiter_settings)
        dl_menu.addSeparator()
        dl_menu.addAction("Options", self.show_settings_dialog)

        view_menu = mb.addMenu("&View")
        self.act_toggle_cats = view_menu.addAction("Hide categories" if self.sidebar_widget.isVisible() else "Show categories", self.toggle_categories_sidebar)

        arrange_submenu = view_menu.addMenu("Arrange files")
        arrange_submenu.addAction("By Order Of Addition", lambda: self.arrange_files("date_added"))
        arrange_submenu.addAction("By File Name", lambda: self.arrange_files("filename"))
        arrange_submenu.addAction("By Size", lambda: self.arrange_files("total_size"))
        arrange_submenu.addAction("By Status", lambda: self.arrange_files("status"))
        arrange_submenu.addAction("By Time Left", lambda: self.arrange_files("time_left"))
        arrange_submenu.addAction("By Transfer Rate", lambda: self.arrange_files("speed"))
        arrange_submenu.addAction("By Last Try Date", lambda: self.arrange_files("date_added"))
        arrange_submenu.addAction("By Description", lambda: self.arrange_files("description"))
        arrange_submenu.addAction("By Save Path", lambda: self.arrange_files("final_path"))
        arrange_submenu.addAction("By Referer", lambda: self.arrange_files("referer"))

        toolbar_submenu = view_menu.addMenu("Toolbar")
        toolbar_submenu.addAction("Customize...", self.open_customize_toolbar)
        toolbar_submenu.addAction("Large Buttons", lambda: self.set_toolbar_size("large"))
        toolbar_submenu.addAction("Small Buttons", lambda: self.set_toolbar_size("small"))

        tray_submenu = view_menu.addMenu("IDM tray icon")
        tray_submenu.addAction("3D style", lambda: self.set_tray_icon_style("3D style"))
        tray_submenu.addAction("Classic style", lambda: self.set_tray_icon_style("Classic style"))
        tray_submenu.addAction("Don't show", lambda: self.set_tray_icon_style("Don't show"))

        view_menu.addAction("Customize URL List...", self.open_customize_columns)
        self.act_dark_mode = view_menu.addAction("Dark Mode support", self.toggle_dark_mode)
        self.act_dark_mode.setCheckable(True)
        self.act_dark_mode.setChecked(self.settings_manager.settings.get("dark_mode", False))

        font_submenu = view_menu.addMenu("Font")
        font_submenu.addAction("Choose Font...", self.choose_font)
        font_submenu.addAction("Reset to default Font", self.reset_font)

    def toggle_categories_sidebar(self):
        visible = not self.sidebar_widget.isVisible()
        self.sidebar_widget.setVisible(visible)
        self.settings_manager.settings["categories_visible"] = visible
        self.settings_manager.save_settings()
        if hasattr(self, 'act_toggle_cats'):
            self.act_toggle_cats.setText("Hide categories" if visible else "Show categories")

    def toggle_dark_mode(self):
        en = self.act_dark_mode.isChecked()
        self.settings_manager.settings["dark_mode"] = en
        self.settings_manager.save_settings()
        self._apply_dark_mode(en)

    def _apply_dark_mode(self, enabled):
        if enabled:
            self.setStyleSheet("""
                QMainWindow, QDialog, QWidget { background-color: #2b2b2b; color: #ffffff; }
                QTableWidget, QTreeWidget, QTextEdit, QLineEdit { background-color: #383838; color: #ffffff; border: 1px solid #555555; }
                QHeaderView::section { background-color: #333333; color: #ffffff; border: 1px solid #444444; }
                QToolBar { background: #333333; border-bottom: 1px solid #444444; }
                QMenuBar, QMenu { background-color: #2b2b2b; color: #ffffff; }
                QMenu::item:selected { background-color: #0d529c; }
            """)
        else:
            self.setStyleSheet("")

    def set_toolbar_size(self, size_mode):
        self.settings_manager.settings["toolbar_size"] = size_mode
        self.settings_manager.save_settings()
        style = Qt.ToolButtonStyle.ToolButtonTextUnderIcon if size_mode == "large" else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        icon_dim = 28 if size_mode == "large" else 18
        
        self.toolbar.setToolButtonStyle(style)
        self.toolbar.setIconSize(QSize(icon_dim, icon_dim))

        if hasattr(self, 'start_queue_btn'):
            self.start_queue_btn.setToolButtonStyle(style)
            self.start_queue_btn.setIconSize(QSize(icon_dim, icon_dim))
        if hasattr(self, 'stop_queue_btn'):
            self.stop_queue_btn.setToolButtonStyle(style)
            self.stop_queue_btn.setIconSize(QSize(icon_dim, icon_dim))

    def set_tray_icon_style(self, style_name):
        self.settings_manager.settings["tray_icon_style"] = style_name
        self.settings_manager.save_settings()
        if style_name == "Don't show":
            self.tray_icon.hide()
        else:
            self.tray_icon.show()

    def choose_font(self):
        font, ok = QFontDialog.getFont(self.font(), self, "Choose Font")
        if ok:
            QApplication.setFont(font)

    def reset_font(self):
        QApplication.setFont(QFont("Segoe UI", 9))

    def arrange_files(self, key):
        def sort_val(item):
            v = item.get(key, 0)
            return v if v is not None else ""
        self.downloads.sort(key=sort_val)
        self.refresh_table()

    def refresh_table(self):
        self.table.setRowCount(0)
        items_copy = list(self.downloads)
        self.downloads = []
        for d in items_copy:
            self.add_download_to_table(d)
        self._filter_table_view()

    def open_customize_toolbar(self):
        avail = ["Add URL", "Resume", "Stop", "Stop All", "Delete", "Delete Co...", "Options", "Scheduler", "Start Qu...", "Stop Qu..."]
        curr = ["Add URL", "Resume", "Stop", "Stop All", "Delete", "Delete Co...", "Options", "Scheduler", "Start Qu...", "Stop Qu..."]
        dlg = CustomizeToolbarDialog(avail, curr, self)
        dlg.exec()

    def open_customize_columns(self):
        col_cfg = self.settings_manager.settings.get("url_list_columns", {})
        vis = col_cfg.get("visible", ["File Name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last Try Date", "Description"])
        widths = col_cfg.get("widths", {})
        dlg = CustomizeColumnsDialog(vis, widths, self)
        if dlg.exec():
            new_vis, new_widths = dlg.get_results()
            self.settings_manager.settings["url_list_columns"] = {"visible": new_vis, "widths": new_widths}
            self.settings_manager.save_settings()
            self._setup_table_columns()
            self.refresh_table()

    def set_speed_limiter_active(self, active):
        self.speed_limiter_active = active
        cfg = self.settings_manager.settings.setdefault("speed_limiter", {})
        cfg["enabled"] = active
        self.settings_manager.save_settings()
        limit_kbps = cfg.get("speed_kbps", 10)
        for w in self.workers.values():
            w.set_speed_limit(active, limit_kbps)

    def open_speed_limiter_settings(self):
        cfg = self.settings_manager.settings.setdefault("speed_limiter", {})
        dlg = SpeedLimiterSettingsDialog(cfg.get("speed_kbps", 10), cfg.get("always_turn_on_startup", False), self)
        if dlg.exec():
            kbps, startup = dlg.get_settings()
            cfg["speed_kbps"] = kbps
            cfg["always_turn_on_startup"] = startup
            self.settings_manager.save_settings()
            if self.speed_limiter_active:
                for w in self.workers.values():
                    w.set_speed_limit(True, kbps)

    def action_add_url(self):
        dlg = AddUrlDialog("", self)
        if dlg.exec():
            url = dlg.get_url()
            if url:
                self.on_download_request({'params': {6: url}})

    def action_add_batch(self):
        dlg = BatchDownloadDialog(parent=self)
        if dlg.exec():
            for u in dlg.get_urls():
                self.on_download_request({'params': {6: u}})

    def action_batch_from_clipboard(self):
        clip_text = QApplication.clipboard().text()
        found_urls = re.findall(r'https?://[^\s]+', clip_text)
        dlg = BatchDownloadDialog(found_urls, self)
        if dlg.exec():
            for u in dlg.get_urls():
                self.on_download_request({'params': {6: u}})

    def action_export_idm_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export to IDM file", "", "IDM Export (*.ef2);;All Files (*)")
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.downloads, f, indent=2)

    def action_export_text_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export to Text file", "", "Text Files (*.txt);;All Files (*)")
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                for d in self.downloads:
                    f.write(d.get('url', '') + '\n')

    def action_import_idm_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import from IDM file", "", "IDM Export (*.ef2);;JSON Files (*.json);;All Files (*)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data:
                    self.add_download_to_table(item)
            except Exception as e:
                QMessageBox.warning(self, "Import Error", f"Failed to import file:\n{e}")

    def action_import_text_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import from Text file", "", "Text Files (*.txt);;All Files (*)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        u = line.strip()
                        if u: self.on_download_request({'params': {6: u}})
            except Exception as e:
                QMessageBox.warning(self, "Import Error", f"Failed to import URLs:\n{e}")

    def action_resume_selected(self):
        rows = sorted(list(set(item.row() for item in self.table.selectedItems())))
        for r in rows:
            if r < len(self.downloads):
                if self.downloads[r].get('status') in ('Paused', 'Queued'):
                    self.toggle_pause_row(r)
                elif self.downloads[r].get('status') in ('Cancelled', 'Error'):
                    self.restart_row_download(r)

    def action_stop_selected(self):
        rows = sorted(list(set(item.row() for item in self.table.selectedItems())))
        for r in rows:
            if r < len(self.downloads):
                if self.downloads[r].get('status') == 'Downloading':
                    self.toggle_pause_row(r)

    def action_stop_all(self):
        for r in range(len(self.downloads)):
            if self.downloads[r].get('status') == 'Downloading':
                self.toggle_pause_row(r)

    def action_delete_selected(self):
        self.remove_selected_download()

    def action_delete_all_completed(self):
        selected_rows = [i for i, d in enumerate(self.downloads) if d.get('status') == 'Completed']
        for row in sorted(selected_rows, reverse=True):
            self.table.removeRow(row)
            del self.downloads[row]
        self.save_download_history()

    def action_redownload_selected(self):
        rows = sorted(list(set(item.row() for item in self.table.selectedItems())))
        for r in rows:
            if r < len(self.downloads):
                self.restart_row_download(r)

    def action_open_scheduler(self):
        dlg = SchedulerDialog(self.settings_manager, self.downloads, self)
        dlg.queue_started.connect(self.start_queue)
        dlg.queue_stopped.connect(self.stop_queue)
        dlg.exec()

    def start_queue(self, queue_name):
        self.active_queues.add(queue_name)
        q_cfg = self.settings_manager.settings.get("queues", {}).get(queue_name, {})
        max_sim = q_cfg.get("concurrent", 1)

        running = sum(1 for d in self.downloads if d.get('queue') == queue_name and d.get('status') == 'Downloading')
        for idx, item in enumerate(self.downloads):
            if running >= max_sim: break
            if item.get('queue') == queue_name and item.get('status') in ('Queued', 'Paused'):
                self.toggle_pause_row(idx)
                running += 1

    def stop_queue(self, queue_name):
        if queue_name in self.active_queues:
            self.active_queues.remove(queue_name)
        for idx, item in enumerate(self.downloads):
            if item.get('queue') == queue_name and item.get('status') == 'Downloading':
                self.toggle_pause_row(idx)

    def action_find(self):
        dlg = FindDialog(self)
        if dlg.exec():
            self.last_search_query = dlg.get_search_text().lower()
            self.action_find_next()

    def action_find_next(self):
        if not self.last_search_query: return
        start_row = self.table.currentRow() + 1
        total_rows = self.table.rowCount()
        for i in range(total_rows):
            r = (start_row + i) % total_rows
            item = self.table.item(r, 0)
            if item and self.last_search_query in item.text().lower():
                self.table.selectRow(r)
                self.table.scrollToItem(item)
                return
        QMessageBox.information(self, "Find", f"No matching entries found for '{self.last_search_query}'.")

    def _handle_table_double_click(self, item):
        row = item.row()
        if row >= len(self.downloads): return
        act = self.settings_manager.settings.get("double_click_action", "properties")
        if act == "open":
            self.open_downloaded_file(item)
        else:
            self.open_file_properties(row)

    def open_file_properties(self, row):
        if row < len(self.downloads):
            dlg = FilePropertiesDialog(self.downloads[row], self)
            if dlg.exec():
                self.save_download_history()

    def show_table_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid(): return
        row = index.row()
        if row >= len(self.downloads): return

        download_item = self.downloads[row]
        status = download_item.get('status', '')
        file_path = Path(download_item.get('final_path', ''))

        menu = QMenu()
        menu.addAction("Open", lambda: self.open_downloaded_file(self.table.item(row, 0)))
        menu.addAction("Open with...", lambda: self._open_file_with(row))
        menu.addAction("Open folder", lambda: self._open_file_folder(row))
        menu.addSeparator()

        menu.addAction("Move/Rename (Ctrl-M)", lambda: self._move_or_rename_row(row))
        menu.addAction("Redownload", lambda: self.restart_row_download(row))

        if status in ("Paused", "Queued"):
            menu.addAction("Resume Download", lambda: self.toggle_pause_row(row))
        elif status == "Downloading":
            menu.addAction("Stop Download", lambda: self.toggle_pause_row(row))

        menu.addAction("Refresh download address", lambda: self._refresh_download_address(row))
        menu.addAction("Remove", self.remove_selected_download)
        menu.addSeparator()

        add_q_menu = menu.addMenu("Add to queue")
        for q_name in self.settings_manager.settings.get("queues", {}).keys():
            add_q_menu.addAction(q_name, lambda q=q_name: self._set_row_queue(row, q))
        add_q_menu.addAction("Create new queue", self._create_queue_and_add)

        menu.addAction("Delete from queue", lambda: self._set_row_queue(row, ""))

        dbl_menu = menu.addMenu("On Double click")
        act_open = dbl_menu.addAction("Open")
        act_prop = dbl_menu.addAction("Properties")
        cur_dbl = self.settings_manager.settings.get("double_click_action", "properties")
        act_open.setCheckable(True); act_open.setChecked(cur_dbl == "open")
        act_prop.setCheckable(True); act_prop.setChecked(cur_dbl == "properties")
        act_open.triggered.connect(lambda: self._set_double_click_action("open"))
        act_prop.triggered.connect(lambda: self._set_double_click_action("properties"))

        menu.addAction("Properties", lambda: self.open_file_properties(row))
        menu.exec(self.table.mapToGlobal(pos))

    def _open_file_with(self, row):
        p = Path(self.downloads[row].get('final_path', ''))
        if p.exists() and platform.system() == "Windows":
            subprocess.run(f'rundll32.exe shell32.dll,OpenAs_RunDLL {p}', shell=True)

    def _open_file_folder(self, row):
        p = Path(self.downloads[row].get('final_path', ''))
        if p.parent.exists():
            if platform.system() == "Windows": subprocess.run(f'explorer /select,"{p}"', shell=True)
            elif platform.system() == "Darwin": subprocess.run(["open", "-R", str(p)])
            else: subprocess.run(["xdg-open", str(p.parent)])

    def _move_or_rename_row(self, row):
        cur = Path(self.downloads[row].get('final_path', ''))
        new_name, ok = QInputDialog.getText(self, "Rename File", "Enter new name:", text=cur.name)
        if ok and new_name.strip():
            new_target = cur.parent / new_name.strip()
            if cur.exists():
                try: shutil.move(str(cur), str(new_target))
                except Exception as e: QMessageBox.warning(self, "Error", f"Failed to rename file:\n{e}"); return
            self.downloads[row]['final_path'] = str(new_target)
            self.downloads[row]['filename'] = new_target.name
            if self.table.item(row, 0): self.table.item(row, 0).setText(new_target.name)

    def _refresh_download_address(self, row):
        cur_url = self.downloads[row].get('url', '')
        text, ok = QInputDialog.getText(self, "Refresh Address", "Enter new URL for this download:", text=cur_url)
        if ok and text.strip():
            self.downloads[row]['url'] = clean_idm_url(text.strip())

    def _set_row_queue(self, row, queue_name):
        self.downloads[row]['queue'] = queue_name
        self.save_download_history()
        self._filter_table_view()

    def _create_queue_and_add(self):
        text, ok = QInputDialog.getText(self, "Create queue", "Queue name:")
        if ok and text.strip():
            q_name = text.strip()
            cfg = self.settings_manager.settings.setdefault("queues", {})
            cfg[q_name] = {"concurrent": 1, "start_at_enabled": False, "start_time": "23:00:00", "stop_at_enabled": False, "stop_time": "07:30:00"}
            self.settings_manager.save_settings()
            self._populate_category_tree()

    def _set_double_click_action(self, action):
        self.settings_manager.settings["double_click_action"] = action
        self.settings_manager.save_settings()

    def restart_row_download(self, row):
        item = self.downloads[row]
        details = {
            'url': item['url'],
            'final_save_path': item['final_path'],
            'headers': item.get('headers', {})
        }

        if row in self.workers:
            self.workers[row].cancel()
            del self.workers[row]

        item['status'] = 'Queued'
        item['progress'] = 0
        item['speed'] = 0
        self.table.item(row, 2).setText('Queued')
        pbar = self.table.cellWidget(row, 3)
        if pbar: pbar.setValue(0)
        self.table.item(row, 4).setText('0.00 MB/s')

        max_conn = int(self.settings_manager.settings.get("max_connections", 32))
        temp_dir = self.settings_manager.settings.get("temp_dir", str(Path.home() / "Downloads" / "Temp"))
        worker = DownloadWorker(row, details['url'], details['final_save_path'], details['headers'], max_connections=max_conn, temp_dir=temp_dir)
        worker.signals.file_info.connect(self.update_download_info)
        worker.signals.progress.connect(self.update_download_progress)
        worker.signals.finished.connect(self.on_download_finished)
        worker.signals.error.connect(self.on_download_error)
        self.workers[row] = worker

        dlg = self.active_progress_dialogs.get(row)
        if dlg:
            dlg.worker = worker
            dlg.setup_connections()
            dlg.status_val.setText("Receiving data...")
            dlg.status_val.setStyleSheet("color: #0000cc; font-weight: bold;")
            dlg.pause_resume_btn.setText("Pause")
            dlg.pause_resume_btn.setEnabled(True)
            self.restore_progress_dialog(dlg)
        else:
            dlg = DownloadProgressDialog(row, item, worker, self.settings_manager, self.tray_icon, parent=None)
            self.active_progress_dialogs[row] = dlg
            dlg.show()

        self.thread_pool.start(worker)

    def restore_row_dialog(self, row):
        dlg = self.active_progress_dialogs.get(row)
        if dlg:
            self.restore_progress_dialog(dlg)
        else:
            worker = self.workers.get(row)
            if worker:
                dlg = DownloadProgressDialog(row, self.downloads[row], worker, self.settings_manager, self.tray_icon, parent=None)
                dlg.status_changed.connect(self.on_dialog_status_changed)
                self.active_progress_dialogs[row] = dlg
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()

    def toggle_pause_row(self, row):
        worker = self.workers.get(row)
        item = self.downloads[row]
        dlg = self.active_progress_dialogs.get(row)

        if item.get('status') == 'Downloading':
            if worker: worker.pause()
            item['status'] = 'Paused'
            item['speed'] = 0
            self.table.item(row, 2).setText('Paused')
            self.table.item(row, 4).setText('0.00 MB/s')
            if dlg:
                dlg.pause_resume_btn.setText("Start")
                dlg.status_val.setText("Paused")
                dlg.status_val.setStyleSheet("color: #cc6600; font-weight: bold;")
        elif item.get('status') in ('Paused', 'Queued'):
            if worker: worker.resume()
            item['status'] = 'Downloading'
            self.table.item(row, 2).setText('Downloading')
            if dlg:
                dlg.pause_resume_btn.setText("Pause")
                dlg.status_val.setText("Receiving data...")
                dlg.status_val.setStyleSheet("color: #0000cc; font-weight: bold;")
        self.update_toolbar_state()

    def cancel_row_download(self, row):
        worker = self.workers.get(row)
        dlg = self.active_progress_dialogs.get(row)
        item = self.downloads[row]

        if worker:
            worker.cancel()
            del self.workers[row]

        item['status'] = 'Cancelled'
        item['progress'] = 0
        item['speed'] = 0

        self.table.item(row, 2).setText('Cancelled')
        progress_bar = self.table.cellWidget(row, 3)
        if progress_bar: progress_bar.setValue(0)
        self.table.item(row, 4).setText('---')

        if dlg:
            dlg.status_val.setText("Cancelled")
            dlg.progress_bar.setValue(0)
            dlg.downloaded_val.setText("0 B ( 0 % )")
            dlg.transfer_rate_val.setText("0.000 MB/sec")
            dlg.pause_resume_btn.setText("Start")
            dlg.pause_resume_btn.setEnabled(False)
        self.update_toolbar_state()

    def on_dialog_status_changed(self, row, new_status):
        if row < len(self.downloads) and row < self.table.rowCount():
            self.downloads[row]['status'] = new_status
            self.table.item(row, 2).setText(new_status)
            if new_status == 'Paused':
                self.downloads[row]['speed'] = 0
                self.table.item(row, 4).setText("0.00 MB/s")
            elif new_status == 'Cancelled':
                self.downloads[row]['progress'] = 0
                self.downloads[row]['speed'] = 0
                pbar = self.table.cellWidget(row, 3)
                if pbar: pbar.setValue(0)
                self.table.item(row, 4).setText("---")
                if row in self.workers:
                    del self.workers[row]

    def delete_file_for_row(self, row):
        if row < len(self.downloads):
            p = Path(self.downloads[row].get('final_path', ''))
            reply = QMessageBox.question(
                self,
                "Confirm File Deletion",
                f"Are you sure you want to delete '{p.name}' from disk and remove it from the list?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                if p.exists():
                    try:
                        os.remove(p)
                    except OSError as e:
                        QMessageBox.warning(self, "Error", f"Could not delete file from disk:\n{e}")
                        return

                if row in self.workers:
                    self.workers[row].cancel()
                    del self.workers[row]

                if row in self.active_progress_dialogs:
                    dlg = self.active_progress_dialogs.pop(row)
                    dlg.close()

                self.table.removeRow(row)
                del self.downloads[row]

                new_workers = {}
                for old_row, worker in self.workers.items():
                    new_row = old_row - 1 if old_row > row else old_row
                    worker.row = new_row
                    new_workers[new_row] = worker
                self.workers = new_workers

                new_dialogs = {}
                for old_row, dlg in self.active_progress_dialogs.items():
                    new_row = old_row - 1 if old_row > row else old_row
                    dlg.row = new_row
                    new_dialogs[new_row] = dlg
                self.active_progress_dialogs = new_dialogs

                self.save_download_history()

    def remove_selected_download(self):
        selected_rows = sorted(list(set(item.row() for item in self.table.selectedItems())), reverse=True)
        for row in selected_rows:
            if row in self.workers: self.workers[row].cancel(); del self.workers[row]
            self.table.removeRow(row)
            if row < len(self.downloads): del self.downloads[row]
        new_workers = {}
        for old_row, worker in self.workers.items():
            shift = sum(1 for r in selected_rows if r < old_row)
            new_row = old_row - shift
            worker.row = new_row
            new_workers[new_row] = worker
        self.workers = new_workers
        self.save_download_history()
        self.update_toolbar_state()

    def open_downloaded_file(self, item):
        row = item.row()
        download_item = self.downloads[row]
        if download_item['status'] == 'Completed':
            file_path = Path(download_item['final_path'])
            if file_path.exists():
                try:
                    system = platform.system()
                    if system == "Windows": os.startfile(file_path)
                    elif system == "Darwin": subprocess.run(["open", file_path])
                    else: subprocess.run(["xdg-open", file_path])
                except Exception as e: QMessageBox.warning(self, "Error", f"Could not open file:\n{e}")
            else: QMessageBox.warning(self, "File Not Found", f"The file '{file_path}' could not be found.")

    def load_download_history(self):
        if not DOWNLOADS_FILE.exists(): return
        try:
            with open(DOWNLOADS_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
                self.history_on_load = history
            for item_data in history:
                if item_data.get("status") not in ["Downloading", "Paused"]:
                    self.add_download_to_table(item_data)
        except Exception as e: log_to_file(f"Failed to load download history: {e}")

    def save_download_history(self):
        try:
            history_to_save = [d for d in self.downloads if d['status'] in ["Completed", "Error", "Cancelled"]]
            if history_to_save == self.history_on_load: return
            with open(DOWNLOADS_FILE, 'w', encoding='utf-8') as f: json.dump(history_to_save, f, indent=4)
        except Exception as e: log_to_file(f"Failed to save download history: {e}")

    def _bring_to_front(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def remove_download_by_row(self, row):
        if row in self.workers:
            self.workers[row].cancel()
            del self.workers[row]
        self.table.removeRow(row)
        if row < len(self.downloads):
            del self.downloads[row]
        new_workers = {}
        for old_row, worker in self.workers.items():
            new_row = old_row - 1 if old_row > row else old_row
            worker.row = new_row
            new_workers[new_row] = worker
        self.workers = new_workers

    def on_download_request(self, data):
        url_to_check = clean_idm_url(data.get('params', {}).get(6, ''))
        if not url_to_check or 'facebook.com/api/graphql' in url_to_check: return
        headers = parse_http_headers(data['params'].get(11, ''))
        if not headers.get('User-Agent') and data['params'].get(54): headers['User-Agent'] = data['params'][54]
        if not headers.get('Referer') and data['params'].get(7): headers['Referer'] = data['params'][7]
        if 'Cookie' not in headers and data['params'].get(100): headers['Cookie'] = data['params'][100]
        data['headers'] = headers

        dup_action = self.settings_manager.settings.get("duplicate_action", DUPLICATE_ACTIONS[0])
        existing = next((d for d in self.downloads if d.get('url') == url_to_check), None)

        filename = Path(unquote(url_to_check.split('?')[0])).name or "download"
        final_dir = self.settings_manager.get_path_for_filename(filename)
        candidate_path = Path(final_dir) / filename

        if existing:
            if dup_action == DUPLICATE_ACTIONS[3]:
                if existing.get('status') == 'Completed' and Path(existing.get('final_path', '')).exists():
                    comp_dlg = DownloadCompleteDialog(existing, None)
                    comp_dlg.exec()
                    return
            elif dup_action == DUPLICATE_ACTIONS[0]:
                dlg = DuplicateDialog(filename, None)
                if dlg.exec():
                    if dlg.selected_choice == "cancel": return
                    elif dlg.selected_choice == "numbered": candidate_path = get_unique_filename(candidate_path)
                else: return
            elif dup_action == DUPLICATE_ACTIONS[1]:
                candidate_path = get_unique_filename(candidate_path)

        show_start = self.settings_manager.settings.get("show_start_dialog", True)
        start_imm = self.settings_manager.settings.get("start_immediately", True)
        initial_details = {'url': url_to_check, 'final_save_path': str(candidate_path), 'headers': headers}

        if not show_start:
            self.start_new_download(initial_details, show_progress=True)
            return

        bg_row, bg_worker, progress_dlg = (None, None, None)
        if start_imm:
            bg_row, bg_worker, progress_dlg = self.start_new_download(initial_details, show_progress=False, is_background=True)

        dialog = DownloadDialog(data, self.settings_manager, None)
        dialog.filename_edit.setText(candidate_path.name)
        dialog.path_edit.setText(str(candidate_path.parent))
        dialog.activateWindow()
        dialog.raise_()

        if dialog.exec():
            res = dialog.result_details
            if res:
                if bg_worker:
                    new_final = Path(res['final_save_path'])
                    item = bg_worker.download_item
                    item['final_path'] = str(new_final)
                    item['filename'] = new_final.name
                    real_row = self.add_download_to_table(item)
                    bg_worker.row = real_row
                    self.workers[real_row] = bg_worker

                    if progress_dlg:
                        progress_dlg.row = real_row
                        self.active_progress_dialogs[real_row] = progress_dlg
                        progress_dlg.save_to_label.setText(f"Save To: {new_final}")
                        progress_dlg.update_title_text(f"{progress_dlg.progress_bar.value()}% {new_final.name}")
                        progress_dlg.show()
                        progress_dlg.raise_()
                        progress_dlg.activateWindow()

                    bg_worker.commit_download(str(new_final))
                else:
                    self.start_new_download(res, show_progress=True)
        else:
            if bg_worker:
                bg_worker.cancel()

    def start_new_download(self, details, show_progress=True, is_background=False):
        final_path = Path(details['final_save_path'])
        download_item = {
            "url": details['url'],
            "final_path": str(final_path),
            "headers": details['headers'],
            "filename": final_path.name,
            "total_size": 0,
            "status": "Queued",
            "progress": 0,
            "speed": 0,
            "date_added": datetime.now().isoformat(),
            "description": "",
            "queue": "Main download queue"
        }
        row = -1 if is_background else self.add_download_to_table(download_item)
        
        max_conn = int(self.settings_manager.settings.get("max_connections", 32))
        temp_dir = self.settings_manager.settings.get("temp_dir", str(Path.home() / "Downloads" / "Temp"))
        write_mode = self.settings_manager.settings.get("single_part_write_mode", "Save to temporary folder first, then move upon completion")

        worker = DownloadWorker(row, details['url'], details['final_save_path'], details['headers'],
                                max_connections=max_conn, temp_dir=temp_dir, is_background=is_background, write_mode=write_mode)
        worker.download_item = download_item
        worker.signals.file_info.connect(self.update_download_info)
        worker.signals.progress.connect(self.update_download_progress)
        worker.signals.finished.connect(self.on_download_finished)
        worker.signals.error.connect(self.on_download_error)
        
        if self.speed_limiter_active:
            cfg = self.settings_manager.settings.get("speed_limiter", {})
            worker.set_speed_limit(True, cfg.get("speed_kbps", 10))

        if not is_background:
            self.workers[row] = worker

        progress_dialog = DownloadProgressDialog(row, download_item, worker, self.settings_manager, self.tray_icon, parent=None)
        progress_dialog.status_changed.connect(self.on_dialog_status_changed)
        if not is_background:
            self.active_progress_dialogs[row] = progress_dialog
        if show_progress:
            progress_dialog.show()

        self.thread_pool.start(worker)
        return row, worker, progress_dialog

    def add_download_to_table(self, item_data):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.downloads.append(item_data)

        vis = getattr(self, 'current_visible_columns', ["File Name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last Try Date", "Description"])

        for col_idx, col_name in enumerate(vis):
            if col_name == "File Name":
                self.table.setItem(row, col_idx, QTableWidgetItem(item_data.get('filename', 'Waiting...')))
            elif col_name == "Q":
                q_text = "Q" if item_data.get('queue') else ""
                self.table.setItem(row, col_idx, QTableWidgetItem(q_text))
            elif col_name == "Size":
                self.table.setItem(row, col_idx, QTableWidgetItem(format_bytes(item_data.get('total_size', 0))))
            elif col_name == "Status":
                self.table.setItem(row, col_idx, QTableWidgetItem(item_data.get('status', 'Queued')))
            elif col_name == "Time left":
                self.table.setItem(row, col_idx, QTableWidgetItem("---"))
            elif col_name == "Transfer rate":
                self.table.setItem(row, col_idx, QTableWidgetItem("0.00 MB/s"))
            elif col_name == "Last Try Date" or col_name == "Date Added":
                date_str = item_data.get('date_added', datetime.now().isoformat())
                dt = datetime.fromisoformat(date_str).strftime('%b %d %H:%M:%S %Y')
                self.table.setItem(row, col_idx, QTableWidgetItem(dt))
            elif col_name == "Description":
                self.table.setItem(row, col_idx, QTableWidgetItem(item_data.get('description', '')))
            elif col_name == "Save To":
                self.table.setItem(row, col_idx, QTableWidgetItem(item_data.get('final_path', '')))
            elif col_name == "Referer":
                ref = item_data.get('headers', {}).get('Referer', '')
                self.table.setItem(row, col_idx, QTableWidgetItem(ref))
            elif col_name == "Parent web page":
                ref = item_data.get('headers', {}).get('Referer', item_data.get('url', ''))
                self.table.setItem(row, col_idx, QTableWidgetItem(ref))

        return row

    def update_download_info(self, row, filename, total_size):
        if 0 <= row < self.table.rowCount() and row < len(self.downloads):
            vis = getattr(self, 'current_visible_columns', [])
            if "File Name" in vis:
                self.table.item(row, vis.index("File Name")).setText(filename)
            if "Size" in vis:
                self.table.item(row, vis.index("Size")).setText(format_bytes(total_size))

            self.downloads[row]['filename'] = filename
            self.downloads[row]['total_size'] = total_size

    def update_download_progress(self, row, downloaded_bytes, speed_mbps):
        if 0 <= row < self.table.rowCount() and row < len(self.downloads):
            self.downloads[row]['status'] = 'Downloading'
            self.downloads[row]['speed'] = speed_mbps
            vis = getattr(self, 'current_visible_columns', [])

            if "Status" in vis:
                self.table.item(row, vis.index("Status")).setText("Downloading")
            if "Transfer rate" in vis:
                self.table.item(row, vis.index("Transfer rate")).setText(f"{speed_mbps:.2f} MB/s")

            total_size = self.downloads[row]['total_size']
            if total_size > 0:
                progress = int((downloaded_bytes / total_size) * 100)
                self.downloads[row]['progress'] = progress

    def on_download_finished(self, row, status, final_filepath):
        if 0 <= row < self.table.rowCount() and row < len(self.downloads):
            vis = getattr(self, 'current_visible_columns', [])
            if "Status" in vis:
                self.table.item(row, vis.index("Status")).setText(status)
            if "Transfer rate" in vis:
                self.table.item(row, vis.index("Transfer rate")).setText("---")

            self.downloads[row]['status'] = status
            self.downloads[row]['final_path'] = final_filepath
            if row in self.workers:
                del self.workers[row]

            q_name = self.downloads[row].get('queue')
            if q_name and q_name in self.active_queues:
                self.start_queue(q_name)
        self.update_toolbar_state()

    def on_download_error(self, row, message):
        if 0 <= row < self.table.rowCount() and row < len(self.downloads):
            vis = getattr(self, 'current_visible_columns', [])
            if "Status" in vis:
                self.table.item(row, vis.index("Status")).setText("Error")
            self.downloads[row]['status'] = "Error"
            log_to_file(f"Download Error for row {row} ('{self.downloads[row]['filename']}'): {message}")
            if row in self.workers:
                del self.workers[row]

    def show_settings_dialog(self):
        dialog = SettingsDialog(self.settings_manager, self.thread_pool, self.ws_server, self)
        if dialog.exec():
            self.overlay.reload_settings()
            if self.overlay.video_data:
                self._render_overlay()

    def closeEvent(self, event):
        if self.workers and self.settings_manager.settings.get("confirm_exit_on_active", True):
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Confirm Exit")
            msg_box.setText("Active downloads will be cancelled. Are you sure you want to exit?")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg_box.setDefaultButton(QMessageBox.StandardButton.No)
            checkbox = QCheckBox("Don't ask me again")
            msg_box.setCheckBox(checkbox)
            reply = msg_box.exec()
            if checkbox.isChecked():
                self.settings_manager.settings["confirm_exit_on_active"] = False
                self.settings_manager.save_settings()
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

        for worker in self.workers.values(): worker.cancel()
        self.thread_pool.waitForDone(1000)
        self.save_download_history()
        self.ws_server.stop()
        self.ws_server.wait()
        super().closeEvent(event)

    def on_tab_switched(self, tab_id, toolbar_offset):
        self.current_active_tab = str(tab_id)
        if toolbar_offset > 50:
            self.toolbar_offset = toolbar_offset

        if self.current_active_tab == self.video_tab_id and self.video_tab_id in self.video_streams:
            self.overlay.set_video_data(self.video_streams[self.video_tab_id])
            self._render_overlay()
        else:
            self.overlay.hide()
            self.overlay.should_be_visible = False

    def on_video_rect_received(self, left, top, right, bottom, zoom):
        logical_x, logical_y, win_w, win_h, dpr, hwnd = get_firefox_client_info()

        z = zoom if zoom > 0 else dpr
        l_x = int(left / z)
        t_y = int(top / z)
        r_x = int(right / z)
        b_y = int(bottom / z)

        video_screen_rect = QRect(
            logical_x + l_x, 
            logical_y + self.toolbar_offset + t_y, 
            r_x - l_x, 
            b_y - t_y
        )
        self.measured_video_rect = video_screen_rect

        self.overlay.update_position(video_screen_rect, logical_y + self.toolbar_offset, hwnd)

    def _render_overlay(self):
        logical_x, logical_y, win_w, win_h, dpr, hwnd = get_firefox_client_info()
        
        if self.measured_video_rect:
            self.overlay.update_position(self.measured_video_rect, logical_y + self.toolbar_offset, hwnd)

    def on_title_detected(self, tab_id, title):
        self.tab_titles[tab_id] = title
        if tab_id in self.video_streams:
            clean_title = re.sub(r'[\\/*?:"<>|]', '-', title).strip()
            clean_title = re.sub(r'\s*-\s*', ' - ', clean_title)
            for s in self.video_streams[tab_id]:
                s['title'] = clean_title
            self.overlay.set_video_data(self.video_streams[tab_id])

    def on_video_detected(self, data):
        args = data.get('args', [])
        params = data.get('params', {})
        tab_id = str(args[1]) if len(args) > 1 else self.current_active_tab
        rect_param = params.get(134)
        
        if not rect_param:
            return
            
        try:
            comps = [float(x) for x in (rect_param.split(',') if isinstance(rect_param, str) else rect_param)]
            if len(comps) >= 4:
                l, t, r, b = comps[0], comps[1], comps[2], comps[3]
                w, h = r - l, b - t
                if w < 50 or h < 50: return
                logical_x, logical_y, win_w, win_h, dpr, hwnd = get_firefox_client_info()
                video_screen_rect = QRect(int(logical_x + (l / dpr)), int(logical_y + (t / dpr)), int(w / dpr), int(h / dpr))
                self.tab_rects[tab_id] = video_screen_rect

                if tab_id == self.current_active_tab:
                    self.overlay.update_position(video_screen_rect, logical_y, hwnd)
                    if tab_id in self.video_streams:
                        self.overlay.set_video_data(self.video_streams[tab_id])
        except Exception as e:
            print(f"[DEBUG on_video_detected ERROR] {e}")

    def _parse_size_str_to_bytes(self, size_str):
        if not size_str or not isinstance(size_str, str): return 0
        s = size_str.strip().upper()
        match = re.match(r'([\d.]+)\s*(B|KB|MB|GB)?', s)
        if not match: return 0
        val = float(match.group(1))
        unit = match.group(2) or 'B'
        multipliers = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3}
        return int(val * multipliers.get(unit, 1))

    def _is_url_in_exceptions(self, url):
        cfg = self.settings_manager.settings.get("download_panels_config", {})
        exc_str = cfg.get("exceptions", "")
        domain_patterns = exc_str.split() + self.settings_manager.settings.get("dont_show_panel_sites", [])
        
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        for pat in domain_patterns:
            pat = pat.strip().lower()
            if not pat: continue
            regex = re.escape(pat).replace(r'\*', '.*')
            if re.fullmatch(regex, host) or pat in host:
                return True
        return False

    def _is_stream_allowed_by_settings(self, ext, file_size_bytes=0):
        if self.settings_manager.settings.get("dont_capture_from_players", False):
            return False

        ext_clean = ext.strip().upper().replace(".", "")
        if not ext_clean or ext_clean in ("COM", "NET", "ORG", "HTML", "HTM", "JS", "CSS"):
            return True

        cfg = self.settings_manager.settings.get("download_panels_config", {})
        table = cfg.get("file_types_table", [])

        for row in table:
            if row.get("type", "").upper() == ext_clean:
                if not row.get("checked", True):
                    return False
                min_size_str = row.get("min_size", "")
                if min_size_str and file_size_bytes > 0:
                    min_bytes = self._parse_size_str_to_bytes(min_size_str)
                    if file_size_bytes < min_bytes:
                        return False
                return True

        allowed_panel_types = [t.upper() for t in self.settings_manager.settings.get("panel_file_types", [])]
        if ext_clean in allowed_panel_types:
            return True

        return True

    def on_video_stream_detected(self, parsed_msg):
        args = parsed_msg.get('args', [])
        params = parsed_msg.get('params', {})
        tab_id = str(args[6]) if len(args) > 6 else "-1"
        element_id = params.get(134)
        url = clean_idm_url(params.get(6))

        if not url:
            return

        if self._is_url_in_exceptions(url):
            return

        file_ext_match = re.search(r'\.([^./?]+)', url.split('?')[0])
        ext_guess = file_ext_match.group(1).upper() if file_ext_match else "MP4"

        if not self._is_stream_allowed_by_settings(ext_guess):
            return

        h = {}
        if params.get(11):
            h.update(parse_http_headers(params.get(11)))

        if not h.get('Referer'):
            if params.get(50):
                h['Referer'] = params.get(50)
            elif params.get(7):
                h['Referer'] = params.get(7)

        if not h.get('User-Agent'):
            h['User-Agent'] = params.get(54) or BROWSER_USER_AGENT
        if not h.get('Cookie') and params.get(100):
            h['Cookie'] = params.get(100)

        raw_title = self.tab_titles.get(tab_id, "Video")
        clean_title = re.sub(r'[\\/*?:"<>|]', '-', raw_title).strip()
        clean_title = re.sub(r'\s*-\s*', ' - ', clean_title)

        clean_url = url.split('?')[0].lower()

        if "master.m3u8" in clean_url or ("master" in clean_url and clean_url.endswith(".m3u8")):
            variants = parse_hls_master_playlist(url, clean_title, h)
            if variants:
                self.video_streams[tab_id] = []
                for v in variants:
                    v['element_id'] = element_id
                    v['headers'] = dict(h)
                    self.video_streams[tab_id].append(v)

                self.overlay.set_video_data(self.video_streams[tab_id])
                self._ensure_overlay_visible()
                return

        if "index.m3u8" in clean_url:
            return

        streams = self.video_streams.setdefault(tab_id, [])
        if any(s['url'] == url for s in streams):
            return

        label = params.get(31, params.get(4, 'Media'))
        ext = params.get(4, '')
        
        if clean_url.endswith('.srt'):
            ext = "SRT"
        elif clean_url.endswith('.vtt'):
            ext = "VTT"

        stream_info = {
            'url': url,
            'title': clean_title,
            'label': label,
            'ext': ext,
            'resolution': '',
            'bandwidth_str': '',
            'element_id': element_id,
            'headers': dict(h)
        }
        
        streams.append(stream_info)
        self.overlay.set_video_data(streams)
        self._ensure_overlay_visible()

    def _ensure_overlay_visible(self):
        logical_x, logical_y, win_w, win_h, dpr, hwnd = get_firefox_client_info()

        active_rect = self.tab_rects.get(self.current_active_tab)
        
        if active_rect is not None:
            self.overlay.update_position(active_rect, logical_y, hwnd)
        elif self.measured_video_rect is not None:
            self.overlay.update_position(self.measured_video_rect, logical_y + self.toolbar_offset, hwnd)
        else:
            center_x = logical_x + (win_w // 2)
            player_top_right_x = center_x + 425
            player_top_y = logical_y + 225
            
            video_approx_rect = QRect(player_top_right_x - 850, player_top_y, 850, 480)
            self.overlay.update_position(video_approx_rect, logical_y, hwnd)

    def on_overlay_download(self, item_data):
        self.overlay.hide()
        self.overlay.should_be_visible = False
        
        mock_data = {
            'params': {
                6: item_data.get('url'),
                7: item_data.get('headers', {}).get('Referer'),
                54: item_data.get('headers', {}).get('User-Agent'),
                100: item_data.get('headers', {}).get('Cookie')
            }
        }
        title = item_data.get('title', 'Video')
        ext = item_data.get('ext', 'mp4').lower()
        default_filename = f"{title}.{ext}"

        cat = self.settings_manager.get_category_for_filename(default_filename)
        final_dir = self.settings_manager.settings.get("save_paths", {}).get(cat, str(Path.home() / "Downloads"))
        candidate_path = Path(final_dir) / default_filename

        show_start = self.settings_manager.settings.get("show_start_dialog", True)
        start_imm = self.settings_manager.settings.get("start_immediately", True)

        initial_details = {
            'url': clean_idm_url(item_data.get('url')),
            'final_save_path': str(candidate_path),
            'headers': item_data.get('headers', {})
        }

        if not show_start:
            self.start_new_download(initial_details, show_progress=True)
            return

        bg_row, bg_worker, progress_dlg = (None, None, None)
        if start_imm:
            bg_row, bg_worker, progress_dlg = self.start_new_download(initial_details, show_progress=False, is_background=True)

        dialog = DownloadDialog(mock_data, self.settings_manager, None)
        dialog.filename_edit.setText(candidate_path.name)
        dialog.path_edit.setText(str(candidate_path.parent))
        dialog.activateWindow()
        dialog.raise_()

        if dialog.exec():
            res = dialog.result_details
            if res:
                if bg_worker:
                    new_final = Path(res['final_save_path'])
                    item = bg_worker.download_item
                    item['final_path'] = str(new_final)
                    item['filename'] = new_final.name
                    real_row = self.add_download_to_table(item)
                    bg_worker.row = real_row
                    self.workers[real_row] = bg_worker

                    if progress_dlg:
                        progress_dlg.row = real_row
                        self.active_progress_dialogs[real_row] = progress_dlg
                        progress_dlg.save_to_label.setText(f"Save To: {new_final}")
                        progress_dlg.update_title_text(f"{progress_dlg.progress_bar.value()}% {new_final.name}")
                        progress_dlg.show()
                        progress_dlg.raise_()
                        progress_dlg.activateWindow()

                    bg_worker.commit_download(str(new_final))
                else:
                    self.start_new_download(res, show_progress=True)
        else:
            if bg_worker:
                bg_worker.cancel()

if __name__ == '__main__':
    if sys.platform == 'win32':
        stop_idm_service()
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())