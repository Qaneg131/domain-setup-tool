#!/usr/bin/env python3
"""
Auto setup domain: Namecheap -> Cloudflare
================================================
Luồng 2 GIAI ĐOẠN (theo yêu cầu 2026-11-18):
  GIAI ĐOẠN 1 --phase 1: nhập full domain, chạy hàng loạt tới BƯỚC 3, chưa cần record
    1. Cloudflare: tìm zone, nếu chưa có thì POST /zones để tạo -> lấy zone_id + name_servers
    2. Namecheap: namecheap.domains.dns.setCustom -> đổi NS sang NS của Cloudflare
    3. Verify NS + trạng thái zone. Xong giai đoạn 1, domain đã về Cloudflare.
  GIAI ĐOẠN 2 --phase 2: sau đó mới thêm domain + DNS record, chạy hàng loạt
    1. Cloudflare: tìm zone đã có (phải qua giai đoạn 1 trước, nếu chưa có thì báo lỗi)
    2. Tạo/cập nhật DNS records theo --records-file / --record / preset
       Mỗi domain có tối đa 3 ô record (Type/Name/Content), ô nào để trống thì BỎ QUA,
       không tạo record rỗng. Hỗ trợ A, AAAA, CNAME, TXT, MX.
    3. Verify lại.
  --phase all (mặc định cũ): làm cả 2 giai đoạn trong 1 lần chạy.

Chạy:
  # Giai đoạn 1: chỉ nhập domain, chạy hàng loạt tới bước 3
  python3 setup_domain.py --domains-file domains.txt --phase 1 --dry-run
  python3 setup_domain.py --domains-file domains.txt --phase 1
  # Giai đoạn 2: thêm record sau, 3 ô, ô trống bỏ qua
  python3 setup_domain.py --domains-file domains.txt --phase 2 --records-file custom-records.json
  python3 setup_domain.py --domain example.com --phase 2 --record "A:@:23.227.38.65" --record "CNAME:www:shops.myshopify.com"
  python3 setup_domain.py --domain example.com --preset shopify --dry-run
  python3 setup_domain.py --domain example.com --verify-only

KHÔNG commit file .env chứa key thật. Xem .env.example
API đã kiểm chứng 2026-11-18:
  - Cloudflare: POST https://api.cloudflare.com/client/v4/zones
                POST https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records
                GET  https://api.cloudflare.com/client/v4/zones?name=...
  - Namecheap:  GET https://api.namecheap.com/xml.response?Command=namecheap.domains.dns.setCustom
                (tất cả call đều là GET, trả XML, cần ClientIp đã whitelist)
"""
import argparse
import csv
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("Thiếu thư viện requests. Chạy: pip install -r requirements.txt")
    sys.exit(1)

try:
    import yaml
except ImportError:
    yaml = None  # presets.yaml là tuỳ chọn, nếu thiếu thì dùng preset mặc định trong code

BASE_DIR = Path(__file__).resolve().parent
CF_BASE = "https://api.cloudflare.com/client/v4"
NC_BASE_PROD = "https://api.namecheap.com/xml.response"
NC_BASE_SANDBOX = "https://api.sandbox.namecheap.com/xml.response"
DOH_URL = "https://cloudflare-dns.com/dns-query"

# Preset mặc định khi không có presets.yaml
DEFAULT_PRESETS = {
    "only-ns": {
        "description": "Chỉ đổi NS sang Cloudflare, không tạo record nào. Dùng khi đã có record sẵn / muốn tự thêm sau.",
        "records": [],
    },
    "shopify": {
        "description": "Shopify: A @ -> 23.227.38.65, CNAME www -> shops.myshopify.com (sửa shops.myshopify.com thành shop thật của bạn trong presets.yaml)",
        "records": [
            {"type": "A", "name": "@", "content": "23.227.38.65", "proxied": True, "ttl": 1},
            {"type": "CNAME", "name": "www", "content": "shops.myshopify.com", "proxied": True, "ttl": 1},
        ],
    },
    "vps": {
        "description": "VPS / server riêng: A @ và www -> --target-ip (bắt buộc)",
        "records": [
            {"type": "A", "name": "@", "content": "{target_ip}", "proxied": True, "ttl": 1},
            {"type": "A", "name": "www", "content": "{target_ip}", "proxied": True, "ttl": 1},
        ],
    },
    "vercel": {
        "description": "Vercel: A @ -> 76.76.21.21, CNAME www -> cname.vercel-dns.com",
        "records": [
            {"type": "A", "name": "@", "content": "76.76.21.21", "proxied": False, "ttl": 1},
            {"type": "CNAME", "name": "www", "content": "cname.vercel-dns.com", "proxied": False, "ttl": 1},
        ],
    },
}

