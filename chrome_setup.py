#!/usr/bin/env python3
"""
Setup domain Namecheap -> Cloudflare BẰNG CHROME ĐÃ ĐĂNG NHẬP.
KHÔNG cần API key / token gì hết. Không cần nhập mật khẩu vào tool.

Cách nó chạy:
  1. Tool nối vào Chrome đang mở ở máy bạn (đã đăng nhập sẵn Cloudflare
     + Namecheap), qua cổng remote-debugging 9222 — nếu bạn chạy
     start-chrome.bat/.sh trước.
     - Nếu không, tool TỰ COPY profile Chrome sang thư mục tạm rồi mở
       Chrome từ bản copy đó (vẫn đăng nhập sẵn, vì cookie mã hoá theo
       tài khoản Windows của chính máy đó). Chrome thật của bạn cứ mở
       bình thường, KHÔNG cần đóng gì hết. Chạy xong bản copy được xoá.
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
    profile_copy = prepare_profile_copy(profile, profile_dir_name)
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


def prepare_profile_copy(profile_root, profile_name="Default"):
    """Copy 1 hồ sơ Chrome thật -> thư mục tạm, đặt thành Default trong bản
    copy để Chrome mở lên dùng đúng hồ sơ đó. Trả về đường dẫn bản copy,
    hoặc None nếu không copy được (sẽ dùng profile thật như cũ).
    profile_name là tên thư mục hồ sơ, vd 'Default' hay 'Profile 1'."""
    src = Path(profile_root)
    if not src.exists():
        log(f"  Không thấy profile Chrome ở {src}")
        return None
    cleanup_stale_copies()
    dst = Path(tempfile.mkdtemp(prefix="domain-chrome-profile-"))
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
    log(f"  Đã copy hồ sơ Chrome '{profile_name}' sang thư mục tạm ({copied[0]} file)"
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


# ---------------------------------------------------------------- login check
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
            needs_login = True
            for t in logged_in_texts:
                try:
                    if page.get_by_text(t).first.is_visible(timeout=1500):
                        needs_login = False
                        break
                except Exception:
                    continue
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
            on_login_form = False
            try:
                on_login_form = page.get_by_text("Log in to your account").first.is_visible(timeout=1500) or \
                    page.get_by_role("button", name="Sign in").first.is_visible(timeout=1500)
            except Exception:
                pass
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
        log("    [dry-run] sẽ bấm Add a site, chọn Free, lấy 2 NS — không click thật.")
        return [], "dry-run"

    # Bấm Add a site / Onboard a domain
    ok = click_text(page, ["Add a site", "Add site", "Onboard a domain", "Connect a domain"])
    if not ok:
        # Thử vào thẳng trang add-site
        page.goto("https://dash.cloudflare.com/add-site", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
    # Ô nhập domain
    filled = False
    for sel in ["input[placeholder*='example.com']", "input[placeholder*='domain']",
                "input[type='text']"]:
        try:
            box = page.locator(sel).first
            box.wait_for(state="visible", timeout=5000)
            box.click()
            box.fill(domain)
            filled = True
            break
        except Exception:
            continue
    if not filled:
        shot(page, domain, "cf-add-no-input")
        raise RuntimeError("không tìm được ô nhập domain ở Cloudflare Add site")
    page.wait_for_timeout(800)
    if not click_text(page, ["Continue", "Add site", "Add Site"]):
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
    page.wait_for_timeout(4000)

    # Chọn gói Free (nếu trang gói hiện ra)
    if wait_text(page, "Free", timeout=8000):
        click_text(page, ["Free"])
        page.wait_for_timeout(1000)
        click_text(page, ["Continue"])
        page.wait_for_timeout(4000)

    # Trang review DNS scan -> Continue to activation
    click_text(page, ["Continue to activation", "Continue"])
    page.wait_for_timeout(4000)

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


def cloudflare_add_records(page, domain, records, dry_run=False):
    """Thêm DNS record ở Cloudflare DNS > Records."""
    if not records:
        return "no-records"
    log(f"  Cloudflare: thêm {len(records)} record cho {domain}")
    page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    try:
        page.get_by_role("link", name=re.compile(rf"^{re.escape(domain)}$", re.I)).first.click(timeout=8000)
        page.wait_for_timeout(2500)
    except Exception:
        raise RuntimeError("không mở được trang domain trong Cloudflare")
    # Vào DNS > Records
    if not click_text(page, ["Records"]):
        click_text(page, ["DNS"])
        page.wait_for_timeout(1500)
        click_text(page, ["Records"])
    page.wait_for_timeout(2000)

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
        try:
            # Type: dropdown/combobox đầu tiên trong form
            combo = page.locator("select").last
            if combo.is_visible(timeout=3000):
                combo.select_option(rtype)
            else:
                page.get_by_label(re.compile("Type", re.I)).first.select_option(rtype)
        except Exception:
            # Thử click theo chữ loại record trong menu
            click_text(page, [rtype])
        # Name + Content: 2 ô text trong form Add record
        try:
            inputs = page.locator("form input[type='text'], form input:not([type])")
            # Ô Name thường có label Name, ô Content/Target có label IPv4 address / Target / Content
            name_box = page.get_by_label(re.compile(r"^(Name|Host)", re.I)).first
            if name_box.is_visible(timeout=3000):
                name_box.click(); name_box.fill("" if name == "@" else name)
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
        if not click_text(page, ["Save"]):
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
def namecheap_open_manage(page, domain):
    """Mở trang Manage của domain ở Namecheap, đúng luồng user chỉ:
    ap.www.namecheap.com -> Domain List -> ô Search cạnh Filters -> gõ
    domain -> Enter -> bấm MANAGE ở dòng đó. Trả về URL trang Manage.
    (Bản cũ vào thẳng URL manage rồi fallback tìm text domain trên trang 1
    của list — list có 45 trang nên domain thường không nằm ở trang 1, và
    nút MANAGE đầu tiên bấm được có thể là của domain khác.)"""
    log(f"  Namecheap: tìm {domain} trong Domain List")
    page.goto("https://ap.www.namecheap.com/domains/list", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    # Ô Search nằm cạnh Filters (ảnh 1). Nếu trang chưa load đúng, thử bấm
    # "Domain List" ở menu trái rồi tìm lại.
    search = page.get_by_placeholder("Search").first
    try:
        search.wait_for(state="visible", timeout=8000)
    except Exception:
        try:
            page.get_by_role("link", name=re.compile("Domain List", re.I)).first.click(timeout=5000)
            page.wait_for_timeout(2500)
            search.wait_for(state="visible", timeout=8000)
        except Exception:
            shot(page, domain, "nc-list-no-search")
            raise RuntimeError("không thấy ô Search ở Domain List của Namecheap")
    search.click()
    search.fill(domain)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    try:
        page.locator(f"text={domain}").first.wait_for(state="visible", timeout=10000)
    except Exception:
        shot(page, domain, "nc-domain-not-found")
        raise RuntimeError(f"không tìm thấy {domain} trong Domain List của Namecheap "
                           "(đã search mà không ra — kiểm tra domain có ở Namecheap không)")
    # Sau khi search chỉ còn dòng domain đó (các dòng khác đã lọc hết),
    # nút MANAGE đầu tiên chính là của nó.
    manage = page.get_by_role("button", name="MANAGE").first
    manage.wait_for(state="visible", timeout=5000)
    manage.click(timeout=5000)
    page.wait_for_timeout(3000)
    manage_url = page.url
    log(f"    đã mở trang Manage: {manage_url}")
    return manage_url


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
    # Chọn Custom DNS trong mục Nameservers
    if not click_text(page, ["Custom DNS"]):
        # Dropdown Nameservers: thử chọn option Custom DNS
        try:
            sel = page.locator("select").first
            sel.wait_for(state="visible", timeout=5000)
            sel.select_option(label="Custom DNS")
        except Exception:
            shot(page, domain, "nc-no-customdns")
            raise RuntimeError("không tìm được Custom DNS ở mục Nameservers")
    page.wait_for_timeout(1200)
    # Điền 2 NS vào các ô trong mục Nameservers
    try:
        ns_inputs = page.locator("input[placeholder*='nameserver' i], input[value*='ns1' i], input[value*='ns2' i]")
        # Cách chắc hơn: lấy tất cả input text trong khu vực Nameservers, lọc ô trống/chứa ns
        inputs = page.locator("input[type='text']")
        count = inputs.count()
        targets = []
        for i in range(count):
            try:
                v = (inputs.nth(i).input_value() or "").strip()
                ph = (inputs.nth(i).get_attribute("placeholder") or "").lower()
                if "nameserver" in ph or v.endswith(".ns.cloudflare.com") or \
                   v.startswith("ns") or (not v and "dns" in ph):
                    targets.append(inputs.nth(i))
            except Exception:
                continue
        if len(targets) < 2:
            # Thử bấm "Add Nameserver" để thêm ô
            click_text(page, ["Add Nameserver"])
            page.wait_for_timeout(800)
            # lấy lại: các ô text trống ở gần cuối form Nameservers
            inputs = page.locator("input[type='text']")
            targets = [inputs.nth(i) for i in range(inputs.count())
                       if not (inputs.nth(i).input_value() or "").strip()][:2]
        if len(targets) < 2:
            shot(page, domain, "nc-ns-inputs")
            raise RuntimeError(f"không tìm đủ 2 ô nhập nameserver (thấy {len(targets)} ô)")
        targets[0].click(); targets[0].fill(ns_list[0])
        targets[1].click(); targets[1].fill(ns_list[1])
    except RuntimeError:
        raise
    except Exception as e:
        shot(page, domain, "nc-ns-fill-fail")
        raise RuntimeError(f"không điền được nameserver: {e}")
    # Lưu: dấu tick xanh (nút Save / checkmark)
    saved = False
    for sel in ["button[title='Save']", "button[aria-label='Save']",
                "button:has-text('✓')", "button:has-text('Save')"]:
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=3000)
            btn.click(timeout=3000)
            saved = True
            break
        except Exception:
            continue
    if not saved:
        saved = click_text(page, ["Save"])
    if not saved:
        shot(page, domain, "nc-no-save")
        raise RuntimeError("không tìm được nút lưu (dấu tick xanh) ở Namecheap")
    page.wait_for_timeout(2500)
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
def setup_one(page, domain, args, presets, file_map):
    result = {"domain": domain, "status": "", "nameservers": "", "records": "", "error": ""}
    ns_list = []
    try:
        if args.phase in ("1", "all"):
            # Luồng mới theo user: Namecheap trước (Domain List -> Search ->
            # MANAGE), rồi Cloudflare add domain lấy 2 NS, rồi quay lại
            # trang Manage ở Namecheap điền 2 NS vào Custom DNS (ảnh 2).
            manage_url = None
            if not args.only_cloudflare:
                manage_url = namecheap_open_manage(page, domain)
            ns_list, st = cloudflare_add_site(page, domain, dry_run=args.dry_run)
            result["nameservers"] = ", ".join(ns_list)
            if args.dry_run:
                result["status"] = "dry-run-phase1"
                return result
            if not args.only_cloudflare:
                namecheap_set_ns(page, domain, ns_list, dry_run=args.dry_run,
                                 manage_url=manage_url)
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
                    page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                    page.get_by_role("link", name=re.compile(rf"^{re.escape(domain)}$", re.I)).first.click(timeout=8000)
                    page.wait_for_timeout(2500)
                    ns_list = extract_ns(page)
                    result["nameservers"] = ", ".join(ns_list)
                except Exception:
                    pass
            out = cloudflare_add_records(page, domain, recs, dry_run=args.dry_run)
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
    ap.add_argument("--chrome-path", help="Đường dẫn chrome.exe")
    ap.add_argument("--only-cloudflare", action="store_true")
    ap.add_argument("--only-namecheap", action="store_true")
    ap.add_argument("--keep-open", action="store_true", help="Chạy xong không đóng Chrome")
    ap.add_argument("--prepare", action="store_true",
                    help="Nút 1: chỉ mở Chrome với 2 tab đăng nhập Cloudflare + Namecheap rồi dừng")
    args = ap.parse_args()

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
            profile_copy = prepare_profile_copy(profile, profile_dir_name)
            use_dir = profile_copy or profile
            if profile_copy:
                log("Chrome chưa bật cổng debug — tool sẽ mở Chrome từ bản copy profile.")
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
                log("Cách sửa: chạy start-chrome.bat/.sh trước, rồi chạy tool.")
                sys.exit(2)
        # Tab làm việc: mở tab mới RIÊNG cho tool và đưa lên trước để user
        # xem trực tiếp từng bước. Không chiếm tab web UI hay tab khác của user,
        # không chạy ẩn. Chỉ dùng lại tab đầu nếu nó đang là about:blank
        # (Chrome vừa mở, chưa có gì).
        for _ in range(20):
            if context.pages:
                break
            time.sleep(0.5)
        log("Chrome đang mở %d tab: %s" % (len(context.pages),
            ", ".join(pg.url[:60] for pg in context.pages[:5]) or "(chưa có tab nào)"))
        if context.pages and context.pages[0].url == "about:blank":
            page = context.pages[0]
        else:
            page = context.new_page()
        if not cdp_mode:
            # Chrome copy có thể khôi phục session cũ thành các tab trắng thừa —
            # đóng bớt cho gọn, chỉ giữ tab làm việc.
            for pg in list(context.pages):
                if pg is not page and pg.url == "about:blank":
                    try:
                        pg.close()
                    except Exception:
                        pass
        page.bring_to_front()
        log("Đã mở tab làm việc riêng và đưa lên trước để bạn xem trực tiếp.")
        log("Đã nối vào Chrome, đang mở Cloudflare để kiểm tra đăng nhập...")
        try:
            ensure_logged_in(page, "Cloudflare", "https://dash.cloudflare.com/",
                             ["Account home", "Domains"])
            ensure_logged_in(page, "Namecheap", "https://ap.www.namecheap.com/domains/list",
                             ["Domain List", "MANAGE"])
            for d in domains:
                log(f"=== {d} (phase {args.phase}) ===")
                results.append(setup_one(page, d, args, presets, file_map))
        finally:
            if not args.keep_open and not cdp_mode:
                try:
                    context.close()
                except Exception:
                    pass
                # Xoá bản copy profile tạm (Chrome đã đóng nên xoá được).
                if profile_copy:
                    shutil.rmtree(profile_copy, ignore_errors=True)
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
