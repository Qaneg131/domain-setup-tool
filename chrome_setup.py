#!/usr/bin/env python3
"""
Setup domain Namecheap -> Cloudflare BẰNG CHROME ĐÃ ĐĂNG NHẬP.
KHÔNG cần API key / token gì hết. Không cần nhập mật khẩu vào tool.

Cách nó chạy:
  1. Tool nối vào Chrome đang mở ở máy bạn (đã đăng nhập sẵn Cloudflare
     + Namecheap), qua cổng remote-debugging 9222 — nếu bạn chạy
     start-chrome.bat/.sh trước.
     - Nếu không, tool TỰ COPY profile Chrome sang thư mục RIÊNG CỦA TOOL
       (chrome-profile-tool/) rồi mở Chrome từ đó (vẫn đăng nhập sẵn, vì
       cookie mã hoá theo tài khoản Windows của chính máy đó). Chrome thật
       của bạn cứ mở bình thường, KHÔNG cần đóng gì hết. Thư mục này được
       GIỮ LẠI giữa các lần chạy nên cookie đăng nhập còn nguyên: đăng
       nhập 1 lần trong Chrome của tool, lần sau mở lên vẫn đăng nhập sẵn,
       không phải đăng nhập lại. Muốn đăng xuất hết thì xoá thư mục đó
       (nút "Reset profile tool" trong app, hoặc --reset-profile).
  2. Tool tự click thay bạn, y như bạn làm tay (tab làm việc mở riêng và
     đưa lên trước để bạn xem trực tiếp):
     Phase 1: Cloudflare Add site (Free) -> lấy 2 nameserver
              -> Namecheap Manage domain -> Nameservers -> Custom DNS
              -> dán 2 NS -> lưu (tới bước 3)
     Phase 2: Cloudflare DNS > Records -> Add record từng ô
              (3 ô Type/Name/Content, ô nào trống thì bỏ qua)
  3. Verify NS bằng DNS 1.1.1.1 + ghi CSV vào reports/

Chạy ở máy bạn (xem README, khuyên dùng app desktop: run-app.bat):
    pip install -r requirements.txt
    playwright install chromium   # chỉ cần 1 lần, để có driver Playwright
    python3 chrome_setup.py --domains-file domains.txt --phase 1
    python3 chrome_setup.py --domains-file domains.txt --phase 2 --records-file custom-records.json

Lưu ý: giao diện Cloudflare/Namecheap thỉnh thoảng đổi nút/chữ. Tool dùng
nhiều cách tìm nút dự phòng (theo chữ, theo role), nếu 1 bước không tìm
được nút thì nó chụp màn hình + lưu vào reports/ rồi dừng ở domain đó,
các domain khác vẫn chạy tiếp. Bạn mở ảnh đó lên là biết nó kẹt ở đâu.
"""
import argparse
import csv
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import requests

# Windows: console mặc định dùng cp1258, không in được vài chữ tiếng Việt
# (vd chữ "ổ" trong "không đổi gì thật") -> ép stdout/stderr sang UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    # Để --help vẫn chạy được khi chưa cài playwright; lỗi chỉ báo khi chạy thật.
    sync_playwright = None

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
CDP_PORT = 9222
NS_RE = re.compile(r"[a-z0-9][a-z0-9\-]*\.ns\.cloudflare\.com", re.I)


def log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # Phòng khi console vẫn không phải UTF-8: in bản không dấu thay vì crash.
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def tool_version():
    """Mã commit đang chạy — in ra log để biết chắc user đang chạy bản mới
    hay bản cũ (nhiều lần sửa xong user chạy lại vẫn là bản cũ vì chưa pull)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(BASE_DIR),
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


# ---------------------------------------------------------------- profile Chrome
def default_profile_dir():
    system = platform.system()
    home = Path.home()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        return Path(local) / "Google" / "Chrome" / "User Data" if local else home
    if system == "Darwin":
        return home / "Library" / "Application Support" / "Google" / "Chrome"
    return home / ".config" / "google-chrome"


def default_chrome_exe():
    system = platform.system()
    candidates = []
    if system == "Windows":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pfx = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
                      Path(pfx) / "Google" / "Chrome" / "Application" / "chrome.exe",
                      Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    elif system == "Darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    else:
        candidates = [Path("/usr/bin/google-chrome"), Path("/usr/bin/google-chrome-stable"),
                      Path("/opt/google/chrome/chrome")]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def cdp_alive(port=CDP_PORT):
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------- bước 1: mở tab đăng nhập
CLOUDFLARE_LOGIN_URL = "https://dash.cloudflare.com/login"
NAMECHEAP_LOGIN_URL = "https://www.namecheap.com/myaccount/login/"
SESSION_FILE = REPORTS_DIR / "last-chrome.json"


def write_session(data):
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except Exception:
        pass


def open_tab_via_cdp(port, url):
    """Mở 1 tab mới trong Chrome đang chạy (qua cổng debug HTTP)."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{urllib.parse.urlencode({'url': url})}",
            method="PUT")
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log(f"  Mở tab {url} lỗi: {e}")
        return False


def prepare_login_chrome(args, profile, profile_dir_name, chrome_exe):
    """Nút 1 — chỉ mở Chrome với 2 tab đăng nhập Cloudflare + Namecheap,
    rồi DỪNG Ở ĐÓ. Không điều khiển trang, không kiểm tra, không reload —
    user tự đăng nhập xong rồi bấm nút 2 để chạy tiếp. Trả về exit code."""
    if cdp_alive(args.cdp_port):
        log(f"Chrome đã mở sẵn qua cổng debug {args.cdp_port} — mở 2 tab đăng nhập trong đó.")
        open_tab_via_cdp(args.cdp_port, CLOUDFLARE_LOGIN_URL)
        open_tab_via_cdp(args.cdp_port, NAMECHEAP_LOGIN_URL)
        write_session({"cdp_port": args.cdp_port, "profile_copy": None, "mode": "cdp"})
        log("Đã mở 2 tab: Cloudflare + Namecheap.")
        log("Đăng nhập xong cả 2 tab rồi bấm nút 2 (Chạy tiếp).")
        return 0
    if not chrome_exe or not Path(chrome_exe).exists():
        log("Không tìm thấy chrome.exe — hãy chạy start-chrome.bat trước rồi bấm nút 1 lại.")
        return 2
    profile_copy = get_tool_profile_dir(profile, profile_dir_name, fresh=args.fresh_profile)
    use_dir = profile_copy or profile
    if not profile_copy:
        log("  (Không copy được profile — Chrome sẽ mở bằng profile thật,")
        log("   tab đăng nhập vẫn mở, nhưng lúc chạy có thể phải đăng nhập lại.)")
    cmd = [chrome_exe, f"--user-data-dir={use_dir}",
           f"--remote-debugging-port={args.cdp_port}", "--no-first-run",
           CLOUDFLARE_LOGIN_URL, NAMECHEAP_LOGIN_URL]
    log("Mở Chrome với 2 tab đăng nhập (Cloudflare + Namecheap)...")
    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)  # Chrome tách ra chạy riêng, không đóng khi tool thoát
    for _ in range(30):
        if cdp_alive(args.cdp_port):
            break
        time.sleep(1)
    if not cdp_alive(args.cdp_port):
        log("Chrome mở lên nhưng không bật được cổng debug — thử bấm nút 1 lại.")
        return 2
    write_session({"cdp_port": args.cdp_port, "profile_copy": profile_copy, "mode": "copy"})
    log("Đã mở Chrome với 2 tab: Cloudflare + Namecheap.")
    log("Đăng nhập xong cả 2 tab rồi bấm nút 2 (Chạy tiếp).")
    return 0


def chrome_already_running():
    """Chrome đang mở sẵn thì profile bị khoá: tool mở Chrome mới sẽ chỉ
    hiện 1 cửa sổ about:blank trong Chrome cũ và tool không điều khiển
    được gì. Phát hiện trước để báo rõ, thay vì mở cửa sổ trắng rồi kẹt."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe",
                                  "/FO", "CSV"], capture_output=True, text=True,
                                 timeout=10).stdout
            return "chrome.exe" in out.lower()
        import shutil
        if shutil.which("pgrep"):
            r = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True,
                               timeout=10)
            if r.returncode == 0:
                return True
            r = subprocess.run(["pgrep", "-x", "chrome"], capture_output=True,
                               timeout=10)
            return r.returncode == 0
    except Exception:
        pass
    return False


# ------------------------------------------------------- copy profile Chrome
# Vấn đề gốc: Chrome khoá profile đang dùng, nên tool không thể mở Chrome
# bằng profile thật khi Chrome của user đang mở — chỉ hiện about:blank rồi kẹt.
# Cách sửa: COPY profile sang thư mục tạm rồi mở Chrome từ bản copy đó.
# Cookie Chrome mã hoá gắn với tài khoản Windows của chính máy đó, nên bản
# copy mở lên vẫn đăng nhập sẵn Cloudflare + Namecheap như Chrome thật.
# Chrome thật của user cứ mở bình thường, không cần đóng gì hết.
# Lưu ý: KHÔNG loại thư mục "Sessions" — phiên đăng nhập của Cloudflare
# là session cookie, Chrome khôi phục nó từ file trong Sessions. Lần trước
# loại thư mục này nên bản copy mở lên bắt đăng nhập lại dù Chrome thật
# đã đăng nhập sẵn (email thì nhớ, vì email lưu bằng cookie thường).
COPY_SKIP_DIRS = {"Cache", "Code Cache", "GPUCache", "Service Worker",
                  "File System", "blob_storage", "Crashpad", "ShaderCache",
                  "DawnCache", "GraphiteDawnCache",
                  "optimization_guide_model_store", "DownloadService"}
COPY_SKIP_FILES = {"SingletonLock", "SingletonCookie", "SingletonSocket", "LOCK"}


def list_chrome_profiles(profile_root):
    """Liệt kê các hồ sơ Chrome trong thư mục User Data.
    Trả về (danh_sách, last_used) với danh_sách = [(thư_mục, tên_hiển_thị)].
    Tên hiển thị lấy từ Local State (profile.info_cache) — chính là tên
    hiện trong menu đổi hồ sơ của Chrome. last_used là hồ sơ dùng gần nhất.
    """
    root = Path(profile_root)
    profiles = []
    last_used = None
    try:
        ls = root / "Local State"
        if ls.exists():
            data = json.loads(ls.read_text(encoding="utf-8"))
            prof = data.get("profile", {}) or {}
            last_used = prof.get("last_used")
            cache = prof.get("info_cache", {}) or {}
            for dirname, info in cache.items():
                if (root / dirname).is_dir():
                    profiles.append((dirname, (info or {}).get("name") or dirname))
    except Exception:
        pass
    if not profiles:
        for d in sorted(root.iterdir()) if root.exists() else []:
            if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile ")):
                profiles.append((d.name, d.name))
    if last_used not in [p[0] for p in profiles]:
        last_used = profiles[0][0] if profiles else None
    return profiles, last_used


def cleanup_stale_copies(max_age_hours=24):
    """Xoá các bản copy profile cũ hơn 1 ngày trong thư mục tạm."""
    try:
        tmp = Path(tempfile.gettempdir())
        now = time.time()
        for d in tmp.glob("domain-chrome-profile-*"):
            try:
                if d.is_dir() and now - d.stat().st_mtime > max_age_hours * 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


# Thư mục profile RIÊNG CỦA TOOL, giữ lâu dài để cookie đăng nhập còn
# nguyên giữa các lần chạy (xem get_tool_profile_dir). Chứa session cookie
# nên TUYỆT ĐỐI không commit lên git — đã thêm vào .gitignore.
TOOL_PROFILE_ROOT = BASE_DIR / "chrome-profile-tool"


def tool_profile_dir(profile_name="Default"):
    """Đường dẫn thư mục profile tool của 1 hồ sơ Chrome (vd 'Profile 1')."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_name or "Default").strip("._") or "Default"
    return TOOL_PROFILE_ROOT / safe


def get_tool_profile_dir(profile_root, profile_name="Default", fresh=False):
    """Trả về thư mục profile tool để mở Chrome từ đó.
    - Lần đầu (chưa có thư mục): copy từ hồ sơ Chrome thật như trước.
    - Các lần sau: DÙNG LẠI y nguyên, KHÔNG copy đè — copy đè sẽ xoá mất
      cookie đăng nhập trong Chrome của tool, làm user phải đăng nhập lại
      mỗi lần chạy (đúng cái user than phiền).
    - fresh=True: xoá rồi copy lại từ đầu (đăng xuất hết / làm mới)."""
    dest = tool_profile_dir(profile_name)
    if fresh:
        shutil.rmtree(dest, ignore_errors=True)
    if (dest / "Local State").exists() or (dest / "Default").exists():
        log(f"Dùng lại profile tool đã lưu: {dest} (cookie đăng nhập còn nguyên).")
        return str(dest)
    copied = prepare_profile_copy(profile_root, profile_name, dest_dir=str(dest))
    if copied:
        log("  Lần đầu: đã copy hồ sơ Chrome sang profile tool — đăng nhập")
        log("  1 lần trong Chrome của tool, các lần sau mở lên vẫn còn.")
    return copied


