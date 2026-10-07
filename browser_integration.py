import sys
import os
import subprocess
import urllib.request
import urllib.error
import ssl
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None

BASE_DIR = Path(__file__).resolve().parent
EXTENSIONS_CACHE_DIR = BASE_DIR / "extensions"

CHROME_EXTENSION_ID = "ngpampappnmepgilojfohadhhmbhlaek"
EDGE_EXTENSION_ID = "llbjbkhnmlidjebalopleeepgdfgcpec"
FIREFOX_EXTENSION_ID = "mozilla_cc3@internetdownloadmanager.com"

CHROME_UPDATE_URL = "https://clients2.google.com/service/update2/crx"
EDGE_UPDATE_URL = "https://edge.microsoft.com/extensionproxy/one/cms1/edge/extensions/crx"

FIREFOX_DOWNLOAD_URLS = [
    "https://www.internetdownloadmanager.com/idmmzcc3.xpi",
    "https://addons.mozilla.org/firefox/downloads/latest/idm-integration-module/latest.xpi"
]
CHROME_CRX_DOWNLOAD_URL = (
    "https://clients2.google.com/service/update2/crx?response=redirect&prodversion=120.0"
    f"&acceptformat=crx2,crx3&x=id%3D{CHROME_EXTENSION_ID}%26uc"
)
EDGE_CRX_DOWNLOAD_URL = (
    "https://edge.microsoft.com/extensionproxy/one/cms1/edge/extensions/crx?response=redirect&prodversion=120.0"
    f"&x=id%3D{EDGE_EXTENSION_ID}%26uc"
)

DEFAULT_IDM_PATHS = [
    Path(r"C:\Program Files (x86)\Internet Download Manager"),
    Path(r"C:\Program Files\Internet Download Manager")
]

SUPPORTED_BROWSERS = [
    "Apple Safari",
    "Google Chrome",
    "Internet Explorer",
    "Microsoft Edge",
    "Microsoft Edge Legacy",
    "Mozilla Firefox",
    "Opera"
]

def find_idm_directory() -> Path | None:
    if winreg and sys.platform == "win32":
        for hkey in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hkey, r"Software\DownloadManager", 0, winreg.KEY_READ) as key:
                    path_val, _ = winreg.QueryValueEx(key, "ExePath")
                    p = Path(path_val)
                    if p.is_file():
                        return p.parent
                    elif p.is_dir():
                        return p
            except OSError:
                pass

    for p in DEFAULT_IDM_PATHS:
        if p.exists() and p.is_dir():
            return p

    return None

def download_extension_file(urls: list[str] | str, dest_path: Path) -> bool:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(urls, str):
        urls = [urls]

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*"
    }

    for url in urls:
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = resp.read()
                if len(data) > 1024:
                    with open(dest_path, "wb") as f:
                        f.write(data)
                    return True
        except Exception:
            continue

    return False

def get_or_download_firefox_xpi() -> Path | None:
    idm_dir = find_idm_directory()
    if idm_dir:
        for xpi_name in ("idmmzcc3.xpi", "idmmzcc2.xpi", "idmmzcc.xpi"):
            local_file = idm_dir / xpi_name
            if local_file.exists():
                return local_file

    cached_file = EXTENSIONS_CACHE_DIR / "firefox" / "idmmzcc3.xpi"
    if cached_file.exists() and cached_file.stat().st_size > 1024:
        return cached_file

    if download_extension_file(FIREFOX_DOWNLOAD_URLS, cached_file):
        return cached_file

    return None

def get_or_download_chrome_crx() -> Path | None:
    idm_dir = find_idm_directory()
    if idm_dir:
        local_file = idm_dir / "IDMGCExt.crx"
        if local_file.exists():
            return local_file

    cached_file = EXTENSIONS_CACHE_DIR / "chrome" / "IDMGCExt.crx"
    if cached_file.exists() and cached_file.stat().st_size > 1024:
        return cached_file

    if download_extension_file(CHROME_CRX_DOWNLOAD_URL, cached_file):
        return cached_file

    return None

