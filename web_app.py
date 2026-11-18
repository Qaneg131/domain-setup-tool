#!/usr/bin/env python3
"""
Web nội bộ: Namecheap -> Cloudflare BẰNG CHROME ĐÃ ĐĂNG NHẬP.
KHÔNG cần API key / token. Không nhập mật khẩu vào đây.

Chạy ở máy bạn:
    pip install -r requirements.txt
    playwright install chromium   # 1 lần duy nhất
    python3 web_app.py
    # mở http://127.0.0.1:8000

- Giai đoạn 1: dán full domain, chạy hàng loạt tới bước 3
  (Cloudflare Add site -> lấy 2 NS -> Namecheap Custom DNS -> lưu).
- Giai đoạn 2: sau đó mới thêm DNS record, mỗi domain 3 ô
  (Type / Name / Content), ô nào trống thì bỏ qua.
- Khi bấm Chạy, tool tự mở Chrome bằng đúng profile của bạn
  (đã đăng nhập sẵn Cloudflare + Namecheap) và tự click thay bạn.
  Bạn chỉ cần ngồi xem, không cần thao tác gì.
  Nếu tool báo "profile đang được dùng": đóng hết cửa sổ Chrome
  rồi bấm Chạy lại — tool sẽ tự mở Chrome đã đăng nhập.
  (Hoặc chạy start-chrome.bat / start-chrome.sh trước, rồi bấm Chạy.)

Bản API cũ (cần key) vẫn giữ ở web_app_api.py + setup_domain.py,
chỉ dùng khi bạn muốn chạy không cần mở Chrome.
"""
import csv
import glob
import io
import json
import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

BASE_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8000"))