def prepare_profile_copy(profile_root, profile_name="Default", dest_dir=None):
    """Copy 1 hồ sơ Chrome thật -> dest_dir (mặc định: thư mục tạm), đặt
    thành Default trong bản copy để Chrome mở lên dùng đúng hồ sơ đó.
    Trả về đường dẫn bản copy, hoặc None nếu không copy được (sẽ dùng
    profile thật như cũ). profile_name là tên thư mục hồ sơ, vd 'Default'
    hay 'Profile 1'."""
    src = Path(profile_root)
    if not src.exists():
        log(f"  Không thấy profile Chrome ở {src}")
        return None
    if dest_dir is None:
        cleanup_stale_copies()
        dst = Path(tempfile.mkdtemp(prefix="domain-chrome-profile-"))
    else:
        dst = Path(dest_dir)
        dst.mkdir(parents=True, exist_ok=True)
    copied = [0]
    skipped = []

    def tolerant_copy(s, d):
        try:
            shutil.copy2(s, d)
            copied[0] += 1
        except Exception as e:
            # File đang bị Chrome khoá/ghi dở (vd Cookies) -> bỏ qua file đó,
            # không crash cả quá trình copy.
            skipped.append(f"{Path(s).name}: {e}")

    # Local State chứa khoá mã hoá cookie — bắt buộc phải có để giữ đăng nhập.
    local_state = src / "Local State"
    chosen_src = src / profile_name
    if not local_state.exists() and not chosen_src.exists():
        log(f"  Profile Chrome ở {src} trống/không có gì để copy — bỏ qua bản copy.")
        shutil.rmtree(dst, ignore_errors=True)
        return None
    if local_state.exists():
        tolerant_copy(str(local_state), str(dst / "Local State"))
    if chosen_src.exists():
        def ignore(d, names):
            return [n for n in names
                    if n in COPY_SKIP_DIRS or n in COPY_SKIP_FILES]
        try:
            shutil.copytree(chosen_src, dst / "Default", ignore=ignore,
                            copy_function=tolerant_copy)
        except Exception as e:
            log(f"  Copy profile lỗi: {e}")
            return None
    # Sửa Local State trong bản copy: bảo Chrome mở Default (chính là hồ sơ
    # vừa copy sang), tránh trường hợp nó mở hồ sơ khác còn ghi trong file.
    try:
        ls_path = dst / "Local State"
        if ls_path.exists():
            data = json.loads(ls_path.read_text(encoding="utf-8"))
            prof = data.setdefault("profile", {})
            info = (prof.get("info_cache", {}) or {}).get(profile_name, {}) or {}
            prof["info_cache"] = {"Default": info if info.get("name") else {"name": profile_name}}
            prof["last_used"] = "Default"
            prof["last_active_profiles"] = ["Default"]
            ls_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    log(f"  Đã copy hồ sơ Chrome '{profile_name}' sang {dst} ({copied[0]} file)"
        + (f", bỏ qua {len(skipped)} file đang khoá" if skipped else "")
        + " — Chrome thật của bạn vẫn mở bình thường, không cần đóng.")
    return str(dst)


# ---------------------------------------------------------------- helpers click
def click_first(page, description, timeout=8000, **locators):
    """Thử lần lượt nhiều cách tìm nút, cái nào thấy thì click cái đó."""
    tried = []
    for name, loc in locators.items():
        try:
            el = loc
            el.wait_for(state="visible", timeout=timeout)
            el.click(timeout=timeout)
            log(f"    click: {description} ({name})")
            return True
        except Exception as e:
            tried.append(f"{name}: {str(e)[:80]}")
    log(f"    KHÔNG tìm được nút '{description}'. Đã thử: {'; '.join(tried)}")
    return False


def click_text(page, texts, timeout=8000):
    """Click theo chữ hiển thị trên nút/link (thử từng chữ)."""
    if isinstance(texts, str):
        texts = [texts]
    for t in texts:
        try:
            el = page.get_by_role("button", name=re.compile(re.escape(t), re.I)).first
            el.wait_for(state="visible", timeout=2000)
            el.click(timeout=2000)
            log(f"    click: '{t}'")
            return True
        except Exception:
            pass
        try:
            el = page.get_by_text(t, exact=False).first
            el.wait_for(state="visible", timeout=2000)
            el.click(timeout=2000)
            log(f"    click text: '{t}'")
            return True
        except Exception:
            pass
    return False


def _click_visible(page, texts, timeout=8000):
    """Bấm nút/link/chữ đầu tiên đang HIỂN THỊ trong danh sách texts.
    Khác click_text ở chỗ không lấy .first (hay trúng phần tử ẩn) mà
    duyệt và chỉ bấm cái đang hiện; chờ tới timeout vì trang SPA load
    chậm. Trả về True nếu bấm được."""
    if isinstance(texts, str):
        texts = [texts]
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for t in texts:
            for loc in [page.get_by_role("button", name=re.compile(re.escape(t), re.I)),
                        page.get_by_role("link", name=re.compile(re.escape(t), re.I)),
                        page.get_by_text(t, exact=False)]:
                # _first_visible_now định nghĩa ở mục Namecheap bên dưới —
                # Python tìm tên lúc gọi nên dùng được bình thường.
                el = _first_visible_now(loc)
                if el:
                    try:
                        el.click(timeout=2000)
                        log(f"    click: '{t}'")
                        return True
                    except Exception:
                        continue
        page.wait_for_timeout(400)
    return False


def wait_text(page, text, timeout=15000):
    try:
        page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


def shot(page, domain, step):
    try:
        p = REPORTS_DIR / f"shot_{domain}_{step}_{int(time.time())}.png"
        page.screenshot(path=str(p))
        log(f"    đã chụp màn hình: {p.name}")
    except Exception:
        pass


def extract_ns(page):
    try:
        text = page.content()
    except Exception:
        text = ""
    found = []
    for m in NS_RE.findall(text):
        ns = m.lower()
        if ns not in found:
            found.append(ns)
    return found[:2]


def doh_ns(domain):
    try:
        r = requests.get("https://cloudflare-dns.com/dns-query",
                         params={"name": domain, "type": "NS"},
                         headers={"Accept": "application/dns-json"}, timeout=10)
        ans = r.json().get("Answer", [])
        return sorted({a["data"].rstrip(".").lower() for a in ans if a.get("type") == 2})
    except Exception as e:
        log(f"    verify NS lỗi: {e}")
        return []