def get_or_download_edge_crx() -> Path | None:
    idm_dir = find_idm_directory()
    if idm_dir:
        local_file = idm_dir / "IDMGCExt.crx"
        if local_file.exists():
            return local_file

    cached_file = EXTENSIONS_CACHE_DIR / "edge" / f"{EDGE_EXTENSION_ID}.crx"
    if cached_file.exists() and cached_file.stat().st_size > 1024:
        return cached_file

    if download_extension_file(EDGE_CRX_DOWNLOAD_URL, cached_file):
        return cached_file

    return None

def is_browser_installed(browser_name: str, custom_exe: str = None) -> bool:
    if custom_exe and Path(custom_exe).exists():
        return True

    if sys.platform != "win32":
        return True

    prog_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))

    if browser_name == "Google Chrome":
        candidates = [
            prog_files / "Google" / "Chrome" / "Application" / "chrome.exe",
            prog_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
            local_app_data / "Google" / "Chrome" / "Application" / "chrome.exe"
        ]
        return any(c.exists() for c in candidates)

    elif browser_name == "Microsoft Edge":
        candidates = [
            prog_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            prog_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        ]
        return any(c.exists() for c in candidates)

    elif browser_name == "Mozilla Firefox":
        candidates = [
            prog_files / "Mozilla Firefox" / "firefox.exe",
            prog_files_x86 / "Mozilla Firefox" / "firefox.exe"
        ]
        return any(c.exists() for c in candidates)

    elif browser_name == "Opera":
        candidates = [
            local_app_data / "Programs" / "Opera" / "launcher.exe",
            prog_files / "Opera" / "launcher.exe",
            prog_files_x86 / "Opera" / "launcher.exe"
        ]
        return any(c.exists() for c in candidates)

    elif browser_name == "Internet Explorer":
        candidates = [
            prog_files / "Internet Explorer" / "iexplore.exe",
            prog_files_x86 / "Internet Explorer" / "iexplore.exe"
        ]
        return any(c.exists() for c in candidates)

    elif browser_name == "Apple Safari":
        candidates = [
            prog_files_x86 / "Safari" / "Safari.exe",
            prog_files / "Safari" / "Safari.exe"
        ]
        return any(c.exists() for c in candidates)

    elif browser_name == "Microsoft Edge Legacy":
        return sys.platform == "win32"

    return True

def _install_chrome() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    crx_path = get_or_download_chrome_crx()

    try:
        reg_path = rf"Software\Google\Chrome\Extensions\{CHROME_EXTENSION_ID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "update_url", 0, winreg.REG_SZ, CHROME_UPDATE_URL)
            if crx_path and crx_path.exists():
                winreg.SetValueEx(key, "path", 0, winreg.REG_SZ, str(crx_path))
                winreg.SetValueEx(key, "version", 0, winreg.REG_SZ, "6.42.0")

        policy_path = r"Software\Policies\Google\Chrome\ExtensionInstallForcelist"
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, policy_path, 0, winreg.KEY_SET_VALUE) as p_key:
                val_entry = f"{CHROME_EXTENSION_ID};{CHROME_UPDATE_URL}"
                winreg.SetValueEx(p_key, "1", 0, winreg.REG_SZ, val_entry)
        except OSError:
            pass

        return True, "Chrome extension configured and ready."
    except Exception as e:
        return False, f"Failed to register Chrome extension: {e}"

def _uninstall_chrome() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    try:
        reg_path = rf"Software\Google\Chrome\Extensions\{CHROME_EXTENSION_ID}"
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, reg_path)
        except OSError:
            pass

        policy_path = r"Software\Policies\Google\Chrome\ExtensionInstallForcelist"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, policy_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as p_key:
                try:
                    val, _ = winreg.QueryValueEx(p_key, "1")
                    if CHROME_EXTENSION_ID in val:
                        winreg.DeleteValue(p_key, "1")
                except OSError:
                    pass
        except OSError:
            pass

        return True, "Chrome extension unregistered."
    except Exception as e:
        return False, f"Failed to unregister Chrome extension: {e}"

