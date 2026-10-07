import sys
import os
import ctypes
from ctypes import wintypes
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QMenu, 
    QFrame, QHBoxLayout, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect, QTimer
from PyQt6.QtGui import QCursor, QAction

class VideoOverlay(QWidget):
    download_requested = pyqtSignal(dict)

    def __init__(self, settings_manager=None):
        super().__init__()
        self.settings_manager = settings_manager
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.container = QFrame(self)
        self.container.setObjectName("overlayContainer")
        self.container.setStyleSheet("""
            QFrame#overlayContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #eef5fc, stop:0.45 #c5ddf5, stop:0.5 #afd0ee, stop:1 #cce1f7);
                border: 1px solid #527a9f;
                border-radius: 3px;
            }
            QFrame#overlayContainer:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f5faff, stop:0.45 #d2e7fa, stop:0.5 #bedcf7, stop:1 #daebfb);
                border-color: #3b668f;
            }
            QPushButton {
                background: transparent;
                border: none;
                color: #0c3660;
                font-family: "Segoe UI", sans-serif;
                font-size: 11px;
                font-weight: bold;
                padding: 2px 6px;
            }
            QPushButton:hover {
                color: #002244;
            }
            QPushButton::menu-indicator {
                image: none;
                width: 0px;
            }
            QPushButton#closeBtn {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dbe9f7, stop:1 #b5d3f2);
                border: 1px solid #7ea4cb;
                border-radius: 2px;
                color: #1a4066;
                font-size: 10px;
                font-weight: bold;
                padding: 0px;
                width: 16px;
                height: 16px;
            }
            QPushButton#closeBtn:hover {
                background: #c3dcf7;
                border-color: #5b87b3;
            }
        """)

        layout = QHBoxLayout(self.container)
        layout.setContentsMargins(2, 2, 3, 2)
        layout.setSpacing(2)

        self.download_btn = QPushButton("Download Video ▾")
        self.download_btn.setObjectName("downloadBtn")
        self.download_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.download_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.download_btn.clicked.connect(self.on_download_click)
        layout.addWidget(self.download_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(16, 17)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(self.on_close_click)
        layout.addWidget(self.close_btn)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        self.video_data = []
        self.current_rect = None
        self.target_hwnd = None
        self.target_window_title = ""
        self.should_be_visible = False
        self.is_closed_by_user = False
        self.is_compact = False
        self.setFixedSize(165, 26)

        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(250)
        self.sync_timer.timeout.connect(self.check_firefox_visibility)
        self.sync_timer.start()

    def on_close_click(self):
        self.is_closed_by_user = True
        self.should_be_visible = False
        self.hide()

    def reload_settings(self):
        panel_mode = "full"
        if self.settings_manager:
            cfg = self.settings_manager.settings.get("download_panels_config", {})
            panel_mode = cfg.get("web_players_view", "full")
        self.set_compact_mode(panel_mode == "mini")

    def _get_window_title(self, hwnd):
        if not hwnd: return ""
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        return buf.value

    def check_firefox_visibility(self):
        if self.is_closed_by_user or not self.should_be_visible or not self.target_hwnd:
            return
            
        user32 = ctypes.windll.user32

        if user32.IsIconic(self.target_hwnd) or not user32.IsWindowVisible(self.target_hwnd):
            if self.isVisible(): self.hide()
            return

        fg = user32.GetForegroundWindow()
        if fg and fg != self.target_hwnd and fg != int(self.winId()):
            fg_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
            target_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(self.target_hwnd, ctypes.byref(target_pid))
            if fg_pid.value != target_pid.value:
                if self.isVisible(): self.hide()
                return

        if self.target_window_title:
            current_title = self._get_window_title(self.target_hwnd)
            if current_title and current_title != self.target_window_title:
                if self.isVisible(): self.hide()
                return

        if not self.isVisible() and self.should_be_visible and not self.is_closed_by_user:
            self.show()
            self.raise_()

    def set_compact_mode(self, compact: bool):
        self.is_compact = compact
        if compact:
            self.setFixedSize(44, 24)
            self.download_btn.setText("▾")
        else:
            count = len(self.video_data)
            self.download_btn.setText(f"Download Video ({count}) ▾" if count > 1 else "Download Video ▾")
            self.adjustSize()
            self.setFixedHeight(25)

    def update_position(self, video_screen_rect: QRect, client_top: int, hwnd=None):
        if self.is_closed_by_user:
            return

        if hwnd:
            self.target_hwnd = hwnd
            self.target_window_title = self._get_window_title(hwnd)

        cfg_mode = "full"
        if self.settings_manager:
            cfg = self.settings_manager.settings.get("download_panels_config", {})
            cfg_mode = cfg.get("web_players_view", "full")

        if video_screen_rect.width() < 320 or cfg_mode == "mini":
            self.set_compact_mode(True)
        else:
            self.set_compact_mode(False)

        btn_w = self.width()
        btn_h = self.height()

        x = video_screen_rect.right() - btn_w - 2
        y = video_screen_rect.top() - btn_h - 4

        self.move(x, y)
        self.should_be_visible = True
        self.show()
        self.raise_()

    def set_video_data(self, data):
        if isinstance(data, list):
            self.video_data = data
        elif isinstance(data, dict):
            self.video_data = [data]
        else:
            self.video_data = []

        cfg_mode = "full"
        if self.settings_manager:
            cfg = self.settings_manager.settings.get("download_panels_config", {})
            cfg_mode = cfg.get("web_players_view", "full")

        if not self.is_compact and cfg_mode != "mini":
            count = len(self.video_data)
            self.download_btn.setText(f"Download Video ({count}) ▾" if count > 1 else "Download Video ▾")
            self.adjustSize()
            self.setFixedHeight(25)
                
        self.setup_menu(self.video_data)

    def setup_menu(self, options):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #f7f9fa;
                color: #111111;
                border: 1px solid #7a9ec2;
                font-family: "Segoe UI", sans-serif;
                font-size: 11px;
            }
            QMenu::item {
                padding: 6px 24px 6px 14px;
            }
            QMenu::item:selected {
                background-color: #2b78c5;
                color: #ffffff;
            }
        """)
        
        for idx, opt in enumerate(options, 1):
            title = opt.get('title') or opt.get('label') or 'Video'
            ext = opt.get('ext', 'MP4')
            res = opt.get('resolution', '')
            bw = opt.get('bandwidth_str', '')
            
            details = []
            if ext: details.append(f"{ext} file")
            if res: details.append(f"quality {res}")
            if bw: details.append(bw)
            
            detail_str = ", ".join(details)
            item_text = f"{idx}. {title}, {detail_str}" if detail_str else f"{idx}. {title}"
            
            action = QAction(item_text, self)
            action.setData(opt)
            action.triggered.connect(self.on_menu_action)
            menu.addAction(action)
            
        self.download_btn.setMenu(menu)

    def on_download_click(self):
        if not self.download_btn.menu() and self.video_data:
            self.download_requested.emit(self.video_data[0])

    def on_menu_action(self):
        action = self.sender()
        if action and action.data():
            self.download_requested.emit(action.data())