def safe_goto(page, url, what="trang"):
    """Mở trang, timeout rõ ràng + thử lại 1 lần. Nếu vẫn không mở được thì
    báo lỗi dễ hiểu thay vì để tab đứng ở about:blank với traceback dài
    (bản cũ: goto timeout là tool chết luôn, Chrome bỏ lại toàn tab trắng)."""
    for attempt in (1, 2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except Exception as e:
            log(f"  Mở {what} ({url}) lần {attempt} lỗi: {str(e)[:150]}")
            if attempt == 2:
                raise RuntimeError(f"không mở được {what} ({url}) — kiểm tra mạng "
                                   "và Chrome có bị kẹt ở trang trắng không")
            page.wait_for_timeout(2000)


def _any_visible(page, text, max_check=20):
    """True nếu CÓ ÍT NHẤT 1 chỗ hiện chữ text đang HIỂN THỊ trên trang.
    Không được chỉ kiểm tra phần tử đầu tiên (.first): trên trang thật,
    phần tử đầu tiên khớp chữ thường nằm trong menu dropdown đang ẨN
    (menu user, menu Domains, menu Actions đứng trước nội dung chính
    trong DOM) nên .first.is_visible() luôn False dù trang đã đăng nhập
    và chữ đó đang hiện rõ ở chỗ khác — đó chính là bug làm tool chờ mãi
    trên trang Domain List đã đăng nhập. is_visible() kiểm tra ngay
    (không chờ), nên duyệt tối đa 20 phần tử vẫn rất nhanh."""
    try:
        for el in page.get_by_text(text).all()[:max_check]:
            try:
                if el.is_visible():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- login check
def _show(page):
    """Đưa tab làm việc lên trước để user xem trực tiếp bước đang chạy
    (2 tab làm việc: Namecheap + Cloudflare, đổi tab qua lại)."""
    try:
        page.bring_to_front()
    except Exception:
        pass


def ensure_logged_in(page, site, start_url, logged_in_texts, wait_minutes=5):
    # Không dùng input() nữa: app/web chạy nền không có bàn phím để nhấn Enter.
    # Thay vào đó tự kiểm tra mỗi 5 giây — user đăng nhập xong trong cửa sổ
    # Chrome vừa mở là tool tự chạy tiếp. Quá 5 phút vẫn chưa đăng nhập thì lỗi.
    # QUAN TRỌNG 1: chỉ điều hướng 1 lần duy nhất rồi CHỈ ĐỌC nội dung khi chờ.
    # Bản cũ gọi page.goto() trong mỗi vòng lặp (~7 giây/lần) nên trang cứ
    # reload liên tục, user đang gõ mật khẩu / làm xác minh thì bị reset —
    # không đăng nhập được.
    # QUAN TRỌNG 2: KHÔNG tìm chuỗi "sign in"/"log in" trong toàn bộ HTML nữa.
    # Trang dashboard đã đăng nhập vẫn chứa mấy chuỗi đó ở chỗ ẩn (menu đổi
    # tài khoản, Turnstile, script...) nên bản cũ tưởng chưa đăng nhập và
    # đứng im chờ đủ 5 phút dù user đã đăng nhập xong. Giờ chỉ tin 2 dấu hiệu:
    # URL có phải trang login không, và có thấy dòng chữ chỉ hiện KHI ĐÃ
    # đăng nhập không (Cloudflare: "Account home" ở menu trái; Namecheap:
    # "Domain List" tiêu đề trang / nút "MANAGE" ở từng dòng domain).
    # LƯU Ý: đừng chọn chữ nằm trong menu ẩn — "My Account" của Namecheap
    # chỉ nằm trong menu dropdown của tên user nên trang Domain List đã
    # đăng nhập vẫn không thấy nó, tool cứ chờ mãi dù user đã đăng nhập.
    # Chưa đăng nhập thì trang login sẽ redirect về URL chứa "login"
    # nên vẫn bắt được.
    if isinstance(logged_in_texts, str):
        logged_in_texts = [logged_in_texts]
    deadline = time.time() + wait_minutes * 60
    warned = False
    last_reminder = 0.0
    safe_goto(page, start_url, site)
    page.wait_for_timeout(2000)
    while True:
        url = page.url.lower()
        if any(m in url for m in ("login", "sign-in", "signin")):
            needs_login = True
        else:
            needs_login = not any(_any_visible(page, t) for t in logged_in_texts)
        if not needs_login:
            log(f"  đã đăng nhập {site} (Chrome sẵn).")
            return
        if time.time() > deadline:
            raise RuntimeError(f"chưa đăng nhập {site} sau {wait_minutes} phút — "
                               "hãy đăng nhập trong cửa sổ Chrome rồi chạy lại")
        if not warned:
            # Nói rõ trang đang hiện gì: nhiều khi URL vẫn là /domains/list
            # nhưng Namecheap hiện form đăng nhập ngay tại URL đó khi chưa
            # đăng nhập — user nhìn URL tưởng đã vào được list.
            on_login_form = _any_visible(page, "Log in to your account") or \
                _any_visible(page, "Sign in")
            try:
                title = page.title()
            except Exception:
                title = "(không đọc được tiêu đề)"
            log(f"  !! Chưa đăng nhập {site} ở Chrome này. Đang ở: {page.url}")
            log(f"     Tiêu đề tab tool đang nhìn: {title}")
            if on_login_form:
                log("     (Trang đang hiện form đăng nhập — chưa vào được, hãy nhập tài khoản rồi bấm Sign in.)")
            else:
                log("     (Không thấy form đăng nhập — chụp lại màn hình tab tool đang nhìn để kiểm tra.)")
            shot(page, site, "waiting-login")
            log("     Hãy đăng nhập NGAY TRONG cửa sổ Chrome vừa mở,")
            log("     tool sẽ tự chạy tiếp khi thấy đã đăng nhập (không cần bấm gì).")
            warned = True
            last_reminder = time.time()
        elif time.time() - last_reminder > 30:
            try:
                title = page.title()
            except Exception:
                title = "(không đọc được tiêu đề)"
            log(f"  ... vẫn đang đợi đăng nhập {site} (đang ở: {page.url} | tiêu đề: {title})")
            last_reminder = time.time()
        page.wait_for_timeout(5000)


# ---------------------------------------------------------------- Cloudflare
def _is_free_card_button(btn):
    """Nút 'Select plan' có nằm trong thẻ gói Free không (thẻ Free là
    thẻ duy nhất chứa '$0'). Không kiểm tra được thì coi như đúng
    (nút Select plan đầu tiên HIỂN THỊ vốn là của thẻ Free xếp đầu)."""
    try:
        return btn.evaluate("""e => {
          let a = e;
          while (a) {
            const t = a.textContent || '';
            if (t.includes('$0')) return true;
            if (t.includes('Pro') || t.includes('Business') || t.includes('Enterprise')) return false;
            a = a.parentElement;
          }
          return false;
        }""")
    except Exception:
        return True


def _select_free_plan(page, domain):
    """Chọn gói Free ở Cloudflare. Trang gói load sau khi gọi API nên
    phải ĐỢI nó hiện ra (tối đa 15s) thay vì kiểm tra 1 lần ngay sau
    Continue — welvella.com trượt đúng chỗ này: lúc kiểm tra trang
    chưa load xong nên bước chọn gói bị bỏ qua, tới bước sau mới thấy
    đang đứng ở trang gói. Bấm nút 'Select plan' trong thẻ Free rồi
    kiểm tra trang đã rời khỏi trang gói chưa; chưa thì bấm lại bằng
    JavaScript. Trả về True nếu đã qua khỏi trang gói (hoặc không có
    trang gói — đi thẳng tới Review); raise nếu thấy trang gói mà
    không bấm được nút Free."""
    deadline = time.time() + 30
    while time.time() < deadline:
        if _any_visible(page, "Continue to activation"):
            return True
        # Domain đã tạo rồi: sau khi nhập domain sẽ nhảy thẳng tới trang
        # "Update your nameservers" (không có bước chọn gói/Review) —
        # thấy nút này thì dừng đợi, để bước sau lấy NS ở đó.
        if _any_visible(page, "I updated my nameservers"):
            return True
        if _any_visible(page, "Select a plan"):
            break
        page.wait_for_timeout(500)
    else:
        # Hết 30s mà không thấy trang gói lẫn nút Continue: ghi lại
        # trạng thái trang để chẩn đoán (đang ở trang nào? bấm
        # Continue nhập domain có chuyển trang không?), rồi để bước
        # sau tự xử lý (nó sẽ chụp màn hình + báo lỗi rõ ràng).
        try:
            log(f"    [chẩn đoán] sau 30s không thấy trang gói: url={page.url} "
                f"title={page.title()!r} "
                f"co_chu_Select_a_plan_trong_HTML={'Select a plan' in page.content()}")
        except Exception:
            pass
        return False
    if _any_visible(page, "Continue to activation"):
        return True
    for _attempt in range(2):
        btn = None
        for loc in (page.get_by_role("button", name="Select plan"),
                    page.get_by_role("link", name="Select plan"),
                    page.get_by_text("Select plan", exact=True)):
            try:
                for el in loc.all():
                    try:
                        if el.is_visible() and _is_free_card_button(el):
                            btn = el
                            break
                    except Exception:
                        continue
            except Exception:
                continue
            if btn:
                break
        if btn:
            try:
                btn.click(timeout=5000)
            except Exception:
                pass
        else:
            # Không tìm được nút bằng Playwright — thử JavaScript.
            try:
                clicked = page.evaluate("""() => {
                  const isFree = b => {
                    let a = b;
                    while (a) {
                      const t = a.textContent || '';
                      if (t.includes('$0')) return true;
                      if (t.includes('Pro') || t.includes('Business') || t.includes('Enterprise')) return false;
                      a = a.parentElement;
                    }
                    return false;
                  };
                  const btns = [...document.querySelectorAll('button, a, [role=button]')]
                    .filter(b => (b.textContent || '').trim() === 'Select plan' && b.offsetParent !== null && isFree(b));
                  if (!btns.length) return false;
                  btns[0].click();
                  return true;
                }""")
                if not clicked:
                    break
            except Exception:
                break
        page.wait_for_timeout(3000)
        if _any_visible(page, "Continue to activation") or not _any_visible(page, "Select a plan"):
            log("    đã chọn gói Free (Select plan).")
            return True
    shot(page, domain, "cf-no-free-plan")
    raise RuntimeError("không bấm được nút 'Select plan' của gói Free ở Cloudflare (đang đứng ở trang chọn gói)")


def _disable_bot_preference_sync(page):
    """Tắt 'Enable Bot Preference Sync' nếu đang BẬT (user yêu cầu:
    lúc thêm domain thì tắt cái này). Nút là toggle switch trên trang
    Review DNS records (và có thể ở trang Update nameservers) — tìm
    theo chữ rồi bấm công tắc kề bên, kiểm tra lại đã tắt chưa.
    Best-effort: không thấy nút / không đọc được trạng thái thì thôi
    (có zone/flow không hiện nó), không làm fail domain."""
    try:
        res = page.evaluate("""async () => {
          const isVis = e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 &&
              getComputedStyle(e).visibility !== 'hidden';
          };
          const findSwitch = () => {
            const labels = [...document.querySelectorAll('*')].filter(
              e => e.children.length === 0 && isVis(e) &&
                (e.textContent || '').trim() === 'Enable Bot Preference Sync');
            for (const label of labels) {
              let container = label;
              for (let i = 0; i < 6 && container; i++) {
                const sw = container.querySelector('[role="switch"], input[type="checkbox"], button[aria-checked], [aria-checked]');
                if (sw) return sw;
                container = container.parentElement;
              }
            }
            return null;
          };
          const stateOf = sw => {
            if (!sw) return 'not-found';
            if (sw.getAttribute('aria-checked') === 'true' || sw.checked === true) return 'on';
            if (sw.getAttribute('aria-checked') === 'false' || sw.checked === false) return 'off';
            const ds = (sw.getAttribute('data-state') || sw.getAttribute('data-checked') || '').toLowerCase();
            if (ds === 'checked' || ds === 'on' || ds === 'true') return 'on';
            if (ds === 'unchecked' || ds === 'off' || ds === 'false') return 'off';
            return 'unknown';
          };
          let sw = findSwitch();
          if (!sw) return 'not-found';
          if (stateOf(sw) === 'off') return 'already-off';
          if (stateOf(sw) === 'unknown') return 'unknown';
          sw.click();
          await new Promise(r => setTimeout(r, 800));
          sw = findSwitch();
          return stateOf(sw) === 'off' ? 'turned-off' : 'still-on';
        }""")
        if res == 'turned-off':
            log("    đã tắt 'Enable Bot Preference Sync'.")
            return True
        if res == 'already-off':
            log("    'Enable Bot Preference Sync' đã tắt sẵn.")
            return True
        if res in ('not-found', 'no-toggle'):
            return False
        log(f"    (không tắt được 'Enable Bot Preference Sync': trạng thái {res} — kiểm tra tay sau.)")
        return False
    except Exception:
        return False


# Cloudflare account ID do user nhap o app (o nhap rieng, luu lai) —
# uu tien hon tu dong do tu URL dash (user dan 2026-11-19: them 1 o
# nhap account-id, domain trong link thi thay theo con dang set).
CF_ACCOUNT_ID = None


def _cloudflare_account_id(page):
    """Account ID Cloudflare (32 ký tự hex) — cố định theo tài khoản.
    Uu tien gia tri user nhap o app (--account-id); khong co thi lay
    từ URL dash hiện tại (sau khi đăng nhập, dash tự redirect về
    /<account-id>). Trả về None nếu không lấy được."""
    if CF_ACCOUNT_ID and re.fullmatch(r"[0-9a-f]{32}", CF_ACCOUNT_ID.strip().lower()):
        return CF_ACCOUNT_ID.strip().lower()
    try:
        m = re.search(r"dash\.cloudflare\.com/([0-9a-f]{32})", page.url or "")
        if m:
            return m.group(1)
        # Chưa ở URL có account ID: mở dash home để nó redirect rồi đọc lại.
        page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        m = re.search(r"dash\.cloudflare\.com/([0-9a-f]{32})", page.url or "")
        return m.group(1) if m else None
    except Exception:
        return None


def _cloudflare_add_site_url(page):
    """Link thẳng trang Connect your domain của Cloudflare:
    https://dash.cloudflare.com/<account-id>/add-site?type=onboard.
    Trả về None nếu không lấy được account ID -> dùng cách bấm cũ."""
    acct = _cloudflare_account_id(page)
    return f"https://dash.cloudflare.com/{acct}/add-site?type=onboard" if acct else None


def _retry_add_site_via_link(page, domain):
    """Thử lại bằng link add-site thẳng khi bước Review hết 30s vẫn không
    thấy nút 'Continue to activation' (user dặn 2026-11-19): vào trang
    Connect your domain, nhập domain, Continue — nếu domain đã set thì
    nhảy thẳng tới trang Update nameservers -> lấy 2 NS; nếu lần này
    vào được trang Review thì bấm Continue to activation rồi lấy NS.
    Trả về list NS hoặc None."""
    try:
        add_url = _cloudflare_add_site_url(page)
        if not add_url:
            return None
        safe_goto(page, add_url, "trang Connect your domain")
        page.wait_for_timeout(2000)
        box = _first_visible_now(page.locator("input[placeholder*='example.com']"))
        if not box:
            box = _first_visible_now(page.locator("input[placeholder*='domain']"))
        if not box:
            return None
        box.click()
        box.fill(domain)
        _disable_bot_preference_sync(page)
        _click_visible(page, ["Continue"], timeout=8000)
        page.wait_for_timeout(3000)
        ns_active = _handle_already_active(page, domain)
        if ns_active:
            return ns_active
        if _any_visible(page, "I updated my nameservers"):
            ns = extract_ns(page)
            if len(ns) == 2:
                log(f"    domain đã set rồi, lấy NS ở trang Update nameservers: {ns[0]}, {ns[1]}")
                return ns
            return None
        if _click_visible(page, ["Continue to activation"], timeout=15000):
            page.wait_for_timeout(3000)
            ns = extract_ns(page)
            if len(ns) == 2:
                return ns
        return None
    except Exception:
        return None


def _handle_already_active(page, domain):
    """Popup 'X is already active on Cloudflare' (hien khi nhap domain
    da co zone roi roi bam Continue): nghia la domain da setup — bam
    'Manage <domain>' de vao trang quan ly roi lay 2 NS. Khong co popup
    thi tra None ngay (khong lam cham flow binh thuong). Tra ve list 2
    NS hoac None."""
    try:
        if not (_any_visible(page, "is already active on Cloudflare") or
                _any_visible(page, "has been added to this Cloudflare account already")):
            return None
        log("    domain đã active sẵn trên Cloudflare (popup already active) — mở trang quản lý để lấy NS.")
        if click_text(page, [f"Manage {domain}"], timeout=8000):
            page.wait_for_timeout(3000)
            ns = extract_ns(page)
            if len(ns) == 2:
                return ns
        # Nut Manage khong bam duoc / trang quan ly khong hien NS: thu
        # mo domain tu dash home roi lay NS (cach exists-check cu).
        page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        try:
            link = page.get_by_role("link", name=re.compile(rf"^{re.escape(domain)}$", re.I)).first
            if link.is_visible(timeout=5000):
                link.click()
                page.wait_for_timeout(2500)
                ns = extract_ns(page)
                if len(ns) == 2:
                    return ns
        except Exception:
            pass
        return None
    except Exception:
        return None


def cloudflare_add_site(page, domain, dry_run=False):
    """Add site ở Cloudflare, trả về (nameservers, status)."""
    log(f"  Cloudflare: thêm {domain}")
    page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # Domain đã có sẵn? Thử bấm vào tên domain ở trang chủ.
    try:
        link = page.get_by_role("link", name=re.compile(rf"^{re.escape(domain)}$", re.I)).first
        if link.is_visible(timeout=3000):
            log("    domain đã có sẵn trong Cloudflare, mở trang quản lý.")
            link.click()
            page.wait_for_timeout(2500)
            ns = extract_ns(page)
            if len(ns) == 2:
                return ns, "exists"
    except Exception:
        pass

    if dry_run:
        log("    [dry-run] sẽ vào thẳng link add-site -> nhập domain -> chọn Free -> lấy 2 NS — không click thật.")
        return [], "dry-run"

    # Cách chính (user yêu cầu 2026-11-19): vào thẳng trang Connect your
    # domain bằng link (khỏi bấm Domains -> Overview -> Add domain vì
    # giao diện Cloudflare hay đổi nên bấm hay trượt). Không vào được
    # thì fallback sang cách bấm cũ.
    opened = False
    add_url = _cloudflare_add_site_url(page)
    if add_url:
        try:
            safe_goto(page, add_url, "trang Connect your domain")
            page.wait_for_timeout(2000)
            if _first_visible_now(page.locator("input[placeholder*='example.com']")) or \
                    _any_visible(page, "Connect your domain"):
                log("    đã mở trang Connect your domain (link thẳng).")
                opened = True
            else:
                log("    link thẳng không mở được trang Connect your domain — thử cách bấm.")
        except Exception:
            log("    link thẳng không mở được trang Connect your domain — thử cách bấm.")
    if not opened:
        # Luồng Cloudflare cũ (theo ảnh user gửi 2026-11-18):
        #   Domains -> Overview (anh 1) -> Add domain (anh 2)
        #   -> Connect a domain (anh 3) -> nhập domain (anh 4) -> Continue
        #   -> chọn gói Free (anh 5) -> Review DNS records -> Continue to
        #   activation (anh 6) -> trang Update nameservers, lấy 2 NS.
        try:
            dom_nav = _first_visible_now(page.get_by_role("link", name="Domains"))
            if dom_nav:
                dom_nav.click(timeout=3000)
                page.wait_for_timeout(1200)
        except Exception:
            pass
        # Vào Domains Overview (nếu bấm Domains đã tới thẳng đó thì bước này
        # chỉ bấm lại link Overview ở sidebar, không sao).
        _click_visible(page, ["Overview"], timeout=5000)
        page.wait_for_timeout(1500)
        # 1. Add domain
        if not _click_visible(page, ["Add domain"], timeout=8000):
            shot(page, domain, "cf-no-add-domain")
            raise RuntimeError("không tìm được nút Add domain ở Cloudflare (trang Domains Overview)")
        page.wait_for_timeout(2000)
        # 2. Connect a domain (không phải Transfer/Buy a domain)
        if not _click_visible(page, ["Connect a domain"], timeout=8000):
            shot(page, domain, "cf-no-connect")
            raise RuntimeError("không tìm được 'Connect a domain' ở trang Add a site của Cloudflare")
        page.wait_for_timeout(2000)
    # 3. Nhập domain (ô "Domain name", placeholder example.com) -> Continue
    filled = False
    deadline = time.time() + 10
    while time.time() < deadline and not filled:
        for sel in ["input[placeholder*='example.com']", "input[placeholder*='domain']"]:
            box = _first_visible_now(page.locator(sel))
            if box:
                try:
                    box.click()
                    box.fill(domain)
                    filled = True
                    break
                except Exception:
                    continue
        if not filled:
            page.wait_for_timeout(500)
    if not filled:
        shot(page, domain, "cf-add-no-input")
        raise RuntimeError("không tìm được ô nhập domain ở Cloudflare (trang Connect your domain)")
    page.wait_for_timeout(800)
    # Tắt 'Enable Bot Preference Sync' nếu đang bật — nút nằm ngay trên
    # trang nhập domain này (ảnh user gửi 2026-11-19: toggle màu xanh
    # dưới mục Additional configuration), tắt TRƯỚC khi bấm Continue.
    _disable_bot_preference_sync(page)
    if not _click_visible(page, ["Continue"], timeout=8000):
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
    page.wait_for_timeout(3000)

    # Domain da active san: Cloudflare hien popup 'X is already active
    # on Cloudflare' thay vi trang chon goi — bam 'Manage <domain>' lay
    # NS roi ve (khong dung o day; loi chay 2026-11-19 10:40).
    ns_active = _handle_already_active(page, domain)
    if ns_active:
        log(f"    domain đã active sẵn, lấy NS ở trang quản lý: {ns_active[0]}, {ns_active[1]}")
        return ns_active, "exists"

    # 4. Chọn gói Free (nếu trang gói hiện ra) — xem _select_free_plan:
    # phải ĐỢI trang gói load xong rồi mới bấm, và kiểm tra trang đã
    # rời khỏi trang gói chưa (không bấm chữ "Free", đó chỉ là tiêu đề).
    _select_free_plan(page, domain)

    # 4b. Domain đã tạo rồi: nhập domain xong sẽ nhảy thẳng tới trang
    # "Update your nameservers" (không có bước chọn gói/Review) — lấy
    # 2 NS ở đây rồi về, khỏi làm tiếp (user dặn: copy NS rồi qua
    # Namecheap đổi, xong quay lại bấm "I updated my nameservers").
    if _any_visible(page, "I updated my nameservers"):
        ns = extract_ns(page)
        if len(ns) == 2:
            log(f"    domain đã tạo rồi, lấy NS ở trang Update nameservers: {ns[0]}, {ns[1]}")
            return ns, "exists"

    # 5. Trang Review DNS records -> Continue to activation.
    # Sau khi chọn gói Free, trang Review hay bị lag (user báo: nút
    # chưa hiện mà tool đã bỏ qua) nên đợi tới 30s thay vì 10s.
    # (Bot Preference Sync đã tắt ở bước nhập domain; giữ lại lệnh tắt
    # ở đây phòng khi có flow hiện nút ở trang Review.)
    _disable_bot_preference_sync(page)
    log("    đang đợi trang Review load xong (tối đa 30s)...")
    if not _click_visible(page, ["Continue to activation"], timeout=30000):
        # Hết 30s vẫn không thấy nút: có thể zone đã tạo rồi nhưng trang
        # Review lag/kẹt — thử chạy lại bằng link add-site thẳng (user
        # dặn): nhập domain vào, nếu domain đã set thì nó nhảy thẳng tới
        # trang Update nameservers -> lấy NS ở đó.
        log("    không thấy 'Continue to activation' sau 30s — thử chạy lại bằng link add-site.")
        ns2 = _retry_add_site_via_link(page, domain)
        if ns2:
            return ns2, "exists"
        # Có thể đã ở thẳng trang nameservers (không có bước review)
        if len(extract_ns(page)) < 2:
            shot(page, domain, "cf-no-continue-activation")
            raise RuntimeError("không tìm được nút 'Continue to activation' ở Cloudflare")
    page.wait_for_timeout(3000)

    ns = extract_ns(page)
    # Chưa thấy NS thì thử mở trang Overview của domain
    if len(ns) < 2:
        try:
            page.get_by_role("link", name=re.compile(rf"^{re.escape(domain)}$", re.I)).first.click(timeout=5000)
            page.wait_for_timeout(3000)
            ns = extract_ns(page)
        except Exception:
            pass
    if len(ns) < 2:
        shot(page, domain, "cf-add-no-ns")
        raise RuntimeError(f"không lấy được 2 nameserver (chỉ thấy: {ns}) — xem ảnh chụp màn hình")
    log(f"    nameserver: {ns[0]}, {ns[1]}")
    return ns, "created"


def cloudflare_confirm_ns(page, domain):
    """Sau khi đã đổi NS ở Namecheap: quay lại Cloudflare bấm
    'I updated my nameservers' để Cloudflare kiểm tra ngay (ảnh user gửi
    2026-11-18: nút xanh cuối trang Update nameservers).
    Best-effort: không thấy nút thì thôi — zone sẽ tự kích hoạt khi NS lan
    truyền, không làm fail domain."""
    log(f"  Cloudflare: xác nhận đã đổi NS cho {domain}")
    try:
        page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        link = _first_visible_now(
            page.get_by_role("link", name=re.compile(rf"^{re.escape(domain)}$", re.I)))
        if link:
            try:
                link.click(timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
        # Tắt Bot Preference Sync nếu đang bật (nút có thể nằm ở trang
        # Update nameservers này thay vì trang Review).
        _disable_bot_preference_sync(page)
        if _click_visible(page, ["I updated my nameservers"], timeout=15000):
            log("    đã bấm 'I updated my nameservers'.")
            page.wait_for_timeout(2000)
            return True
        # Có thể nút nằm trong trang kích hoạt riêng: thử mở nó qua link
        # 'Update your nameservers' / 'Finish setup' rồi tìm nút lại.
        if _click_visible(page, ["Update your nameservers", "Finish setup"], timeout=5000):
            page.wait_for_timeout(2000)
            _disable_bot_preference_sync(page)
            if _click_visible(page, ["I updated my nameservers"], timeout=10000):
                log("    đã bấm 'I updated my nameservers'.")
                return True
        log("    (không thấy nút 'I updated my nameservers' — zone sẽ tự kích hoạt khi NS lan truyền.)")
    except Exception as e:
        log(f"    (không xác nhận được ở Cloudflare: {e} — không sao, zone sẽ tự kích hoạt.)")
    return False


def _delete_all_records(page, domain):
    """Xóa hết record DNS cũ ở Cloudflare trước khi thêm record mới
    (user yêu cầu 2026-11-19): tick chọn hết -> bấm 'Delete N records'
    -> popup hiện lên, gõ 'DELETE' vào ô xác nhận -> bấm Delete.
    Bảng records của Cloudflare KHÔNG phải <table> mà là div grid
    (role=row/columnheader) — checkbox là <span role='checkbox'> (ô
    Select All có aria-label='Select All'), input checkbox thật bị ẩn
    (1px, aria-hidden) nên selector table/input cũ không thấy gì (lỗi
    chạy 2026-11-19 10:06: báo 'không có record cũ' dù có 8 record).
    Không có record nào thì thôi. Xóa lỗi mà còn record thì báo lỗi
    (không thêm chồng lên record cũ)."""
    try:
        # Cho bảng records load xong (API trả về sau khi shell hiện):
        # đợi tối đa 10s để thấy dòng chữ số lượng hoặc dòng record.
        n = None
        for _ in range(20):
            try:
                body = page.content() or ""
                m = re.search(r"You have used\s+(\d+)\s+of\s+200", body)
                if m:
                    n = int(m.group(1))
                    break
            except Exception:
                pass
            try:
                if page.locator("[role='row']").count() > 1:
                    n = page.locator("[role='row']").count() - 1
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)
        if n is None:
            try:
                n = page.locator("[role='row'] [role='checkbox']").count()
            except Exception:
                n = 0
        if n == 0:
            log("    (không có record cũ để xóa.)")
            return True
        log(f"    xóa {n} record cũ trước khi thêm mới...")
        # Tick chọn hết: <span role='checkbox' aria-label='Select All'>
        # ở cột đầu tiên của hàng header (HTML thật user gửi 2026-11-19).
        sel_all = _first_visible_now(page.locator("[aria-label='Select All']"))
        if not sel_all:
            sel_all = _first_visible_now(page.locator("[role='checkbox']"))
        if not sel_all:
            raise RuntimeError("không tìm được ô chọn hết record để xóa")
        sel_all.click(timeout=3000)
        page.wait_for_timeout(1500)
        # Nút 'Delete N records' (số N thay đổi theo số record đã chọn).
        del_btn = _first_visible_now(
            page.get_by_role("button", name=re.compile(r"Delete \d+ records?", re.I)))
        if not del_btn:
            del_btn = _first_visible_now(page.get_by_text(re.compile(r"Delete \d+ records?", re.I)))
        if not del_btn:
            raise RuntimeError("không tìm được nút Delete records sau khi chọn hết")
        del_btn.click(timeout=5000)
        page.wait_for_timeout(1500)
        # Popup 'Delete DNS records': gõ 'DELETE' vào ô xác nhận rồi bấm Delete.
        scope = page
        try:
            dlg = page.get_by_role("dialog").first
            dlg.wait_for(state="visible", timeout=4000)
            scope = dlg
        except Exception:
            pass
        try:
            if scope is page:
                # Không thấy dialog: ô nhập DELETE là ô text hiện CUỐI CÙNG
                # (popup render cuối body, sau ô Search DNS Records).
                vis = []
                for e in page.locator("input[type='text'], input:not([type])").all():
                    try:
                        if e.is_visible():
                            vis.append(e)
                    except Exception:
                        continue
                inp = vis[-1] if vis else None
            else:
                inp = scope.locator("input").first
            if inp is None:
                raise RuntimeError("không thấy ô nhập")
            inp.wait_for(state="visible", timeout=3000)
            inp.click()
            inp.fill("DELETE")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"không tìm được ô nhập DELETE: {e}")
        try:
            scope.get_by_role("button", name="Delete", exact=True).first.click(timeout=3000)
        except Exception:
            if not _click_visible(page, ["Delete"], timeout=3000):
                raise RuntimeError("không tìm được nút Delete trong popup xác nhận")
        # Đợi bảng trống (hết record) tối đa 15s: dòng chữ số lượng về
        # 0, hoặc chỉ còn hàng header (role=row == 1).
        for _ in range(30):
            try:
                body = page.content() or ""
                m = re.search(r"You have used\s+(\d+)\s+of\s+200", body)
                if m and int(m.group(1)) == 0:
                    log("    đã xóa hết record cũ.")
                    return True
            except Exception:
                pass
            try:
                if page.locator("[role='row']").count() <= 1:
                    log("    đã xóa hết record cũ.")
                    return True
            except Exception:
                pass
            page.wait_for_timeout(500)
        raise RuntimeError("xóa record cũ chưa xong (vẫn còn record) — không thêm mới để tránh trùng")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"không xóa được record cũ: {e}")