def _install_edge() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    crx_path = get_or_download_edge_crx()

    try:
        reg_path = rf"Software\Microsoft\Edge\Extensions\{EDGE_EXTENSION_ID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "update_url", 0, winreg.REG_SZ, EDGE_UPDATE_URL)
            if crx_path and crx_path.exists():
                winreg.SetValueEx(key, "path", 0, winreg.REG_SZ, str(crx_path))
                winreg.SetValueEx(key, "version", 0, winreg.REG_SZ, "6.42.0")

        cr_reg_path = rf"Software\Microsoft\Edge\Extensions\{CHROME_EXTENSION_ID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, cr_reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "update_url", 0, winreg.REG_SZ, CHROME_UPDATE_URL)

        policy_path = r"Software\Policies\Microsoft\Edge\ExtensionInstallForcelist"
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, policy_path, 0, winreg.KEY_SET_VALUE) as p_key:
                val_entry = f"{EDGE_EXTENSION_ID};{EDGE_UPDATE_URL}"
                winreg.SetValueEx(p_key, "1", 0, winreg.REG_SZ, val_entry)
        except OSError:
            pass

        return True, "Edge extension configured and ready."
    except Exception as e:
        return False, f"Failed to register Edge extension: {e}"

def _uninstall_edge() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    try:
        for ext_id in (EDGE_EXTENSION_ID, CHROME_EXTENSION_ID):
            reg_path = rf"Software\Microsoft\Edge\Extensions\{ext_id}"
            try:
                winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, reg_path)
            except OSError:
                pass

        policy_path = r"Software\Policies\Microsoft\Edge\ExtensionInstallForcelist"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, policy_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as p_key:
                try:
                    winreg.DeleteValue(p_key, "1")
                except OSError:
                    pass
        except OSError:
            pass

        return True, "Edge extension unregistered."
    except Exception as e:
        return False, f"Failed to unregister Edge extension: {e}"

def _install_firefox() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    xpi_path = get_or_download_firefox_xpi()
    val_to_write = str(xpi_path) if xpi_path else FIREFOX_DOWNLOAD_URLS[0]

    try:
        reg_path = r"Software\Mozilla\Firefox\Extensions"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, FIREFOX_EXTENSION_ID, 0, winreg.REG_SZ, val_to_write)

        policy_path = r"Software\Policies\Mozilla\Firefox\Extensions\Install"
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, policy_path, 0, winreg.KEY_SET_VALUE) as p_key:
                winreg.SetValueEx(p_key, "1", 0, winreg.REG_SZ, val_to_write)
        except OSError:
            pass

        return True, "Firefox extension configured and ready."
    except Exception as e:
        return False, f"Failed to register Firefox extension: {e}"

def _uninstall_firefox() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    try:
        reg_path = r"Software\Mozilla\Firefox\Extensions"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, FIREFOX_EXTENSION_ID)
        except OSError:
            pass

        policy_path = r"Software\Policies\Mozilla\Firefox\Extensions\Install"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, policy_path, 0, winreg.KEY_SET_VALUE) as p_key:
                try:
                    winreg.DeleteValue(p_key, "1")
                except OSError:
                    pass
        except OSError:
            pass

        return True, "Firefox extension unregistered."
    except Exception as e:
        return False, f"Failed to unregister Firefox extension: {e}"

def _install_opera() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    crx_path = get_or_download_chrome_crx()

    try:
        reg_path = rf"Software\Opera Software\Extensions\{CHROME_EXTENSION_ID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "update_url", 0, winreg.REG_SZ, CHROME_UPDATE_URL)
            if crx_path and crx_path.exists():
                winreg.SetValueEx(key, "path", 0, winreg.REG_SZ, str(crx_path))
                winreg.SetValueEx(key, "version", 0, winreg.REG_SZ, "6.42.0")
        return True, "Opera extension configured and ready."
    except Exception as e:
        return False, f"Failed to register Opera extension: {e}"

def _uninstall_opera() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."

    try:
        reg_path = rf"Software\Opera Software\Extensions\{CHROME_EXTENSION_ID}"
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, reg_path)
        except OSError:
            pass
        return True, "Opera extension unregistered."
    except Exception as e:
        return False, f"Failed to unregister Opera extension: {e}"