MULTI_PART_TLDS = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.nz", "com.br"}


def log(msg, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')} {level}] {msg}", flush=True)


def load_dotenv(path=BASE_DIR / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_presets():
    p = BASE_DIR / "presets.yaml"
    if yaml and p.exists():
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            presets = data.get("presets", {})
            if presets:
                return presets
        except Exception as e:
            log(f"Không đọc được presets.yaml ({e}), dùng preset mặc định", "WARN")
    return DEFAULT_PRESETS


def load_custom_records(path, domains):
    """Đọc file custom records. Hỗ trợ 2 dạng:
    - dict theo domain: {"example.com": [{"type":"A","name":"@","content":"1.2.3.4"}, ...], ...}
    - list chung cho mọi domain: [{"type":"A",...}, ...]
    Ô nào trống (thiếu type/name/content) thì BỎ QUA, không tạo.
    Trả về dict domain -> list record đã lọc."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    if isinstance(raw, list):
        recs = [r for r in raw if r.get("type") and r.get("name") is not None and r.get("content")]
        for d in domains:
            out[d] = recs
        return out
    for d in domains:
        recs = raw.get(d) or raw.get("*") or []
        out[d] = [r for r in recs if r.get("type") and r.get("name") is not None and str(r.get("content", "")).strip()]
    return out


def parse_record_arg(s):
    """--record 'A:@:1.2.3.4' hoặc 'TXT:@:v=spf1...' hoặc 'CNAME:www:target:proxied'"""
    parts = s.split(":", 3)
    if len(parts) < 3:
        raise ValueError(f"--record sai định dạng (cần Type:Name:Content): {s}")
    rec = {"type": parts[0].upper(), "name": parts[1], "content": parts[2], "ttl": 1}
    if rec["type"] in ("A", "AAAA", "CNAME"):
        rec["proxied"] = True
    if len(parts) == 4:
        rec["proxied"] = parts[3].lower() in ("1", "true", "proxied", "yes")
    return rec


def split_domain(domain):
    """Tách domain thành SLD + TLD cho Namecheap API. vd: shop.example.co.uk -> SLD=example, TLD=co.uk"""
    domain = domain.strip().lower().rstrip(".")
    parts = domain.split(".")
    if len(parts) < 2:
        raise ValueError(f"Domain không hợp lệ: {domain}")
    for tld in MULTI_PART_TLDS:
        if domain.endswith("." + tld):
            sld = domain[: -len(tld) - 1].split(".")[-1]
            return sld, tld
    return parts[-2], parts[-1]


def get_public_ip():
    try:
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception:
        return ""


# ---------------- Cloudflare ----------------
class Cloudflare:
    def __init__(self, token, account_id=""):
        self.token = token
        self.account_id = account_id
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _check(self, r, action):
        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"{action} lỗi HTTP {r.status_code}: {r.text[:300]}")
        if not data.get("success"):
            errs = data.get("errors") or []
            raise RuntimeError(f"{action} thất bại: {errs}")
        return data.get("result")

    def get_zone(self, domain):
        r = self.s.get(f"{CF_BASE}/zones", params={"name": domain, "per_page": 5}, timeout=30)
        result = self._check(r, f"Tìm zone {domain}")
        return result[0] if result else None

    def create_zone(self, domain):
        payload = {"name": domain, "type": "full", "jump_start": True}
        if self.account_id:
            payload["account"] = {"id": self.account_id}
        log(f"Cloudflare: tạo zone {domain} ...")
        r = self.s.post(f"{CF_BASE}/zones", json=payload, timeout=60)
        return self._check(r, f"Tạo zone {domain}")

    def get_or_create_zone(self, domain):
        zone = self.get_zone(domain)
        if zone:
            log(f"Cloudflare: zone đã có sẵn: {domain} (id={zone['id']}, status={zone.get('status')})")
            return zone, False
        zone = self.create_zone(domain)
        log(f"Cloudflare: đã tạo zone, NS được cấp: {', '.join(zone.get('name_servers') or [])}")
        return zone, True

    def scan_dns(self, zone_id):
        r = self.s.post(f"{CF_BASE}/zones/{zone_id}/dns_records/scan", timeout=60)
        return self._check(r, "Quét DNS cũ")

    def list_records(self, zone_id):
        out, page = [], 1
        while True:
            r = self.s.get(f"{CF_BASE}/zones/{zone_id}/dns_records", params={"per_page": 100, "page": page}, timeout=30)
            result = self._check(r, "List DNS records")
            out.extend(result or [])
            info = r.json().get("result_info", {})
            if page >= info.get("total_pages", 1):
                break
            page += 1
        return out

    def upsert_record(self, zone_id, domain, rec):
        name = domain if rec["name"] in ("@", "") else f"{rec['name']}.{domain}"
        existing = [x for x in self.list_records(zone_id) if x["type"] == rec["type"] and x["name"] == name]
        payload = {"type": rec["type"], "name": name, "content": rec["content"], "ttl": rec.get("ttl", 1)}
        # Chỉ A/AAAA/CNAME mới proxied được; MX/TXT... phải để False
        if rec["type"] in ("A", "AAAA", "CNAME"):
            payload["proxied"] = bool(rec.get("proxied", True))
        if rec["type"] == "MX":
            payload["priority"] = rec.get("priority", 10)
        if existing:
            rid = existing[0]["id"]
            if existing[0].get("content") == rec["content"]:
                log(f"  - {rec['type']} {name} đã đúng ({rec['content']}), bỏ qua")
                return "skipped"
            r = self.s.patch(f"{CF_BASE}/zones/{zone_id}/dns_records/{rid}", json=payload, timeout=30)
            self._check(r, f"Cập nhật {rec['type']} {name}")
            log(f"  - {rec['type']} {name} -> {rec['content']} (đã cập nhật)")
            return "updated"
        r = self.s.post(f"{CF_BASE}/zones/{zone_id}/dns_records", json=payload, timeout=30)
        self._check(r, f"Tạo {rec['type']} {name}")
        log(f"  - {rec['type']} {name} -> {rec['content']} (đã tạo)")
        return "created"


# ---------------- Namecheap ----------------
class Namecheap:
    def __init__(self, api_user, api_key, username, client_ip, sandbox=False):
        self.base = NC_BASE_SANDBOX if sandbox else NC_BASE_PROD
        self.api_user, self.api_key, self.username = api_user, api_key, username
        self.client_ip = client_ip or get_public_ip()
        if not self.client_ip:
            raise RuntimeError("Không xác định được ClientIp. Đặt NAMECHEAP_CLIENT_IP trong .env (IP public đã whitelist ở Namecheap).")

    def call(self, command, extra=None):
        params = {"ApiUser": self.api_user, "ApiKey": self.api_key, "UserName": self.username,
                  "Command": command, "ClientIp": self.client_ip}
        if extra:
            params.update(extra)
        r = requests.get(self.base, params=params, timeout=60)
        root = ET.fromstring(r.text)
        if root.attrib.get("Status") != "OK":
            errs = [e.text for e in root.iter("Error")]
            raise RuntimeError(f"Namecheap {command} lỗi: {'; '.join(errs) or r.text[:300]}")
        return root

    def get_nameservers(self, domain):
        sld, tld = split_domain(domain)
        root = self.call("namecheap.domains.dns.getList", {"SLD": sld, "TLD": tld})
        ns = [e.attrib.get("Nameserver") for e in root.iter("Nameserver")]
        return ns

    def set_custom_nameservers(self, domain, nameservers):
        sld, tld = split_domain(domain)
        log(f"Namecheap: đổi NS của {domain} sang {', '.join(nameservers)} ...")
        root = self.call("namecheap.domains.dns.setCustom",
                         {"SLD": sld, "TLD": tld, "Nameservers": ",".join(nameservers)})
        updated = root.find(".//DomainDNSSetCustomResult")
        ok = updated.attrib.get("Updated") == "true" if updated is not None else False
        if not ok:
            raise RuntimeError(f"Namecheap setCustom trả về Updated != true cho {domain}")
        log(f"Namecheap: đã đổi NS cho {domain}")
        return True


# ---------------- Verify ----------------
def verify_ns(domain, expected_ns):
    """Hỏi DNS 1.1.1.1 xem NS hiện tại là gì (DNS-over-HTTPS, không cần dig)."""
    try:
        r = requests.get(DOH_URL, params={"name": domain, "type": "NS"},
                         headers={"Accept": "application/dns-json"}, timeout=15)
        answers = r.json().get("Answer") or []
        found = sorted({a["data"].rstrip(".") for a in answers})
        expected = sorted(x.rstrip(".") for x in expected_ns)
        return found, found == expected
    except Exception as e:
        return [], False


# ---------------- Main flow ----------------
def setup_one(domain, cf, nc, preset, target_ip, args, custom_records=None):
    # custom_records: list record đã lọc cho domain này (3 ô, ô trống đã bỏ qua).
    # Nếu None thì dùng preset["records"].
    records = custom_records if custom_records is not None else list(preset.get("records") or [])
    phase = getattr(args, "phase", "all")
    result = {"domain": domain, "zone_id": "", "nameservers": "", "status": "", "records": "", "error": ""}
    try:
        if args.dry_run:
            # DRY-RUN không được gọi API tạo/sửa gì cả, kể cả tạo zone
            try:
                zone = cf.get_zone(domain)
            except Exception as e:
                log(f"[DRY-RUN] Không check được zone (token sai / chưa có mạng?): {e}", "WARN")
                zone = None
            if zone:
                log(f"[DRY-RUN] {domain}: zone đã có (id={zone['id']}), NS sẽ dùng: {zone.get('name_servers')}")
            else:
                log(f"[DRY-RUN] {domain}: chưa có zone -> sẽ POST /zones tạo mới, rồi lấy NS được cấp")
            if phase in ("1", "all"):
                log(f"[DRY-RUN] {domain}: giai đoạn 1 sẽ gọi Namecheap setCustom để đổi NS sang NS Cloudflare")
            if phase in ("2", "all"):
                if records:
                    for rec in records:
                        log(f"[DRY-RUN]   giai đoạn 2 sẽ tạo/cập nhật: {rec['type']} {rec['name']} -> {rec['content']}")
                else:
                    log(f"[DRY-RUN] {domain}: giai đoạn 2 không có record nào (tất cả ô đều trống / preset rỗng), bỏ qua")
            result["status"] = "dry-run"
            return result

        # Giai đoạn 2 yêu cầu zone đã có sẵn từ giai đoạn 1, không tự tạo mới
        if phase == "2":
            zone = cf.get_zone(domain)
            if not zone:
                raise RuntimeError(f"{domain} chưa có zone ở Cloudflare — chạy --phase 1 trước")
            created = False
            log(f"Cloudflare: zone đã có sẵn (giai đoạn 1): {domain} (id={zone['id']}, status={zone.get('status')})")
        else:
            zone, created = cf.get_or_create_zone(domain)
        result["zone_id"] = zone["id"]
        ns = zone.get("name_servers") or []
        result["nameservers"] = ";".join(ns)
        if not ns:
            raise RuntimeError("Cloudflare không trả về nameservers")

        if phase in ("1", "all") and not args.skip_scan and created:
            try:
                scanned = cf.scan_dns(zone["id"])
                log(f"Cloudflare: quét được {len(scanned or [])} record cũ")
            except Exception as e:
                log(f"Quét DNS cũ thất bại (không chặn luồng): {e}", "WARN")

        # Giai đoạn 2: DNS records (3 ô, ô trống đã lọc ở load_custom_records / preset)
        if phase in ("2", "all") and not args.only_namecheap:
            if not records:
                log("Không có record nào để tạo (tất cả ô đều trống), bỏ qua bước record")
            else:
                done = []
                for rec in records:
                    rec = dict(rec)
                    if "{target_ip}" in str(rec.get("content")):
                        if not target_ip:
                            raise RuntimeError("Preset này cần --target-ip (vd: --target-ip 1.2.3.4)")
                        rec["content"] = rec["content"].replace("{target_ip}", target_ip)
                    done.append(cf.upsert_record(zone["id"], domain, rec))
                result["records"] = ",".join(done)

        # Giai đoạn 1: Namecheap đổi NS (giai đoạn 2 bỏ qua, NS đã đổi rồi)
        if phase in ("1", "all") and not args.only_cloudflare:
            current_ns = nc.get_nameservers(domain)
            log(f"Namecheap: NS hiện tại của {domain}: {current_ns}")
            if sorted(x.lower() for x in current_ns) == sorted(x.lower() for x in ns):
                log("NS đã đúng, không cần đổi")
            else:
                nc.set_custom_nameservers(domain, ns)

        # Verify
        found, ok = verify_ns(domain, ns)
        zone_now = cf.get_zone(domain)
        cf_status = zone_now.get("status") if zone_now else ""
        log(f"Verify {domain}: NS qua 1.1.1.1 = {found} | Cloudflare status = {cf_status} "
            f"({'KHỚP' if ok else 'CHƯA PROPAGATE - thường cần 5 phút tới 24h'})")
        result["status"] = "active" if cf_status == "active" else ("pending" if ok else "pending-ns")
        return result
    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        log(f"{domain} LỖI: {e}", "ERROR")
        return result


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description="Auto setup domain Namecheap -> Cloudflare")
    p.add_argument("--domain", help="1 domain, vd: example.com")
    p.add_argument("--domains-file", help="File txt, mỗi dòng 1 domain")
    p.add_argument("--phase", default="all", choices=["1", "2", "all"],
                   help="1 = chỉ tạo zone + đổi NS (chưa cần record). 2 = chỉ thêm DNS record (phải qua phase 1 trước). all = cả 2 trong 1 lần.")
    p.add_argument("--records-file", help="File JSON custom records, vd custom-records.json. Mỗi domain 3 ô record, ô trống bỏ qua.")
    p.add_argument("--record", action="append", default=[],
                   help="1 record dạng 'Type:Name:Content', dùng nhiều lần cho tối đa 3 ô. Vd: --record 'A:@:1.2.3.4' --record 'CNAME:www:shops.myshopify.com'")
    p.add_argument("--preset", default="only-ns", choices=["only-ns", "shopify", "vps", "vercel"],
                   help="Preset DNS (xem presets.yaml). Mặc định only-ns = chỉ đổi NS.")
    p.add_argument("--target-ip", help="IP đích cho preset vps")
    p.add_argument("--target-host", help="Ghi chú / host đích (không bắt buộc)")
    p.add_argument("--dry-run", action="store_true", help="Chỉ in ra những gì sẽ làm, không gọi API đổi gì cả")
    p.add_argument("--only-cloudflare", action="store_true", help="Chỉ làm phía Cloudflare, không động vào Namecheap")
    p.add_argument("--only-namecheap", action="store_true", help="Chỉ đổi NS ở Namecheap (zone phải có sẵn ở Cloudflare)")
    p.add_argument("--verify-only", action="store_true", help="Chỉ kiểm tra NS + trạng thái zone, không tạo/sửa gì")
    p.add_argument("--skip-scan", action="store_true", help="Bỏ bước quét DNS cũ của Cloudflare")
    p.add_argument("--sandbox", action="store_true", help="Dùng Namecheap Sandbox API (để test)")
    p.add_argument("--yes", action="store_true", help="Bỏ qua hỏi xác nhận")
    args = p.parse_args()

    domains = []
    if args.domain:
        domains.append(args.domain.strip().lower())
    if args.domains_file:
        domains += [l.strip().lower() for l in Path(args.domains_file).read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.strip().startswith("#")]
    domains = list(dict.fromkeys(domains))
    if not domains:
        p.error("Cần --domain hoặc --domains-file")

    presets = load_presets()
    preset = presets.get(args.preset)
    if not preset:
        print(f"Preset không có: {args.preset}. Có sẵn: {', '.join(presets)}")
        sys.exit(1)

    # Records cho giai đoạn 2: ưu tiên --records-file, rồi --record, rồi preset
    custom_by_domain = None
    if args.records_file:
        custom_by_domain = load_custom_records(args.records_file, domains)
    elif args.record:
        recs = [parse_record_arg(s) for s in args.record]
        custom_by_domain = {d: recs for d in domains}
    if args.phase == "2" and custom_by_domain is None and args.preset == "only-ns":
        print("Giai đoạn 2 cần record: dùng --records-file custom-records.json hoặc --record 'A:@:IP' hoặc --preset shopify/vps/vercel")
        sys.exit(1)

    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not cf_token:
        print("Thiếu CLOUDFLARE_API_TOKEN. Copy .env.example thành .env rồi điền token.")
        sys.exit(1)
    cf = Cloudflare(cf_token, os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))

    nc = None
    if not args.only_cloudflare and not args.verify_only and not args.dry_run and args.phase != "2":
        nc = Namecheap(os.environ.get("NAMECHEAP_API_USER", ""), os.environ.get("NAMECHEAP_API_KEY", ""),
                       os.environ.get("NAMECHEAP_USERNAME", ""), os.environ.get("NAMECHEAP_CLIENT_IP", ""),
                       sandbox=args.sandbox or os.environ.get("NAMECHEAP_SANDBOX", "") == "1")
        if not all([nc.api_user, nc.api_key, nc.username]):
            print("Thiếu NAMECHEAP_API_USER / NAMECHEAP_API_KEY / NAMECHEAP_USERNAME trong .env")
            sys.exit(1)

    if args.verify_only:
        for d in domains:
            zone = cf.get_zone(d)
            if not zone:
                log(f"{d}: chưa có zone ở Cloudflare", "WARN")
                continue
            found, ok = verify_ns(d, zone.get("name_servers") or [])
            print(f"{d}: Cloudflare={zone.get('status')} | NS hiện tại={found} | khớp={'YES' if ok else 'NO'}")
        return

    phase_label = {"1": "GIAI ĐOẠN 1: tạo zone + đổi NS (chưa cần record)",
                   "2": "GIAI ĐOẠN 2: chỉ thêm DNS record (zone đã có từ giai đoạn 1)",
                   "all": "CẢ 2 GIAI ĐOẠN trong 1 lần"}[args.phase]
    print(f"\n{phase_label}")
    print(f"Sẽ setup {len(domains)} domain, preset={args.preset} ({preset.get('description','')})")
    if custom_by_domain:
        for d in domains:
            recs = custom_by_domain.get(d, [])
            print(f"  {d}: {len(recs)} record (ô trống đã bỏ qua) — " +
                  (", ".join(f"{r['type']} {r['name']}->{r['content']}" for r in recs) if recs else "không có record"))
    print(f"Domains: {', '.join(domains)}")
    if args.dry_run:
        print("Chế độ DRY-RUN: không thay đổi gì thật.")
    if len(domains) > 25:
        print("CẢNH BÁO: Cloudflare giới hạn ~25 zone mới / 10 phút, và chặn thêm zone khi số zone pending > số zone active. Nên chạy từng đợt nhỏ, chờ active rồi chạy tiếp.")
    if not args.yes and not args.dry_run:
        ans = input("Tiếp tục? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Đã huỷ.")
            return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = BASE_DIR / "reports" / f"setup_{ts}.csv"
    report.parent.mkdir(exist_ok=True)
    results = []
    for i, d in enumerate(domains, 1):
        log(f"=== [{i}/{len(domains)}] {d} ===")
        recs = custom_by_domain.get(d) if custom_by_domain is not None else None
        results.append(setup_one(d, cf, nc, preset, args.target_ip, args, custom_records=recs))
        if i < len(domains):
            time.sleep(2)  # tránh rate limit

    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["domain", "zone_id", "nameservers", "status", "records", "error"])
        w.writeheader()
        w.writerows(results)
    ok = sum(1 for r in results if r["status"] in ("active", "pending", "pending-ns", "dry-run"))
    print(f"\nXong: {ok}/{len(results)} OK. Báo cáo: {report}")
    print("Lưu ý: zone mới thường ở trạng thái 'pending' cho tới khi NS propagate (5 phút - 24h). Chạy lại --verify-only để kiểm tra.")


if __name__ == "__main__":
    main()