def cloudflare_add_records(page, domain, records, dry_run=False):
    """Thêm DNS record ở Cloudflare DNS > Records."""
    if not records:
        return "no-records"
    log(f"  Cloudflare: thêm {len(records)} record cho {domain}")
    # Vào thẳng trang DNS records (link user gửi 2026-11-19):
    # https://dash.cloudflare.com/<account-id>/<domain>/dns/records
    # Vao thang trang DNS records bang link (user dan 2026-11-19):
    # https://dash.cloudflare.com/<account-id>/<domain>/dns/records
    # (account-id lay tu o nhap trong app, domain thay theo con dang
    # set). Link dung va da dang nhap la vao duoc (user bam tay van vo);
    # tab moi mo con lanh nen trang records load cham — poll toi 10s
    # thay vi check 1 lan sau 2.5s (loi chay 2026-11-19 10:21).
    # TAM THOI BO QUA cach bam tay (dash home -> link domain -> DNS ->
    # Records) theo user 2026-11-19 — thao tac tay buoc 1 Phase 2 dang
    # sai; chi dung link thang, thu lai 1 lan roi bao loi neu van truot.
    on_records = False
    acct = _cloudflare_account_id(page)
    if not acct:
        raise RuntimeError("không lấy được Cloudflare account ID — nhập account ID vào ô trong app rồi chạy lại")
    for attempt in range(2):
        try:
            safe_goto(page, f"https://dash.cloudflare.com/{acct}/{domain}/dns/records", "trang DNS records")
            for _ in range(20):
                if _any_visible(page, "Add record") or _any_visible(page, "DNS records for"):
                    on_records = True
                    break
                page.wait_for_timeout(500)
            if on_records:
                log("    đã mở trang DNS records (link thẳng).")
                break
            log(f"    [chẩn đoán] link thẳng chưa thấy 'Add record' (lần {attempt + 1}): url={page.url} title={page.title()!r}")
        except Exception:
            pass
        if attempt == 0:
            log("    link thẳng chưa load xong — thử lại 1 lần.")
    if not on_records:
        raise RuntimeError("không mở được trang DNS records trong Cloudflare (link thẳng)")
        # Vào DNS > Records
        if not click_text(page, ["Records"]):
            click_text(page, ["DNS"])
            page.wait_for_timeout(1500)
            click_text(page, ["Records"])
        page.wait_for_timeout(2000)
    # Xóa hết record cũ trước khi thêm mới (user yêu cầu 2026-11-19).
    # Dry-run thì không xóa thật.
    if dry_run:
        log("    [dry-run] sẽ xóa hết record cũ rồi thêm mới — không xóa thật.")
    else:
        _delete_all_records(page, domain)

    done = 0
    for rec in records:
        rtype, name, content = rec["type"], rec["name"], rec["content"]
        proxied = rec.get("proxied", True)
        log(f"    + {rtype} {name} -> {content} (proxied={proxied})")
        if dry_run:
            done += 1
            continue
        if not click_text(page, ["Add record", "Add Record"]):
            shot(page, domain, "cf-dns-no-add")
            raise RuntimeError("không tìm được nút Add record")
        page.wait_for_timeout(1200)
        # Type: chọn đúng loại record. Form Cloudflare dùng dropdown tự
        # làm (không phải <select>) nên thử nhiều cách; nếu loại hiện
        # tại đã đúng thì khỏi bấm (bấm vào chỉ mở menu ra, không chọn).
        type_done = False
        try:
            combo = page.locator("select").last
            if combo.is_visible(timeout=3000):
                combo.select_option(rtype)
                type_done = True
        except Exception:
            pass
        if not type_done:
            try:
                ctrl = page.get_by_label(re.compile(r"^Type$", re.I)).first
                if ctrl.is_visible(timeout=2000):
                    cur = ""
                    try:
                        cur = (ctrl.inner_text() or "").strip().upper()
                    except Exception:
                        pass
                    if cur != rtype:
                        ctrl.click(timeout=3000)
                        page.wait_for_timeout(800)
                        opt = page.get_by_role("option", name=rtype, exact=True).first
                        if opt.is_visible(timeout=2000):
                            opt.click(timeout=3000)
                        else:
                            _click_visible(page, [rtype], timeout=3000)
                    type_done = True
            except Exception:
                pass
        if not type_done:
            # Cuối cùng: bấm chữ loại (cách cũ).
            click_text(page, [rtype])
        # Name + Content: 2 ô text trong form Add record
        try:
            inputs = page.locator("form input[type='text'], form input:not([type])")
            # Ô Name thường có label Name, ô Content/Target có label IPv4 address / Target / Content
            name_box = page.get_by_label(re.compile(r"^(Name|Host)", re.I)).first
            if name_box.is_visible(timeout=3000):
                name_box.click()
                # Form Cloudflare bắt buộc ô Name có giá trị: '@' là root
                # (placeholder 'Use @ for root') — điền '@' thật, KHÔNG
                # để trống (để trống thì báo 'Name is required.' và khóa
                # nút Save — lỗi chạy 2026-11-19 10:06).
                name_box.fill(name)
                got = name_box.input_value()
                if got.strip() != name.strip():
                    raise RuntimeError(f"ô Name điền không dính (muốn {name!r}, được {got!r})")
            target_box = None
            for lab in ["IPv4 address", "Target", "Content", "Value", "Text"]:
                try:
                    tb = page.get_by_label(re.compile(lab, re.I)).first
                    if tb.is_visible(timeout=1500):
                        target_box = tb
                        break
                except Exception:
                    continue
            if target_box is None:
                # fallback: ô text trống cuối cùng trong form
                target_box = page.locator("form input[type='text']").last
            target_box.click(); target_box.fill(content)
        except Exception as e:
            shot(page, domain, "cf-dns-fill-fail")
            raise RuntimeError(f"không điền được record {rtype} {name}: {e}")
        # Proxy toggle (chỉ A/AAAA/CNAME mới có)
        if rtype in ("A", "AAAA", "CNAME"):
            try:
                toggle = page.get_by_label(re.compile("Proxy", re.I)).first
                if toggle.is_visible(timeout=2000):
                    is_on = toggle.is_checked() if hasattr(toggle, "is_checked") else False
                    if bool(proxied) != bool(is_on):
                        toggle.click()
            except Exception:
                pass
        # Nút Save bị khóa khi Name trống (lỗi 'Name is required.') —
        # đợi nó mở khóa tối đa 5s rồi mới bấm.
        clicked_save = False
        for _ in range(10):
            save_btn = _first_visible_now(page.get_by_role("button", name="Save", exact=True))
            if save_btn:
                try:
                    if save_btn.is_enabled():
                        save_btn.click(timeout=3000)
                        clicked_save = True
                        break
                except Exception:
                    pass
            page.wait_for_timeout(500)
        if not clicked_save and not click_text(page, ["Save"]):
            shot(page, domain, "cf-dns-no-save")
            raise RuntimeError("không tìm được nút Save khi thêm record")
        page.wait_for_timeout(2000)
        # Trùng record -> Cloudflare báo lỗi, coi như đã có, đi tiếp
        try:
            body = page.content().lower()
            if "already exists" in body or "duplicate" in body:
                log("      (record đã có sẵn, bỏ qua)")
                click_text(page, ["Cancel", "Discard"])
        except Exception:
            pass
        done += 1
    return f"{done}/{len(records)} records"