def _install_ie() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Windows required."

    idm_dir = find_idm_directory()
    success = False

    if idm_dir:
        dll64 = idm_dir / "IDMIECC64.dll"
        dll32 = idm_dir / "IDMIECC.dll"
        for dll in (dll64, dll32):
            if dll.exists():
                res = subprocess.run(["regsvr32.exe", "/s", str(dll)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                if res.returncode == 0:
                    success = True

    if winreg:
        try:
            bho_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Browser Helper Objects\{07999AC3-058B-40BF-984F-69EB1E554CA7}"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, bho_key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "NoExplorer", 0, winreg.REG_DWORD, 1)
            success = True
        except OSError:
            pass

    return (True, "Internet Explorer integration registered.") if success else (False, "IE module registration requires local DLLs.")

def _uninstall_ie() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Windows required."

    idm_dir = find_idm_directory()
    if idm_dir:
        dll64 = idm_dir / "IDMIECC64.dll"
        dll32 = idm_dir / "IDMIECC.dll"
        for dll in (dll64, dll32):
            if dll.exists():
                subprocess.run(["regsvr32.exe", "/u", "/s", str(dll)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    if winreg:
        try:
            bho_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Browser Helper Objects\{07999AC3-058B-40BF-984F-69EB1E554CA7}"
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, bho_key)
        except OSError:
            pass

    return True, "Internet Explorer integration unregistered."

def _install_safari() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."
    try:
        safari_key = r"Software\Apple Computer, Inc.\Safari\Extensions"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, safari_key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "IDMIntegration", 0, winreg.REG_DWORD, 1)
        return True, "Safari integration enabled."
    except Exception as e:
        return False, f"Failed to configure Safari integration: {e}"

def _uninstall_safari() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."
    try:
        safari_key = r"Software\Apple Computer, Inc.\Safari\Extensions"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, safari_key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "IDMIntegration")
        except OSError:
            pass
        return True, "Safari integration disabled."
    except Exception as e:
        return False, f"Failed to disable Safari integration: {e}"

def _install_edge_legacy() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."
    try:
        reg_path = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\SystemAppData\Microsoft.MicrosoftEdge_8wekyb3d8bbwe"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "IDMIntegrationEnabled", 0, winreg.REG_DWORD, 1)
        return True, "Edge Legacy integration enabled."
    except Exception as e:
        return False, f"Failed to configure Edge Legacy integration: {e}"

def _uninstall_edge_legacy() -> tuple[bool, str]:
    if not winreg or sys.platform != "win32":
        return False, "Windows Registry not available."
    try:
        reg_path = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\SystemAppData\Microsoft.MicrosoftEdge_8wekyb3d8bbwe"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "IDMIntegrationEnabled")
        except OSError:
            pass
        return True, "Edge Legacy integration disabled."
    except Exception as e:
        return False, f"Failed to disable Edge Legacy integration: {e}"

def install_browser_extension(browser_name: str, custom_exe_path: str = None) -> tuple[bool, str]:
    if browser_name == "Google Chrome":
        return _install_chrome()
    elif browser_name == "Microsoft Edge":
        return _install_edge()
    elif browser_name == "Mozilla Firefox":
        return _install_firefox()
    elif browser_name == "Opera":
        return _install_opera()
    elif browser_name == "Internet Explorer":
        return _install_ie()
    elif browser_name == "Apple Safari":
        return _install_safari()
    elif browser_name == "Microsoft Edge Legacy":
        return _install_edge_legacy()
    else:
        lower_name = (custom_exe_path or browser_name).lower()
        if any(gecko in lower_name for gecko in ("firefox", "waterfox", "librewolf", "floorp", "palemoon", "seamonkey")):
            return _install_firefox()
        else:
            return _install_chrome()

def uninstall_browser_extension(browser_name: str, custom_exe_path: str = None) -> tuple[bool, str]:
    if browser_name == "Google Chrome":
        return _uninstall_chrome()
    elif browser_name == "Microsoft Edge":
        return _uninstall_edge()
    elif browser_name == "Mozilla Firefox":
        return _uninstall_firefox()
    elif browser_name == "Opera":
        return _uninstall_opera()
    elif browser_name == "Internet Explorer":
        return _uninstall_ie()
    elif browser_name == "Apple Safari":
        return _uninstall_safari()
    elif browser_name == "Microsoft Edge Legacy":
        return _uninstall_edge_legacy()
    else:
        lower_name = (custom_exe_path or browser_name).lower()
        if any(gecko in lower_name for gecko in ("firefox", "waterfox", "librewolf", "floorp", "palemoon", "seamonkey")):
            return _uninstall_firefox()
        else:
            return _uninstall_chrome()