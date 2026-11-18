#!/usr/bin/env python3
"""
Web nội bộ: Namecheap -> Cloudflare, chạy trên máy của bạn, không public.
Chạy:
    python3 web_app.py
    # mở http://127.0.0.1:8000 trên trình duyệt

- Giai đoạn 1: dán full domain (mỗi dòng 1 domain) -> tạo zone Cloudflare + đổi NS ở Namecheap (tới bước 3)
- Giai đoạn 2: nhập domain + 3 ô DNS record (Type / Name / Content), ô nào để trống thì BỎ QUA,
  hỗ trợ A, AAAA, CNAME, TXT, MX. Có nút + Thêm dòng nếu cần hơn 3.
- Key/token chỉ nhập ở máy bạn, lưu vào file .env local, KHÔNG gửi đi đâu khác,
  KHÔNG commit lên git (đã có .gitignore).
- Chỉ dùng Python stdlib + requests, không cần Flask.
"""
import io
import os
import sys
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from setup_domain import Cloudflare, Namecheap, load_dotenv, load_presets, setup_one  # noqa: E402

load_dotenv()

PORT = int(os.environ.get("PORT", "8000"))

HTML = """<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Domain Setup — Namecheap → Cloudflare (nội bộ)</title>
<style>
body{font-family:system-ui,Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#16130f;background:#fffdf9}
h1{font-size:24px} h2{font-size:18px;margin-top:28px;border-top:1px solid #ddd;padding-top:16px}
label{display:block;margin:8px 0 4px;font-weight:600}
input,select,textarea{width:100%;padding:8px;border:1px solid #bbb;border-radius:6px;box-sizing:border-box}
textarea{font-family:monospace}
button{padding:10px 16px;border:0;border-radius:6px;background:#16130f;color:#fff;cursor:pointer;margin:6px 6px 0 0}
button.secondary{background:#a06a35} button.ghost{background:#eee;color:#16130f}
table{width:100%;border-collapse:collapse} td,th{padding:6px;border:1px solid #ddd;text-align:left}
td input,td select{width:100%} .row-proxied{width:60px;text-align:center}
.note{background:#f5f2ed;padding:12px;border-radius:6px} .small{color:#666;font-size:13px}
pre{background:#16130f;color:#f5f2ed;padding:12px;border-radius:6px;white-space:pre-wrap}
</style></head><body>
<h1>Domain Setup — Namecheap → Cloudflare</h1>
<p class="note"><b>Web nội bộ, chạy ở http://127.0.0.1:8000</b> — key chỉ ở máy bạn, trong file <code>.env</code>, không gửi đi đâu khác.
<br><b>Giai đoạn 1:</b> dán full domain, chạy hàng loạt tới bước 3 (tạo zone + đổi NS).
<br><b>Giai đoạn 2:</b> sau đó mới thêm DNS record, mỗi domain 3 ô, ô nào trống thì bỏ qua.</p>

<form method="post" action="/run">
<h2>0. Key / Token (nhập 1 lần, tick Lưu để ghi vào .env)</h2>
<label>Cloudflare API Token</label>
<input name="cf_token" type="password" placeholder="Token có quyền Zone:Edit + DNS:Edit" value="{cf_token}">
<label>Namecheap API User</label><input name="nc_user" value="{nc_user}">
<label>Namecheap API Key</label><input name="nc_key" type="password" value="">
<label>Namecheap Username</label><input name="nc_username" value="{nc_username}">
<label><input type="checkbox" name="save_settings" style="width:auto"> Lưu các key trên vào .env ở máy này</label>

<h2>1. Giai đoạn 1 — Full domain (mỗi dòng 1 domain)</h2>
<textarea name="domains" rows="8" placeholder="example.com&#10;shop-example.com&#10;landing-example.com"></textarea>
<p>
<label style="display:inline"><input type="radio" name="phase" value="1" checked style="width:auto"> Giai đoạn 1: tạo zone + đổi NS</label>
<label style="display:inline;margin-left:12px"><input type="radio" name="phase" value="2" style="width:auto"> Giai đoạn 2: chỉ thêm DNS record</label>
<label style="display:inline;margin-left:12px"><input type="radio" name="phase" value="all" style="width:auto"> Cả 2 trong 1 lần</label>
<label style="display:inline;margin-left:12px"><input type="checkbox" name="dry_run" checked style="width:auto"> Dry-run (xem trước, không đổi gì thật)</label>
</p>

<h2>2. Giai đoạn 2 — DNS record (3 ô, ô trống bỏ qua)</h2>
<p class="small">Preset nhanh:
<button type="button" class="ghost" onclick="fillPreset('shopify')">Shopify</button>
<button type="button" class="ghost" onclick="fillPreset('vps')">VPS (cần IP ở ô A)</button>
<button type="button" class="ghost" onclick="fillPreset('vercel')">Vercel</button>
<button type="button" class="ghost" onclick="fillPreset('clear')">Xoá hết</button>
</p>
<table id="records"><tr><th>Type</th><th>Name (@ = root)</th><th>Content (IP / đích / text)</th><th>Proxied</th></tr>
<tr><td><select name="type_0"><option>A</option><option>AAAA</option><option>CNAME</option><option>TXT</option><option>MX</option></select></td><td><input name="name_0" value="@" placeholder="@"></td><td><input name="content_0" placeholder="vd: 23.227.38.65"></td><td class="row-proxied"><input type="checkbox" name="proxied_0" checked></td></tr>
<tr><td><select name="type_1"><option>A</option><option>AAAA</option><option selected>CNAME</option><option>TXT</option><option>MX</option></select></td><td><input name="name_1" value="www" placeholder="www"></td><td><input name="content_1" placeholder="vd: shops.myshopify.com"></td><td class="row-proxied"><input type="checkbox" name="proxied_1" checked></td></tr>
<tr><td><select name="type_2"><option>A</option><option>AAAA</option><option>CNAME</option><option selected>TXT</option><option>MX</option></select></td><td><input name="name_2" value="@" placeholder="@"></td><td><input name="content_2" placeholder="để trống = bỏ qua ô này"></td><td class="row-proxied"><input type="checkbox" name="proxied_2"></td></tr>
</table>
<p><button type="button" class="ghost" onclick="addRow()">+ Thêm 1 ô record</button></p>

<p><button type="submit">Chạy</button>
<button type="button" class="secondary" onclick="window.open('https://dash.cloudflare.com','_blank')">Mở Cloudflare</button>
<button type="button" class="secondary" onclick="window.open('https://www.namecheap.com/myaccount/','_blank')">Mở Namecheap</button></p>
<p class="small">Không muốn dùng API key? Dùng cách Chrome: mở 2 link trên, đăng nhập tay, làm theo các bước 1-3 ở trên. Web này chỉ là form nhập liệu + chạy API ở máy bạn.</p>
</form>
<script>
let n=3;
function addRow(){const t=document.getElementById('records');const r=t.insertRow();
r.innerHTML=`<td><select name="type_${n}"><option>A</option><option>AAAA</option><option>CNAME</option><option>TXT</option><option>MX</option></select></td><td><input name="name_${n}" placeholder="@"></td><td><input name="content_${n}" placeholder="để trống = bỏ qua"></td><td class="row-proxied"><input type="checkbox" name="proxied_${n}" checked></td>`;n++;}
function setRow(i,type,name,content,proxied){document.getElementsByName('type_'+i)[0].value=type;document.getElementsByName('name_'+i)[0].value=name;document.getElementsByName('content_'+i)[0].value=content;document.getElementsByName('proxied_'+i)[0].checked=proxied;}
function fillPreset(p){
 if(p==='shopify'){setRow(0,'A','@','23.227.38.65',true);setRow(1,'CNAME','www','shops.myshopify.com',true);setRow(2,'TXT','@','',false);}
 if(p==='vps'){setRow(0,'A','@','1.2.3.4',true);setRow(1,'A','www','1.2.3.4',true);setRow(2,'TXT','@','',false);}
 if(p==='vercel'){setRow(0,'A','@','76.76.21.21',false);setRow(1,'CNAME','www','cname.vercel-dns.com',false);setRow(2,'TXT','@','',false);}
 if(p==='clear'){for(let i=0;i<3;i++)setRow(i,'A','@','',false);}
}
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            # Không dùng .format() vì HTML/CSS/JS chứa nhiều dấu { } — thay token thủ công
            page = (HTML.replace("{cf_token}", "••••••••" if os.environ.get("CLOUDFLARE_API_TOKEN") else "")
                        .replace("{nc_user}", os.environ.get("NAMECHEAP_API_USER", ""))
                        .replace("{nc_username}", os.environ.get("NAMECHEAP_USERNAME", "")))
            self._send(page)
        else:
            self._send("<h1>404</h1><p><a href='/'>Về trang chính</a></p>", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        get = lambda k, d="": (form.get(k) or [d])[0]

        if self.path == "/run":
            self._handle_run(form, get)
        else:
            self._send("<h1>404</h1>", 404)

    def _handle_run(self, form, get):
        domains = [l.strip().lower() for l in get("domains", "").splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        phase = get("phase", "1")
        dry_run = "dry_run" in form
        if not domains:
            self._send("<h1>Thiếu domain</h1><p>Dán domain vào ô Giai đoạn 1, mỗi dòng 1 domain.</p><p><a href='/'>Quay lại</a></p>")
            return

        # Key: ưu tiên form nhập, rồi .env
        cf_token = get("cf_token") or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        # token đã lưu chỉ hiện ••••, không dùng lại được — nếu user không nhập token mới thì lấy từ .env
        if cf_token.startswith("•"):
            cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if get("save_settings"):
            env_path = BASE_DIR / ".env"
            lines = []
            if env_path.exists():
                lines = [l for l in env_path.read_text(encoding="utf-8").splitlines()
                         if not l.startswith(("CLOUDFLARE_API_TOKEN=", "NAMECHEAP_API_USER=",
                                              "NAMECHEAP_API_KEY=", "NAMECHEAP_USERNAME="))]
            if cf_token:
                lines.append(f"CLOUDFLARE_API_TOKEN={cf_token}")
            if get("nc_user"):
                lines.append(f"NAMECHEAP_API_USER={get('nc_user')}")
            if get("nc_key"):
                lines.append(f"NAMECHEAP_API_KEY={get('nc_key')}")
            if get("nc_username"):
                lines.append(f"NAMECHEAP_USERNAME={get('nc_username')}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 3 ô record: ô nào trống content thì bỏ qua
        records = []
        for i in range(20):
            t = get(f"type_{i}")
            c = get(f"content_{i}", "").strip()
            if not t or not c:
                continue
            rec = {"type": t, "name": get(f"name_{i}", "@") or "@", "content": c, "ttl": 1}
            if t in ("A", "AAAA", "CNAME"):
                rec["proxied"] = f"proxied_{i}" in form
            records.append(rec)

        buf = io.StringIO()
        results = []
        with redirect_stdout(buf):
            try:
                cf = Cloudflare(cf_token, os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
                nc = None
                if phase != "2" and not dry_run:
                    nc = Namecheap(get("nc_user") or os.environ.get("NAMECHEAP_API_USER", ""),
                                   get("nc_key") or os.environ.get("NAMECHEAP_API_KEY", ""),
                                   get("nc_username") or os.environ.get("NAMECHEAP_USERNAME", ""),
                                   os.environ.get("NAMECHEAP_CLIENT_IP", ""))
                preset = {"records": records}
                # object giả lập args của CLI
                class Args:
                    pass
                args = Args()
                args.phase = phase
                args.dry_run = dry_run
                args.skip_scan = False
                args.only_cloudflare = False
                args.only_namecheap = False
                for d in domains:
                    print(f"=== {d} (phase {phase}) ===", flush=True)
                    # phase 2 mà không có record thì báo luôn, không gọi API vô ích
                    if phase == "2" and not records:
                        print("  Không có record nào (tất cả ô đều trống), bỏ qua.", flush=True)
                        results.append({"domain": d, "status": "skipped-empty", "records": ""})
                        continue
                    r = setup_one(d, cf, nc, preset, None, args,
                                  custom_records=records if phase in ("2", "all") else [])
                    results.append(r)
            except Exception as e:
                print(f"LỖI: {e}", flush=True)

        rows = "".join(
            f"<tr><td>{r.get('domain')}</td><td>{r.get('status')}</td>"
            f"<td>{r.get('nameservers','')}</td><td>{r.get('records','')}</td><td>{r.get('error','')}</td></tr>"
            for r in results)
        log = buf.getvalue()
        self._send(f"""<h1>Kết quả — Phase {phase} {'(dry-run)' if dry_run else ''}</h1>
<p>{len(results)} domain, {len(records)} record đã nhập (ô trống đã bỏ qua).</p>
<table><tr><th>Domain</th><th>Status</th><th>Nameservers</th><th>Records</th><th>Lỗi</th></tr>{rows}</table>
<h2>Log</h2><pre>{log}</pre>
<p><a href="/">← Quay lại form</a></p>
<p class="small">Zone mới thường ở trạng thái pending tới khi NS propagate (5 phút – 24h).</p>""")


if __name__ == "__main__":
    print(f"Web nội bộ chạy ở http://127.0.0.1:{PORT}  — mở link này trên trình duyệt máy bạn.")
    print("Nhấn Ctrl+C để dừng.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