# ---------------------------------------------------------------- Namecheap
def _first_visible_now(loc, max_check=20):
    """Trả về phần tử đầu tiên đang HIỂN THỊ trong locator (kiểm tra ngay,
    không chờ). Dùng khi locator khớp nhiều phần tử mà phần tử đầu có thể
    nằm trong menu/panel đang ẨN (vd ô search mobile) — .first sẽ trúng
    phần tử ẩn và chờ mãi không hiện, dù ô thật đang hiện rõ ở chỗ khác."""
    try:
        for el in loc.all()[:max_check]:
            try:
                if el.is_visible():
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


def _find_list_search(page, timeout=15000):
    """Tìm ô Search của Domain List (ô cạnh Filters). Trang là SPA, list
    load xong ô mới hiện nên chờ tối đa timeout, mỗi vòng thử nhiều cách
    (kiểm tra ngay, không chờ từng cái):
    1. input có placeholder đúng 'Search' — ô tìm domain ở đầu trang là
       'Search for your next domain' nên không khớp cái này.
    2. placeholder đúng 'Search' (Playwright exact).
    3. textbox có tên 'Search'.
    4. bất kỳ ô nào có placeholder chứa 'search', trừ ô tìm domain ở đầu
       trang ('...next domain') — phòng khi placeholder đổi chữ."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        el = _first_visible_now(page.locator("input[placeholder='Search']"))
        if el:
            return el
        el = _first_visible_now(page.get_by_placeholder("Search", exact=True))
        if el:
            return el
        el = _first_visible_now(page.get_by_role("textbox", name="Search", exact=True))
        if el:
            return el
        try:
            for cand in page.locator("input[placeholder*='search' i]").all():
                try:
                    ph = (cand.get_attribute("placeholder") or "").lower()
                    if "next domain" in ph:
                        continue
                    if cand.is_visible():
                        return cand
                except Exception:
                    continue
        except Exception:
            pass
        page.wait_for_timeout(400)
    return None


def _search_domain_in_list(page, search, domain):
    """Gõ domain vào ô Search của Domain List rồi chờ dòng domain hiện ra.
    Trang lọc live theo từng phím gõ (keyup + debounce) nên fill() một
    lần rồi bấm Enter KHÔNG kích hoạt bộ lọc — đúng như ảnh user gửi:
    ô đã có chữ mà list vẫn hiện tất cả, không lọc. Phải gõ từng ký tự
    như người gõ thật: press_sequentially tạo đủ keydown/input/keyup
    cho từng ký tự nên bộ lọc chạy như gõ tay. Không cần Enter vì gõ
    xong list tự lọc; chỉ bấm Enter dự phòng nếu sau 12s vẫn chưa thấy."""
    search.click()
    try:
        search.fill("")
    except Exception:
        pass
    search.click()
    search.press_sequentially(domain, delay=60)
    # Cho list lọc xong (gõ xong là tự lọc, không cần Enter).
    deadline = time.time() + 12
    while time.time() < deadline:
        if _any_visible(page, domain):
            return
        page.wait_for_timeout(400)
    # Dự phòng: thử bấm Enter rồi chờ thêm.
    page.keyboard.press("Enter")
    deadline = time.time() + 10
    while time.time() < deadline:
        if _any_visible(page, domain):
            return
        page.wait_for_timeout(400)
    shot(page, domain, "nc-domain-not-found")
    raise RuntimeError(f"không tìm thấy {domain} trong Domain List của Namecheap "
                       "(đã search mà không ra — kiểm tra domain có ở Namecheap không)")


def _find_manage_button(page, domain):
    """Tìm nút MANAGE của domain trong Domain List đã lọc.
    Nút này trên trang thật KHÔNG phải <button> (get_by_role('button')
    không khớp dù nút hiện rõ trong ảnh user gửi) mà là link <a>, nên
    phải thử nhiều dạng: button → link → bất kỳ phần tử nào có chữ
    MANAGE. Ưu tiên tìm trong dòng chứa domain để không bấm nhầm
    MANAGE ở chỗ khác."""
    # 1. Trong dòng (tr) chứa tên domain.
    try:
        rows = page.locator("tr", has_text=domain)
        if rows.count() > 0:
            row = rows.first
            if row.is_visible():
                for loc in (row.get_by_role("button", name="MANAGE"),
                            row.get_by_role("link", name="MANAGE"),
                            row.get_by_text("MANAGE", exact=True)):
                    el = _first_visible_now(loc)
                    if el:
                        return el
    except Exception:
        pass
    # 2. Toàn trang (sau khi lọc chỉ còn 1 dòng nên MANAGE hiện ra
    # chính là của domain đó; _first_visible_now bỏ qua các MANAGE
    # đang ẩn trong menu khác).
    for loc in (page.get_by_role("button", name="MANAGE"),
                page.get_by_role("link", name="MANAGE"),
                page.get_by_text("MANAGE", exact=True)):
        el = _first_visible_now(loc)
        if el:
            return el
    return None


def _js_click_manage(page, domain):
    """Bấm MANAGE bằng JavaScript, không cần biết nút là thẻ gì
    (button/a/span/div) hay role gì: tìm phần tử lá có chữ đúng
    'MANAGE' đang hiển thị, ưu tiên phần tử nằm trong dòng chứa tên
    domain; nếu chỉ có đúng 1 phần tử như vậy trên trang thì bấm nó.
    Trả về tag của phần tử đã bấm, hoặc None nếu không tìm được /
    có nhiều ứng viên mà không xác định được cái nào là của domain."""
    try:
        return page.evaluate("""(domain) => {
          const isVis = e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 &&
              getComputedStyle(e).visibility !== 'hidden';
          };
          const leaves = [...document.querySelectorAll('*')].filter(
            e => e.children.length === 0 &&
              e.textContent.trim() === 'MANAGE' && isVis(e));
          if (!leaves.length) return null;
          const inRow = leaves.find(e => {
            let a = e.parentElement;
            while (a) {
              if (a.tagName === 'TR' ||
                  (a.className && /row|domain/i.test(String(a.className)))) {
                return a.textContent.includes(domain);
              }
              a = a.parentElement;
            }
            return false;
          });
          // Chi bam khi xac dinh duoc dung dong cua domain. Du phong
          // "chi co 1 nut MANAGE tren trang" chi ap dung khi KHONG co
          // dong <tr> nao cua domain (layout khong phai bang) — neu
          // dong cua domain hien ra ma trong do khong co MANAGE thi
          // khong bam nut cua domain khac.
          const hasDomainRow = [...document.querySelectorAll('tr')].some(
            tr => isVis(tr) && tr.textContent.includes(domain));
          const target = inRow ||
            (!hasDomainRow && leaves.length === 1 ? leaves[0] : null);
          if (!target) return null;
          target.click();
          return target.tagName;
        }""", domain)
    except Exception:
        return None


def _log_manage_diagnosis(page, domain):
    """Khi không bấm được MANAGE: ghi HTML thật của dòng domain vào
    log (tối đa 600 ký tự) để lần sau nhìn là biết nút là dạng nào,
    khỏi đoán mò qua ảnh chụp."""
    try:
        rows = page.locator("tr", has_text=domain)
        if rows.count() > 0:
            html = rows.first.evaluate("e => e.outerHTML.slice(0, 600)")
            log(f"    [chẩn đoán] dòng {domain}: {html}")
            return
    except Exception:
        pass
    try:
        n = page.locator("text=MANAGE").count()
        log(f"    [chẩn đoán] không có dòng <tr> chứa {domain}; "
            f"số phần tử chứa chữ MANAGE trên trang: {n}")
    except Exception:
        pass


def namecheap_open_manage(page, domain):
    """Mở trang Manage của domain ở Namecheap, trả về URL trang Manage.
    Cách chính (user yêu cầu 2026-11-19): vào thẳng link
    https://ap.www.namecheap.com/domains/domaincontrolpanel/<domain>/domain
    (thay tên domain vào trước /domain), khỏi gõ ô Search + bấm MANAGE —
    giao diện Namecheap hay đổi nên bấm hay trượt. Nếu link thẳng không
    vào được trang Manage (domain chưa có trong tài khoản / bị redirect)
    thì fallback sang cách cũ: Domain List -> Search -> MANAGE."""
    log(f"  Namecheap: tìm {domain} trong Domain List")
    # Link thẳng tới trang Manage (user gửi 2026-11-19) — kiểm tra đã
    # vào đúng trang Manage chưa (thấy mục Nameservers) rồi mới tin.
    # Trang là SPA, tab mới mở còn lạnh nên đợi tới 15s; thử lại 1 lần
    # nếu lần đầu chưa load (lỗi chạy 2026-11-19 10:21: link thẳng
    # trượt ở elvellia nhưng fallback Search+MANAGE lại vào được).
    for attempt in range(2):
        try:
            manage_url = f"https://ap.www.namecheap.com/domains/domaincontrolpanel/{domain}/domain"
            safe_goto(page, manage_url, "trang Manage")
            ok = False
            for _ in range(30):
                if _any_visible(page, "Nameserver") or _any_visible(page, "Custom DNS"):
                    ok = True
                    break
                page.wait_for_timeout(500)
            if ok:
                log(f"    đã mở trang Manage (link thẳng): {page.url}")
                return page.url
        except Exception:
            pass
        if attempt == 0:
            log("    link thẳng chưa load xong — thử lại 1 lần.")
    try:
        body = page.content() or ""
        log(f"    [chẩn đoán] link thẳng thất bại: url={page.url} title={page.title()!r} "
            f"co_chu_Nameserver_trong_HTML={'Nameserver' in body}")
    except Exception:
        pass
    log("    link thẳng không vào được trang Manage — thử cách Search + MANAGE.")
    page.goto("https://ap.www.namecheap.com/domains/list", wait_until="domcontentloaded")
    # Ô Search nằm cạnh Filters (xem _find_list_search: thử nhiều cách,
    # chọn ô đang HIỂN THỊ, không lấy .first vì .first hay trúng ô search
    # đang ẩn ở đầu trang). Nếu trang chưa load đúng, thử bấm "Domain List"
    # ở menu trái rồi tìm lại.
    search = _find_list_search(page, timeout=15000)
    if not search:
        try:
            page.get_by_role("link", name=re.compile("Domain List", re.I)).first.click(timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        search = _find_list_search(page, timeout=15000)
    if not search:
        shot(page, domain, "nc-list-no-search")
        raise RuntimeError("không thấy ô Search ở Domain List của Namecheap")
    _search_domain_in_list(page, search, domain)
    # Sau khi search chỉ còn dòng domain đó (các dòng khác đã lọc hết),
    # nút MANAGE đầu tiên HIỂN THỊ chính là của nó (không lấy .first vì
    # .first có thể trúng nút MANAGE đang ẩn trong menu khác — cùng lỗi
    # như ô Search và kiểm tra đăng nhập trước đây). _find_manage_button
    # thử cả button/link/chữ MANAGE vì nút thật là link <a>, không phải
    # <button>.
    manage = None
    deadline = time.time() + 10
    while time.time() < deadline and manage is None:
        manage = _find_manage_button(page, domain)
        if not manage:
            page.wait_for_timeout(400)
    if not manage:
        # Không tìm được bằng role/chữ — bấm trực tiếp bằng JavaScript,
        # không cần biết nút là thẻ/role gì (xem _js_click_manage).
        tag = _js_click_manage(page, domain)
        if tag:
            log(f"    đã bấm MANAGE bằng JavaScript (thẻ <{tag}>).")
            page.wait_for_timeout(3000)
        else:
            _log_manage_diagnosis(page, domain)
            shot(page, domain, "nc-no-manage")
            raise RuntimeError(f"không thấy nút MANAGE của {domain} trong Domain List")
    else:
        try:
            manage.click(timeout=5000)
        except Exception:
            log("    click thường không được, thử lại bằng JavaScript.")
        page.wait_for_timeout(3000)
        if "/domains/list" in page.url:
            tag = _js_click_manage(page, domain)
            if tag:
                log(f"    click thường không chuyển trang, đã bấm lại bằng JavaScript (thẻ <{tag}>).")
                page.wait_for_timeout(3000)
    if "/domains/list" in page.url:
        _log_manage_diagnosis(page, domain)
        shot(page, domain, "nc-no-manage")
        raise RuntimeError(f"bấm MANAGE của {domain} mà trang không chuyển khỏi Domain List")
    manage_url = page.url
    log(f"    đã mở trang Manage: {manage_url}")
    return manage_url


def _select_custom_dns(page):
    """Chọn 'Custom DNS' trong mục Nameservers của trang Manage.
    Ô này là <select> gốc (ảnh user gửi: dropdown có 3 lựa chọn
    Namecheap BasicDNS / Web Hosting DNS / Custom DNS) — option trong
    select đóng không phải chữ thường trong trang nên click theo chữ
    không bấm được, phải chọn thẳng bằng select_option. Duyệt mọi
    select trên trang, cái nào có option 'Custom DNS' thì chọn nó.
    Trả về True nếu chọn được. False = không có select nào như vậy
    (dropdown tự làm bằng div thì dùng click_text sau đó)."""
    try:
        for sel in page.locator("select").all():
            try:
                sel.select_option(label="Custom DNS", timeout=2000)
                log("    đã chọn Custom DNS trong dropdown Nameservers.")
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _click_save_ns(page):
    """Bấm nút lưu NS ở Namecheap: dấu tick xanh (✓) bên phải mục
    Nameservers (ảnh user gửi). HTML THẬT (user gửi 2026-11-19): tick
    xanh là link <a class="save">Save</a> (chữ "Save" bị ẩn, chỉ hiện
    icon tick bằng CSS) nằm trong <div class="inline-actions"> cùng với
    <a class="cancel">Cancel</a> — KHÔNG phải <button>, KHÔNG có chữ
    ✓ nào trong DOM nên mọi cách tìm nút/chữ ✓ trước đây đều trượt.
    Vì vậy bấm thẳng link class 'save' này trước tiên."""
    # Nhanh 0 (dang that): link <a class="save"> — uu tien cai nam cung
    # o voi <a class="cancel"> (cap Save/Cancel cua muc Nameservers).
    try:
        clicked = page.evaluate("""() => {
            // Chi can rong > 0 (khong can cao > 0): chu "Save" bi an
            // nen hop cua link co the suy bien (cao = 0) nhung click
            // bang JS van kich hoat duoc. display:none van loai duoc
            // vi no co rong = 0.
            const isVis = e => {
              const r = e.getBoundingClientRect();
              return r.width > 0 &&
                getComputedStyle(e).visibility !== 'hidden';
            };
            const hasCls = (e, c) => e.classList && e.classList.contains(c);
            const cancels = [...document.querySelectorAll('a, button, [role=button]')]
              .filter(e => isVis(e) && hasCls(e, 'cancel'));
            for (const c of cancels) {
              const p = c.parentElement;
              if (!p) continue;
              const s = [...p.querySelectorAll('a, button, [role=button]')]
                .find(e => hasCls(e, 'save'));
              if (s && isVis(s)) { s.click(); return true; }
            }
            const saves = [...document.querySelectorAll('a, button, [role=button]')]
              .filter(e => isVis(e) && hasCls(e, 'save'));
            if (saves.length) { saves[0].click(); return true; }
            return false;
        }""")
        if clicked:
            return True
    except Exception:
        pass
    for sel in ["button[title='Save']", "button[aria-label='Save']",
                "button[title*='Save' i]", "button[aria-label*='Save' i]",
                "button:has-text('✓')", "button:has-text('✔')",
                "button:has-text('Save')"]:
        btn = _first_visible_now(page.locator(sel))
        if btn:
            try:
                btn.click(timeout=2000)
                return True
            except Exception:
                continue
    # Quét mọi nút đang hiện: nút nào có title/aria/chữ mang nghĩa lưu
    # (Save/✓/✔) và không phải nút huỷ (×/Cancel) thì bấm nó.
    try:
        for btn in page.locator("button").all():
            try:
                if not btn.is_visible():
                    continue
                info = ((btn.get_attribute("title") or "") + " " +
                        (btn.get_attribute("aria-label") or "") + " " +
                        (btn.inner_text() or "")).strip()
                if re.search(r"save|✓|✔", info, re.I) and \
                        not re.search(r"×|✕|cancel|close", info, re.I):
                    btn.click(timeout=2000)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    # Nút tick xanh chỉ có icon (không title/aria/chữ): nó nằm cạnh nút X
    # (huỷ) trong cùng 1 ô — tìm nút X đang hiện rồi bấm nút kề trước nó.
    try:
        clicked = page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button')]
                .filter(b => b.offsetParent !== null);
            const x = btns.find(b => (b.textContent || '').includes('\\u00d7') ||
                (b.title || '').match(/cancel/i) ||
                (b.getAttribute('aria-label') || '').match(/cancel/i));
            if (!x || !x.parentElement) return false;
            const sibs = [...x.parentElement.querySelectorAll('button')]
                .filter(b => b !== x && b.offsetParent !== null);
            if (!sibs.length) return false;
            sibs[sibs.length - 1].click();
            return true;
        }""")
        if clicked:
            return True
    except Exception:
        pass
    # Nút tick xanh có thể không phải <button> (span/a/div, hoặc chỉ có
    # icon SVG không có chữ/title/aria) nên mọi cách trên đều trượt —
    # bấm trực tiếp bằng JavaScript, không cần biết thẻ gì.
    try:
        clicked = page.evaluate("""() => {
            const isVis = e => {
              const r = e.getBoundingClientRect();
              return r.width > 0 && r.height > 0 &&
                getComputedStyle(e).visibility !== 'hidden';
            };
            // Nhanh 1: phan tu SAU NHAT co chu dung '✓'/'✔' (nut tick
            // co the chua them icon nen khong phai phan tu la).
            const marks = [...document.querySelectorAll('*')].filter(
              e => isVis(e) && ['\\u2713', '\\u2714'].includes((e.textContent || '').trim()));
            if (marks.length) {
              const depth = e => { let d = 0, x = e; while (x) { d++; x = x.parentElement; } return d; };
              marks.sort((a, b) => depth(b) - depth(a));
              marks[0].click();
              return true;
            }
            // Nhanh 2: phan tu ke truoc nut X (tick xanh nam ngay ben
            // trai nut X nhu anh user gui).
            const isX = e => {
              const t = (e.textContent || '').trim();
              if (['\\u00d7', '\\u2717', '\\u2715'].includes(t)) return true;
              // Nut X that ra la <a class="cancel">Cancel</a> — chu la
              // 'Cancel' (khong phai ky tu ✗), nhan dang qua class.
              if (e.classList && e.classList.contains('cancel')) return true;
              const al = ((e.getAttribute('aria-label') || '') + ' ' + (e.title || '')).toLowerCase();
              return /cancel/.test(al);
            };
            const xs = [...document.querySelectorAll('button, a, span, i, [role=button]')]
              .filter(e => isVis(e) && isX(e));
            for (const x of xs) {
              const p = x.parentElement;
              if (!p) continue;
              const idx = [...p.children].indexOf(x);
              if (idx <= 0) continue;
              const prev = p.children[idx - 1];
              if (!isVis(prev)) continue;
              const target = prev.querySelector('button, a, [role=button]') || prev;
              target.click();
              return true;
            }
            // Nhanh 3: trong muc Nameservers, nut co class/id/aria
            // mang nghia luu (save/check/tick/ok) — phong khi tick xanh
            // chi la icon SVG, khong co chu nao de nhan dang.
            const inp = document.querySelector("input[placeholder*='Nameserver' i]");
            if (inp) {
              let sec = inp;
              for (let i = 0; i < 5 && sec.parentElement; i++) sec = sec.parentElement;
              const cands = [...sec.querySelectorAll('button, a, [role=button]')].filter(e => {
                if (!isVis(e)) return false;
                const s = ((e.className && String(e.className)) || '') + ' ' + (e.id || '') + ' ' +
                  (e.getAttribute('aria-label') || '') + ' ' + (e.title || '');
                return /save|check|tick|\\bok\\b/i.test(s);
              });
              if (cands.length) { cands[0].click(); return true; }
            }
            return false;
        }""")
        if clicked:
            return True
    except Exception:
        pass
    return click_text(page, ["Save", "✓", "✔"])


