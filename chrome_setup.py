#!/usr/bin/env python3
"""
Setup domain Namecheap -> Cloudflare BẰNG CHROME ĐÃ ĐĂNG NHẬP.
KHÔNG cần API key / token gì hết. Không cần nhập mật khẩu vào tool.

Cách nó chạy:
  1. Tool nối vào Chrome đang mở ở máy bạn (đã đăng nhập sẵn Cloudflare
     + Namecheap), qua cổng remote-debugging 9222.
     - Nếu Chrome chưa bật cổng đó, tool tự mở Chrome bằng đúng profile
       thật của bạn (nên vẫn đăng nhập sẵn) — lúc đó bạn cần ĐÓNG Chrome
       trước rồi chạy tool, tool sẽ tự mở lại.
  2. Tool tự click thay bạn, y như bạn làm tay:
     Phase 1: Cloudflare Add site (Free) -> lấy 2 nameserver
              -> Namecheap Manage domain -> Nameservers -> Custom DNS
              -> dán 2 NS -> lưu (tới bước 3)
     Phase 2: Cloudflare DNS > Records -> Add record từng ô
              (3 ô Type/Name/Content, ô nào trống thì bỏ qua)
  3. Verify NS bằng DNS 1.1.1.1 + ghi CSV vào reports/

Chạy 1 lần duy nhất ở máy bạn (xem README):
    pip install -r requirements.txt
    playwright install chromium   # chỉ cần 1 lần, để có driver Playwright
    # Cách A (khuyên dùng): đóng Chrome, rồi chạy tool — tool tự mở Chrome đã đăng nhập
    python3 chrome_setup.py --domains-file domains.txt --phase 1
    # Cách B: mở Chrome sẵn với cổng debug rồi chạy tool (xem start-chrome.bat/.sh)
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
import subprocess
import sys
import time
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


# ---------------------------------------------------------------- login check
def ensure_logged_in(page, site, start_url, login_markers):
    page.goto(start_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    body = ""
    try:
        body = page.content().lower()
    except Exception:
        pass
    url = page.url.lower()
    if any(m in url for m in ("login", "sign-in", "signin")) or \
       any(m in body for m in login_markers):
        log(f"  !! Chưa đăng nhập {site} ở Chrome này.")
        log(f"     Hãy đăng nhập {site} NGAY TRONG cửa sổ Chrome vừa mở,")
        log("     rồi quay lại đây nhấn Enter để tool chạy tiếp (chỉ cần 1 lần này).")
        try:
            input("     Nhấn Enter sau khi đã đăng nhập xong... ")
        except EOFError:
            pass
        page.goto(start_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    else:
        log(f"  đã đăng nhập {site} (Chrome sẵn).")


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
def namecheap_set_ns(page, domain, ns_list, dry_run=False):
    log(f"  Namecheap: đổi NS cho {domain} -> {', '.join(ns_list)}")
    url = f"https://ap.www.namecheap.com/domains/domaincontrolpanel/{domain}/domain"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    body = page.content().lower()
    if "nameserver" not in body:
        # Vào thẳng trang đó không được -> qua Domain List bấm Manage
        page.goto("https://ap.www.namecheap.com/domains/list", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        try:
            row = page.locator(f"text={domain}").first
            row.wait_for(state="visible", timeout=8000)
            manage = page.get_by_role("button", name=re.compile("Manage", re.I)).first
            manage.click(timeout=5000)
            page.wait_for_timeout(3000)
        except Exception:
            shot(page, domain, "nc-list-fail")
            raise RuntimeError("không mở được Domain List / Manage ở Namecheap")
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
            ns_list, st = cloudflare_add_site(page, domain, dry_run=args.dry_run)
            result["nameservers"] = ", ".join(ns_list)
            if args.dry_run:
                result["status"] = "dry-run-phase1"
                return result
            if not args.only_cloudflare:
                namecheap_set_ns(page, domain, ns_list, dry_run=args.dry_run)
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
    ap.add_argument("--chrome-path", help="Đường dẫn chrome.exe")
    ap.add_argument("--only-cloudflare", action="store_true")
    ap.add_argument("--only-namecheap", action="store_true")
    ap.add_argument("--keep-open", action="store_true", help="Chạy xong không đóng Chrome")
    args = ap.parse_args()

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

    profile = args.profile or str(default_profile_dir())
    chrome_exe = args.chrome_path or default_chrome_exe()
    log(f"Profile Chrome: {profile}")
    log(f"Chrome: {chrome_exe or '(dùng channel chrome của Playwright)'}")
    log(f"Domain ({len(domains)}): {', '.join(domains)} | Phase {args.phase}"
          + (" | DRY-RUN (không đổi gì thật)" if args.dry_run else ""))

    results = []
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
            log("Chrome chưa bật cổng debug — tool sẽ tự mở Chrome bằng profile thật của bạn.")
            log("Nếu báo lỗi 'profile đang được dùng', hãy ĐÓNG hết cửa sổ Chrome rồi chạy lại.")
            try:
                kwargs = dict(user_data_dir=profile, headless=False,
                              args=["--no-first-run", "--disable-blink-features=AutomationControlled"],
                              viewport={"width": 1280, "height": 900},
                              ignore_https_errors=True)
                if chrome_exe:
                    kwargs["executable_path"] = chrome_exe
                else:
                    kwargs["channel"] = "chrome"
                context = p.chromium.launch_persistent_context(**kwargs)
            except Exception as e:
                log(f"LỖI mở Chrome: {e}")
                log("Cách sửa: đóng hết Chrome rồi chạy lại. Hoặc chạy start-chrome.bat/.sh trước, rồi chạy tool.")
                sys.exit(2)
        page = context.new_page() if not context.pages else context.pages[0]
        try:
            ensure_logged_in(page, "Cloudflare", "https://dash.cloudflare.com/",
                             ["log in", "sign in", "enter your email"])
            ensure_logged_in(page, "Namecheap", "https://ap.www.namecheap.com/domains/list",
                             ["log in", "sign in"])
            for d in domains:
                log(f"=== {d} (phase {args.phase}) ===")
                results.append(setup_one(page, d, args, presets, file_map))
        finally:
            if not args.keep_open and not cdp_mode:
                try:
                    context.close()
                except Exception:
                    pass
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