# Windows: console mặc định dùng cp1258, không in/đọc được vài chữ tiếng Việt
# (vd chữ "ổ") -> ép sang UTF-8 để log của chrome_setup.py không bị vỡ.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HTML = """<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Domain Setup — Chrome đã đăng nhập (nội bộ)</title>
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
.ok{color:#1a7a3a;font-weight:700} .fail{color:#c0392b;font-weight:700}
#ket-qua{margin-top:8px} #run-btn:disabled{opacity:.6;cursor:wait}
.chay{color:#a06a35;font-weight:700}
</style></head><body>
<h1>Domain Setup — Namecheap → Cloudflare</h1>
<p class="note"><b>Chạy bằng Chrome đã đăng nhập — không cần key/token gì hết.</b>
<br>Web nội bộ ở http://127.0.0.1:8000, chỉ ở máy bạn.
<br><b>Giai đoạn 1:</b> dán full domain, chạy hàng loạt tới bước 3 (tạo zone + đổi NS).
<br><b>Giai đoạn 2:</b> sau đó mới thêm DNS record, mỗi domain 3 ô, ô nào trống thì bỏ qua.
<br><b>Cách chạy:</b> bấm Chạy, tool tự mở Chrome bằng profile của bạn (đã đăng nhập sẵn)
và tự click thay bạn. Bạn không cần thao tác gì, chỉ ngồi xem.
Nếu báo "profile đang được dùng" thì đóng hết Chrome rồi bấm Chạy lại.</p>

<form method="post" action="/run" id="setup-form">
<h2>1. Giai đoạn 1 — Full domain (mỗi dòng 1 domain)</h2>
<textarea name="domains" rows="8" placeholder="example.com&#10;shop-example.com&#10;landing-example.com"></textarea>
<p>
<label style="display:inline"><input type="radio" name="phase" value="1" checked style="width:auto"> Giai đoạn 1: tạo zone + đổi NS</label>
<label style="display:inline;margin-left:12px"><input type="radio" name="phase" value="2" style="width:auto"> Giai đoạn 2: chỉ thêm DNS record</label>
<label style="display:inline;margin-left:12px"><input type="radio" name="phase" value="all" style="width:auto"> Cả 2 trong 1 lần</label>
<label style="display:inline;margin-left:12px"><input type="checkbox" name="dry_run" checked style="width:auto"> Dry-run (mở Chrome kiểm tra đăng nhập, xem trước, không đổi gì thật)</label>
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

<p><button type="submit" id="run-btn">Chạy bằng Chrome</button>
<button type="button" class="secondary" onclick="window.open('https://dash.cloudflare.com','_blank')">Mở Cloudflare</button>
<button type="button" class="secondary" onclick="window.open('https://www.namecheap.com/myaccount/','_blank')">Mở Namecheap</button></p>
<p class="small">Tool không lưu mật khẩu, không lấy key. Nó chỉ điều khiển Chrome ở máy bạn,
dùng phiên đăng nhập sẵn có. Chạy xong Chrome vẫn mở để bạn kiểm tra lại.</p>
</form>

<div id="ket-qua" style="display:none">
<h2>Kết quả</h2>
<p id="kq-tomtat" class="note"></p>
<table><thead><tr><th>Domain</th><th>Status</th><th>Nameservers</th><th>Records</th><th>Lỗi</th></tr></thead>
<tbody id="kq-bang"></tbody></table>
<h3>Log</h3>
<pre id="kq-log"></pre>
<p class="small">Zone mới thường ở trạng thái pending tới khi NS propagate (5 phút – 24h).
Nếu 1 domain lỗi vì giao diện đổi nút, tool đã chụp màn hình vào thư mục reports/ — mở ảnh đó lên là biết nó kẹt ở đâu.</p>
</div>
<script>
document.getElementById('setup-form').addEventListener('submit', async function(ev){
  ev.preventDefault();
  const box = document.getElementById('ket-qua');
  const tomtat = document.getElementById('kq-tomtat');
  const bang = document.getElementById('kq-bang');
  const logEl = document.getElementById('kq-log');
  const btn = document.getElementById('run-btn');
  btn.disabled = true; btn.textContent = 'Đang chạy bằng Chrome...';
  box.style.display = 'block';
  tomtat.innerHTML = '<span class="chay">Đang chạy bằng Chrome, đừng đóng trang này... (nhìn qua cửa sổ Chrome để xem tool click)</span>';
  bang.innerHTML = ''; logEl.textContent = '';
  box.scrollIntoView({behavior:'smooth', block:'start'});
  try {
    const res = await fetch('/run', {method:'POST', body:new FormData(ev.target)});
    const data = await res.json();
    const okN = (data.rows||[]).filter(r => r.status && r.status !== 'failed').length;
    tomtat.textContent = data.error
      ? 'Lỗi: ' + data.error
      : `Xong: ${okN}/${data.domains} domain OK — Phase ${data.phase}${data.dry_run ? ' (dry-run)' : ''}, ${data.records} record đã nhập. Chrome vẫn mở để bạn kiểm tra lại.`;
    if (!data.rows || !data.rows.length) {
      bang.innerHTML = '<tr><td colspan="5">Chưa có báo cáo CSV — xem log bên dưới (có thể Chrome chưa mở được, xem mục \u201cprofile đang được dùng\u201d).</td></tr>';
    } else {
      bang.innerHTML = '';
      data.rows.forEach(function(r){
        const tr = document.createElement('tr');
        [['domain'],['status'],['nameservers'],['records'],['error']].forEach(function(k){
          const td = document.createElement('td');
          td.textContent = r[k[0]] || '';
          if (k[0] === 'status') td.className = (r.status === 'failed') ? 'fail' : 'ok';
          tr.appendChild(td);
        });
        bang.appendChild(tr);
      });
    }
    logEl.textContent = data.log || '';
  } catch (e) {
    tomtat.textContent = 'Lỗi kết nối tới web nội bộ: ' + e + ' — kiểm tra cửa sổ terminal chạy web còn mở không.';
  }
  btn.disabled = false; btn.textContent = 'Chạy bằng Chrome';
});
</script>
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

    def _send_json(self, obj, code=200):
        # Kết quả trả về dạng JSON để trang chính hiện ngay tại chỗ, không nhảy trang.
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(HTML)
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
            self._send_json({"ok": False, "error": "Thiếu domain — dán domain vào ô Giai đoạn 1, mỗi dòng 1 domain.",
                             "phase": phase, "dry_run": dry_run, "domains": 0, "records": 0,
                             "rows": [], "log": ""})
            return

        # 3 ô record: ô nào trống content thì bỏ qua
        records = []
        for i in range(20):
            t = get(f"type_{i}")
            c = get(f"content_{i}", "").strip()
            if not t or not c:
                continue
            rec = {"type": t, "name": get(f"name_{i}", "@") or "@", "content": c,
                   "proxied": f"proxied_{i}" in form}
            records.append(rec)

        # Ghi input tạm rồi gọi chrome_setup.py (chạy Chrome thật ở máy bạn)
        tmpdir = Path(tempfile.mkdtemp(prefix="domain-chrome-"))
        dom_file = tmpdir / "domains.txt"
        dom_file.write_text("\n".join(domains) + "\n", encoding="utf-8")
        cmd = [sys.executable, str(BASE_DIR / "chrome_setup.py"),
               "--domains-file", str(dom_file), "--phase", phase, "--keep-open"]
        if dry_run:
            cmd.append("--dry-run")
        rec_file = None
        if phase in ("2", "all") and records:
            rec_file = tmpdir / "records.json"
            rec_file.write_text(json.dumps({"__shared__": records}, ensure_ascii=False),
                                encoding="utf-8")
            cmd += ["--records-file", str(rec_file)]

        before = set(glob.glob(str(BASE_DIR / "reports" / "chrome_setup_*.csv")))
        try:
            env = dict(os.environ)
            env["PYTHONUTF8"] = "1"  # bắt chrome_setup.py con in log bằng UTF-8
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                                  cwd=str(BASE_DIR), env=env,
                                  encoding="utf-8", errors="replace")
            log_text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        except subprocess.TimeoutExpired:
            log_text = "Hết thời gian chờ (30 phút). Chrome có thể vẫn đang chạy — kiểm tra lại trong Chrome."
            proc = None
        except Exception as e:
            log_text = f"LỖI chạy chrome_setup.py: {e}"

        # Đọc báo cáo CSV mới nhất (nếu có) -> trả JSON, trang chính tự vẽ bảng tại chỗ.
        rows = []
        after = sorted(set(glob.glob(str(BASE_DIR / "reports" / "chrome_setup_*.csv"))) - before)
        csv_path = after[-1] if after else None
        if csv_path:
            try:
                with open(csv_path, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        rows.append({"domain": r.get("domain", ""), "status": r.get("status", ""),
                                     "nameservers": r.get("nameservers", ""), "records": r.get("records", ""),
                                     "error": r.get("error", "")})
            except Exception:
                pass
        self._send_json({"ok": True, "error": "", "phase": phase, "dry_run": dry_run,
                         "domains": len(domains), "records": len(records),
                         "rows": rows, "log": log_text})


if __name__ == "__main__":
    print(f"Web nội bộ (Chrome) chạy ở http://127.0.0.1:{PORT}  — mở link này trên trình duyệt máy bạn.")
    print("Bấm Chạy là tool tự mở Chrome đã đăng nhập và làm thay bạn. Nhấn Ctrl+C để dừng web.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