def _fill_ns_input(page, inp, ns):
    """Điền 1 NS vào ô, kiểm tra lại giá trị đã vào ô chưa; chưa thì
    gõ lại từng ký tự như người gõ thật (ô React có thể không nhận
    fill() 1 lần). Raise nếu điền mãi không vào."""
    try:
        inp.click()
        inp.fill(ns)
    except Exception:
        pass
    page.wait_for_timeout(300)
    try:
        if (inp.input_value() or "").strip() == ns:
            return
    except Exception:
        pass
    try:
        inp.click()
        inp.press_sequentially(ns, delay=30)
    except Exception:
        pass
    page.wait_for_timeout(300)
    try:
        if (inp.input_value() or "").strip() != ns:
            raise RuntimeError(f"ô nameserver vẫn trống/sai sau khi điền (muốn {ns})")
    except RuntimeError:
        raise
    except Exception:
        pass


def _ns_already_set(page):
    """Check ngay o buoc Namecheap (user dan 2026-11-19): do xuong muc
    NAMESERVERS — thay 3 dong la du: dong dau la 'Custom DNS', 2 dong
    tiep co noi dung (bat ky noi dung gi, khong fix cung ten NS nao —
    Cloudflare hay doi ten NS theo zone). Chua setup thi dropdown la
    'Namecheap BasicDNS' va khong co dong NS nao. Chi can 1 trong 2
    dieu kien thieu (vd Custom DNS nhung 2 dong sau trong) thi cung coi
    nhu chua setup -> chay Phase 1 binh thuong.
    Tra ve list 2 dong NS (noi dung nhu tren trang) hoac []."""
    js = """() => {
      // Tim select cua muc NAMESERVERS (co 'Custom DNS' trong danh sach
      // lua chon) — khong doi no dang chon san 'Custom DNS', vi trang
      // la SPA: luc moi load xong dropdown co the van dang o gia tri
      // mac dinh / chua set, check ngay luc do se ket luan sai la chua
      // setup (loi chay 2026-11-19 10:40: du dieu kien bo qua nhung van
      // qua buoc 3 them domain).
      const sel = [...document.querySelectorAll('select')].find(s =>
        [...s.options].some(o => o.textContent.trim() === 'Custom DNS'));
      if (!sel) return {mode: 'loading', ns: []};
      const cur = ((sel.options[sel.selectedIndex] || {}).textContent || '').trim();
      if (cur !== 'Custom DNS') return {mode: cur || 'loading', ns: []};
      // Lay 2 hostname trong cac dong sau 'Custom DNS' (bat ky ten NS
      // nao, khong fix cung) — cac dong chu thuong nhu 'redirect
      // domain' / 'alert' xen vao thi bo qua vi khong phai hostname.
      // Chi nhan hostname that: co dau '.' o giua (khong phai dau
      // cham cuoi cau nhu 'account.' / 'default.' trong doan chu
      // '...your Namecheap account. ...to Namecheap default.' — loi
      // chay 2026-11-19 10:21 tra ve 'account.' / 'default.'), cac
      // phan cach nhau bang '.' chi gom chu/so/gach noi, phan cuoi
      // (TLD) it nhat 2 ky tu chu.
      const isHost = t => {
        if (!t.includes('.') || t.endsWith('.')) return false;
        const parts = t.split('.');
        return parts.length >= 2 && parts.every(p => /^[a-z0-9-]+$/i.test(p)) && parts[parts.length - 1].length >= 2;
      };
      let a = sel;
      for (let i = 0; i < 6 && a.parentElement; i++) {
        a = a.parentElement;
        const lines = (a.innerText || '').split(String.fromCharCode(10)).map(x => x.trim()).filter(Boolean);
        const idx = lines.indexOf('Custom DNS');
        if (idx < 0) continue;
        const hosts = [];
        for (const line of lines.slice(idx + 1)) {
          for (const tok of line.split(' ')) {
            const v = tok.trim().toLowerCase();
            if (v && isHost(v) && !hosts.includes(v)) hosts.push(v);
          }
          if (hosts.length >= 2) break;
        }
        if (hosts.length >= 2) return {mode: 'Custom DNS', ns: hosts.slice(0, 2)};
      }
      return {mode: 'Custom DNS', ns: []};
    }"""
    # Poll toi 6s cho trang SPA render xong: dropdown co the chua set
    # gia tri, 2 dong NS co the chua hien. Chi ket luan 'chua setup'
    # ngay khi dropdown da hien ro 'Namecheap BasicDNS'; con lai (dang
    # load / Custom DNS nhung chua thay dong NS) thi doi tiep.
    try:
        for _ in range(12):
            res = page.evaluate(js) or {}
            if res.get("ns"):
                return [str(x).lower() for x in res["ns"]]
            if res.get("mode") == "Namecheap BasicDNS":
                return []
            page.wait_for_timeout(500)
        return []
    except Exception:
        return []


def namecheap_set_ns(page, domain, ns_list, dry_run=False, manage_url=None):
    log(f"  Namecheap: đổi NS cho {domain} -> {', '.join(ns_list)}")
    # Quay lại trang Manage đã mở ở bước 1; nếu không còn ở đó thì mở lại
    # bằng cách search trong Domain List (đúng luồng user chỉ).
    on_manage = False
    if manage_url:
        page.goto(manage_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        on_manage = "nameserver" in page.content().lower()
    if not on_manage:
        manage_url = namecheap_open_manage(page, domain)
        page.wait_for_timeout(2000)
        if "nameserver" not in page.content().lower():
            shot(page, domain, "nc-no-nameserver")
            raise RuntimeError("đã mở trang Manage nhưng không thấy mục Nameservers")
    if dry_run:
        log("    [dry-run] sẽ chọn Custom DNS, dán 2 NS, bấm dấu tick xanh — không lưu thật.")
        return "dry-run"
    # Chọn Custom DNS trong mục Nameservers (ảnh user gửi: dropdown select
    # có 3 lựa chọn) — thử select gốc trước, dropdown tự làm thì click chữ.
    if not _select_custom_dns(page) and not click_text(page, ["Custom DNS"]):
        shot(page, domain, "nc-no-customdns")
        raise RuntimeError("không tìm được Custom DNS ở mục Nameservers")
    page.wait_for_timeout(1200)
    # Điền 2 NS vào các ô trong mục Nameservers.
    try:
        # Ưu tiên ô có placeholder 'Nameserver 1/2' — đúng ô cần điền.
        # (Bản cũ quét mọi input[type='text'] nên có thể trúng ô khác
        # trong trang, điền xong mà 2 ô NS vẫn trống như ảnh user gửi.)
        targets = page.locator("input[placeholder*='nameserver' i]").all()[:2]
        if len(targets) < 2:
            inputs = page.locator("input[type='text']")
            targets = []
            for i in range(inputs.count()):
                try:
                    v = (inputs.nth(i).input_value() or "").strip()
                    ph = (inputs.nth(i).get_attribute("placeholder") or "").lower()
                    if "nameserver" in ph or v.endswith(".ns.cloudflare.com") or \
                       v.startswith("ns") or (not v and "dns" in ph):
                        targets.append(inputs.nth(i))
                except Exception:
                    continue
            targets = targets[:2]
        if len(targets) < 2:
            # Thử bấm "Add Nameserver" để thêm ô
            click_text(page, ["Add Nameserver"])
            page.wait_for_timeout(800)
            targets = page.locator("input[placeholder*='nameserver' i]").all()[:2]
        if len(targets) < 2:
            shot(page, domain, "nc-ns-inputs")
            raise RuntimeError(f"không tìm đủ 2 ô nhập nameserver (thấy {len(targets)} ô)")
        for inp, ns in zip(targets, ns_list):
            _fill_ns_input(page, inp, ns)
    except RuntimeError:
        raise
    except Exception as e:
        shot(page, domain, "nc-ns-fill-fail")
        raise RuntimeError(f"không điền được nameserver: {e}")
    # Lưu: dấu tick xanh (✓) bên phải mục Nameservers (ảnh user gửi).
    if not _click_save_ns(page):
        # Ghi HTML thật của mục Nameservers vào log để lần sau nhìn là
        # biết nút lưu là dạng nào (thẻ gì, class gì, có chữ/icon gì),
        # khỏi đoán mò qua ảnh chụp.
        try:
            sec = page.evaluate("""() => {
              const inp = document.querySelector("input[placeholder*='Nameserver' i]");
              if (!inp) return 'khong thay o Nameserver';
              let a = inp;
              for (let i = 0; i < 4 && a.parentElement; i++) a = a.parentElement;
              return (a.outerHTML || '').slice(0, 900);
            }""")
            log(f"    [chẩn đoán] HTML mục Nameservers: {sec}")
        except Exception:
            pass
        shot(page, domain, "nc-no-save")
        raise RuntimeError("không tìm được nút lưu (dấu tick xanh) ở Namecheap")
    page.wait_for_timeout(2500)
    # Kiểm tra lại bằng cách tải lại trang Manage: chỉ khi lưu thật thì
    # 2 NS mới còn hiện (tải lại sẽ hiện NS đã lưu trên server, không
    # phải giá trị vừa gõ còn nằm trong ô chưa lưu). Trang Manage là
    # Angular SPA nên sau khi tải lại phải ĐỢI nó render xong (poll tới
    # 20s). Check theo CHỮ HIỂN THỊ THẬT (innerText) chứ không chỉ
    # HTML thô (page.content) — lần chạy 2026-11-19 10:21 cho thấy NS
    # đã hiện rõ trên màn hình (ảnh chụp) mà check HTML thô vẫn trượt.
    safe_goto(page, manage_url, "trang Manage")
    found = False
    for _ in range(40):
        try:
            text = page.inner_text("body") or ""
        except Exception:
            text = ""
        if ns_list[0] in text and ns_list[1] in text:
            found = True
            break
        try:
            body = page.content()
            if ns_list[0] in body and ns_list[1] in body:
                found = True
                break
        except Exception:
            pass
        page.wait_for_timeout(500)
    if not found:
        try:
            text = page.inner_text("body") or ""
            i = text.lower().find("nameserver")
            log(f"    [chẩn đoán] text quanh NAMESERVERS: {text[max(0, i - 50):i + 300]!r}")
        except Exception:
            pass
        shot(page, domain, "nc-save-not-verified")
        raise RuntimeError("bấm lưu rồi nhưng tải lại trang Manage không thấy 2 NS mới — có thể chưa lưu được")
    log("    đã lưu NS ở Namecheap.")
    return "saved"


# ---------------------------------------------------------------- records input
def load_presets():
    try:
        import yaml
        p = BASE_DIR / "presets.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {"shopify": {"records": [{"type": "A", "name": "@", "content": "23.227.38.65", "proxied": True},
                                    {"type": "CNAME", "name": "www", "content": "shops.myshopify.com", "proxied": True}]},
            "vps": {"records": []}, "vercel": {"records": []}, "only-ns": {"records": []}}


def parse_records_file(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data  # {domain: [records]} hoặc {"records": [...]}
    if isinstance(data, list):
        return {"__shared__": data}
    return {}


def parse_record_arg(s):
    parts = s.split(":")
    if len(parts) < 3:
        raise ValueError(f"--record sai định dạng (cần Type:Name:Content): {s}")
    rtype, name = parts[0].upper(), parts[1]
    content = ":".join(parts[2:])
    proxied = True
    if content.endswith(":proxied=false"):
        content = content[: -len(":proxied=false")]; proxied = False
    return {"type": rtype, "name": name, "content": content, "proxied": proxied}


def records_for_domain(domain, args, presets, file_map):
    if domain in file_map and isinstance(file_map[domain], list):
        return [r for r in file_map[domain] if str(r.get("content", "")).strip()]
    if "__shared__" in file_map:
        return [r for r in file_map["__shared__"] if str(r.get("content", "")).strip()]
    if "records" in file_map and isinstance(file_map["records"], list):
        return [r for r in file_map["records"] if str(r.get("content", "")).strip()]
    if args.record:
        return [parse_record_arg(s) for s in args.record]
    if args.preset:
        return list(presets.get(args.preset, {}).get("records", []) or [])
    return []


# ---------------------------------------------------------------- main flow
def setup_one(nc_page, cf_page, domain, args, presets, file_map):
    """Xử lý 1 domain. 2 tab làm việc riêng: nc_page chỉ vào Namecheap,
    cf_page chỉ vào Cloudflare — đổi tab qua lại thay vì 1 tab goto qua
    lại đè link (user yêu cầu)."""
    result = {"domain": domain, "status": "", "nameservers": "", "records": "", "error": ""}
    ns_list = []
    try:
        if args.phase in ("1", "all"):
            # Luồng mới theo user: Namecheap trước (Domain List -> Search ->
            # MANAGE), rồi Cloudflare add domain lấy 2 NS, rồi quay lại
            # trang Manage ở Namecheap điền 2 NS vào Custom DNS (ảnh 2).
            manage_url = None
            ns_list = []
            ns_skipped = False
            if not args.only_cloudflare:
                _show(nc_page)
                manage_url = namecheap_open_manage(nc_page, domain)
                # Check ngay o buoc Namecheap (user dan 2026-11-19): do
                # xuong muc NAMESERVERS — thay 3 dong la du: dong dau la
                # 'Custom DNS', 2 dong tiep co noi dung (bat ky noi dung
                # gi) thi da setup roi -> nhay thang Phase 2 (set record),
                # khoi chay Phase 1. Chua setup thi dropdown la
                # 'Namecheap BasicDNS' va khong co dong NS.
                ns_already = _ns_already_set(nc_page)
                if ns_already:
                    log(f"  Namecheap: NS đã setup sẵn ({', '.join(ns_already)}) — bỏ qua Phase 1, đi thẳng Phase 2.")
                    ns_list = ns_already
                    ns_skipped = True
                    result["nameservers"] = ", ".join(ns_list)
                    result["status"] = "dry-run-phase1" if args.dry_run else "phase1-skipped-ns-set"
            if not ns_skipped:
                _show(cf_page)
                ns_list, st = cloudflare_add_site(cf_page, domain, dry_run=args.dry_run)
                result["nameservers"] = ", ".join(ns_list)
                if args.dry_run:
                    result["status"] = "dry-run-phase1"
                    return result
                if not args.only_cloudflare:
                    _show(nc_page)
                    namecheap_set_ns(nc_page, domain, ns_list, dry_run=args.dry_run,
                                     manage_url=manage_url)
                    # Quay lại Cloudflare bấm "I updated my nameservers" để
                    # xác nhận (ảnh user gửi) — best-effort, không fail domain
                    # nếu không thấy nút.
                    _show(cf_page)
                    cloudflare_confirm_ns(cf_page, domain)
                result["status"] = "phase1-done" if args.phase == "1" else "phase1-part"
        if args.phase in ("2", "all"):
            recs = records_for_domain(domain, args, presets, file_map)
            if not recs:
                log(f"  {domain}: không có record nào (tất cả ô đều trống) — bỏ qua Phase 2.")
                result["status"] = (result["status"] or "phase2-skipped-empty")
                return result
            if args.phase == "2" and not args.only_namecheap:
                # Phase 2 cần biết NS (để verify) nhưng không đổi NS nữa:
                # lấy NS từ trang Cloudflare của domain
                try:
                    _show(cf_page)
                    cf_page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
                    cf_page.wait_for_timeout(2000)
                    cf_page.get_by_role("link", name=re.compile(rf"^{re.escape(domain)}$", re.I)).first.click(timeout=8000)
                    cf_page.wait_for_timeout(2500)
                    ns_list = extract_ns(cf_page)
                    result["nameservers"] = ", ".join(ns_list)
                except Exception:
                    pass
            _show(cf_page)
            out = cloudflare_add_records(cf_page, domain, recs, dry_run=args.dry_run)
            result["records"] = out
            result["status"] = "done" if args.phase == "all" else "phase2-done"
        # Verify NS (không chạy ở dry-run)
        if not args.dry_run and ns_list:
            time.sleep(2)
            live = doh_ns(domain)
            log(f"  verify NS (1.1.1.1): {live or '(chưa thấy — NS mới đổi cần 5 phút–24h để lan)'}")
            if live and set(live) != set(n.lower() for n in ns_list):
                result["error"] = f"NS live khác NS Cloudflare cấp: {live}"
    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        log(f"  !! {domain} LỖI: {e}")
    return result


def main():
    ap = argparse.ArgumentParser(description="Setup domain Namecheap -> Cloudflare bằng Chrome đã đăng nhập (không cần API key)")
    ap.add_argument("--domain")
    ap.add_argument("--domains-file")
    ap.add_argument("--phase", choices=["1", "2", "all"], default="1")
    ap.add_argument("--preset", choices=["only-ns", "shopify", "vps", "vercel"])
    ap.add_argument("--records-file")
    ap.add_argument("--record", action="append", help='vd: --record "A:@:23.227.38.65"')
    ap.add_argument("--target-ip", help="IP cho preset vps")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cdp-port", type=int, default=CDP_PORT)
    ap.add_argument("--profile", help="Đường dẫn profile Chrome (mặc định tự tìm)")
    ap.add_argument("--profile-dir", help="Hồ sơ Chrome cần dùng, vd 'Default' hay 'Profile 1' (mặc định: hồ sơ dùng gần nhất)")
    ap.add_argument("--account-id", help="Cloudflare account ID (32 ký tự hex) — nhập 1 lần ở app, khỏi tự dò từ URL dash")
    ap.add_argument("--chrome-path", help="Đường dẫn chrome.exe")
    ap.add_argument("--only-cloudflare", action="store_true")
    ap.add_argument("--only-namecheap", action="store_true")
    ap.add_argument("--keep-open", action="store_true", help="Chạy xong không đóng Chrome")
    ap.add_argument("--fresh-profile", action="store_true",
                    help="Xoá profile tool rồi copy lại từ đầu (đăng xuất hết / làm mới)")
    ap.add_argument("--reset-profile", action="store_true",
                    help="Chỉ xoá profile tool đã lưu (đăng xuất hết) rồi thoát, không chạy gì")
    ap.add_argument("--prepare", action="store_true",
                    help="Nút 1: chỉ mở Chrome với 2 tab đăng nhập Cloudflare + Namecheap rồi dừng")
    args = ap.parse_args()

    # Account ID Cloudflare do user nhap (o nhap rieng trong app) —
    # uu tien hon tu dong do tu URL dash.
    global CF_ACCOUNT_ID
    if args.account_id:
        CF_ACCOUNT_ID = args.account_id.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", CF_ACCOUNT_ID):
            log(f"CẢNH BÁO: account-id '{args.account_id}' không đúng dạng 32 ký tự hex — bỏ qua, tự dò từ URL dash.")
            CF_ACCOUNT_ID = None
        else:
            log(f"Cloudflare account ID (từ ô nhập): {CF_ACCOUNT_ID}")

    profile = args.profile or str(default_profile_dir())
    chrome_exe = args.chrome_path or default_chrome_exe()
    # Chọn hồ sơ Chrome: user chỉ định > hồ sơ dùng gần nhất > Default.
    # (Trước đây tool luôn copy 'Default' nên mở lộn hồ sơ nếu user đăng
    # nhập Cloudflare/Namecheap ở hồ sơ khác, vd 'Profile 1'.)
    profiles, last_used = list_chrome_profiles(profile)
    if args.profile_dir:
        profile_dir_name = args.profile_dir
    elif last_used:
        profile_dir_name = last_used
    else:
        profile_dir_name = "Default"
    log(f"Domain Setup tool — bản {tool_version()} (kiểm tra mã này khớp với bản mới nhất trên git chưa)")
    if args.reset_profile:
        dest = tool_profile_dir(profile_dir_name)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
            log(f"Đã xoá profile tool: {dest} (đăng xuất hết).")
            log("Chạy lại lần sau tool sẽ copy mới từ hồ sơ Chrome thật.")
        else:
            log(f"Chưa có profile tool nào để xoá ({dest}).")
        sys.exit(0)
    if args.prepare:
        sys.exit(prepare_login_chrome(args, profile, profile_dir_name, chrome_exe))

    domains = []
    if args.domain:
        domains = [args.domain.strip().lower()]
    elif args.domains_file:
        domains = [l.strip().lower() for l in Path(args.domains_file).read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.strip().startswith("#")]
    if not domains:
        ap.error("cần --domain hoặc --domains-file")
    if sync_playwright is None:
        print("Thiếu playwright. Chạy:  pip install playwright  &&  playwright install chromium")
        sys.exit(1)
    presets = load_presets()
    if args.preset == "vps" and args.target_ip:
        presets["vps"] = {"records": [{"type": "A", "name": "@", "content": args.target_ip, "proxied": True},
                                      {"type": "A", "name": "www", "content": args.target_ip, "proxied": True}]}
    file_map = parse_records_file(args.records_file) if args.records_file else {}

    display = dict(profiles).get(profile_dir_name, profile_dir_name)
    log(f"Profile Chrome: {profile}")
    if profiles:
        log(f"Hồ sơ dùng: {display} ({profile_dir_name}) — có {len(profiles)} hồ sơ: "
            + ", ".join(f"{n} ({d})" for d, n in profiles))
    log(f"Chrome: {chrome_exe or '(dùng channel chrome của Playwright)'}")
    log(f"Domain ({len(domains)}): {', '.join(domains)} | Phase {args.phase}"
          + (" | DRY-RUN (không đổi gì thật)" if args.dry_run else ""))

    results = []
    profile_copy = None
    with sync_playwright() as p:
        browser = None
        context = None
        cdp_mode = False
        if cdp_alive(args.cdp_port):
            log(f"Nối vào Chrome đang mở qua CDP port {args.cdp_port}...")
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.cdp_port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            cdp_mode = True
        else:
            # KHÔNG bắt user đóng Chrome nữa. Chrome thật cứ mở bình thường;
            # tool mở Chrome từ BẢN COPY profile (vẫn đăng nhập sẵn) nên không
            # bị khoá profile, không hiện about:blank rồi kẹt.
            if chrome_already_running():
                log("Chrome của bạn đang mở sẵn — không sao, tool dùng bản copy")
                log("profile nên không cần đóng Chrome.")
            # Profile tool được GIỮ LẠI giữa các lần chạy để cookie đăng nhập
            # còn nguyên — đăng nhập 1 lần, lần sau mở lên vẫn còn.
            profile_copy = get_tool_profile_dir(profile, profile_dir_name, fresh=args.fresh_profile)
            use_dir = profile_copy or profile
            if profile_copy:
                log("Chrome chưa bật cổng debug — tool sẽ mở Chrome từ profile tool đã lưu.")
            else:
                log("Không copy được profile — thử mở Chrome bằng profile thật.")
                log("(Nếu báo 'profile đang được dùng', hãy đóng hết Chrome rồi chạy lại.)")
            try:
                # chromium_sandbox=True: Playwright mặc định tắt sandbox của Chrome
                # (--no-sandbox) nên Chrome hiện cảnh báo "cờ dòng lệnh không được
                # hỗ trợ". Bật sandbox lại thì hết cảnh báo, Chrome chạy như bình thường.
                # (Đã bỏ --disable-blink-features=AutomationControlled vì Chrome
                # cũng báo cờ đó không được hỗ trợ.)
                kwargs = dict(user_data_dir=use_dir, headless=False,
                              chromium_sandbox=True,
                              # Bỏ cờ --enable-automation mặc định của Playwright:
                              # cờ đó làm Chrome hiện "đang bị điều khiển tự động" và
                              # Cloudflare nhận ra trình duyệt tự động nên bắt đăng
                              # nhập lại + xác minh danh tính. Bỏ nó thì bản copy
                              # giống Chrome thường hơn, phiên đăng nhập dễ giữ được.
                              ignore_default_args=["--enable-automation"],
                              args=["--no-first-run"],
                              viewport={"width": 1280, "height": 900},
                              ignore_https_errors=True)
                if chrome_exe:
                    kwargs["executable_path"] = chrome_exe
                else:
                    kwargs["channel"] = "chrome"
                context = p.chromium.launch_persistent_context(**kwargs)
            except Exception as e:
                log(f"LỖI mở Chrome: {e}")
                if "already in use" in str(e) or "đang được dùng" in str(e):
                    log("Profile tool đang bị 1 cửa sổ Chrome của tool giữ (lần chạy")
                    log("trước chưa đóng) — đóng cửa sổ Chrome đó rồi chạy lại.")
                    log("Nếu vẫn kẹt: --reset-profile để xoá profile tool rồi chạy lại.")
                else:
                    log("Cách sửa: chạy start-chrome.bat/.sh trước, rồi chạy tool.")
                sys.exit(2)
        # 2 tab làm việc riêng — 1 cho Namecheap, 1 cho Cloudflare (user
        # yêu cầu: mỗi site 1 tab độc lập, đổi tab qua lại thay vì 1 tab
        # goto qua lại đè link). Không chiếm tab khác của user, không chạy
        # ẩn. Chỉ dùng lại tab đầu nếu nó đang là about:blank (Chrome vừa
        # mở, chưa có gì).
        for _ in range(20):
            if context.pages:
                break
            time.sleep(0.5)
        log("Chrome đang mở %d tab: %s" % (len(context.pages),
            ", ".join(pg.url[:60] for pg in context.pages[:5]) or "(chưa có tab nào)"))
        if context.pages and context.pages[0].url == "about:blank":
            nc_page = context.pages[0]
        else:
            nc_page = context.new_page()
        cf_page = context.new_page()
        if not cdp_mode:
            # Chrome copy có thể khôi phục session cũ thành các tab trắng thừa —
            # đóng bớt cho gọn, chỉ giữ 2 tab làm việc.
            for pg in list(context.pages):
                if pg is not nc_page and pg is not cf_page and pg.url == "about:blank":
                    try:
                        pg.close()
                    except Exception:
                        pass
        log("Đã mở 2 tab làm việc riêng: tab Namecheap + tab Cloudflare (đổi tab qua lại, không đè link).")
        log("Đã nối vào Chrome, đang kiểm tra đăng nhập Cloudflare + Namecheap...")
        try:
            _show(cf_page)
            ensure_logged_in(cf_page, "Cloudflare", "https://dash.cloudflare.com/",
                             ["Account home", "Domains"])
            _show(nc_page)
            ensure_logged_in(nc_page, "Namecheap", "https://ap.www.namecheap.com/domains/list",
                             ["Domain List", "MANAGE"])
            for d in domains:
                log(f"=== {d} (phase {args.phase}) ===")
                results.append(setup_one(nc_page, cf_page, d, args, presets, file_map))
        finally:
            if not args.keep_open and not cdp_mode:
                try:
                    context.close()
                except Exception:
                    pass
                # KHÔNG xoá profile tool nữa — giữ lại để cookie đăng nhập
                # còn nguyên cho lần chạy sau (đăng nhập 1 lần là đủ).
            elif cdp_mode:
                try:
                    browser.close()  # chỉ ngắt kết nối, không đóng Chrome của bạn
                except Exception:
                    pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORTS_DIR / f"chrome_setup_{ts}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["domain", "status", "nameservers", "records", "error"])
        w.writeheader()
        w.writerows(results)
    ok = sum(1 for r in results if r["status"] not in ("failed",))
    log(f"\nXong: {ok}/{len(results)} domain OK. Báo cáo: {out}")
    for r in results:
        log(f" - {r['domain']}: {r['status']} {r.get('error','')}")


if __name__ == "__main__":
    main()
