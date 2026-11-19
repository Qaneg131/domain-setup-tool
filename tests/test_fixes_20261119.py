#!/usr/bin/env python3
"""Test cac fix 2026-11-19 tren Chromium that (mock HTML + route):
(a) namecheap_set_ns: xac minh sau luu POLL toi 15s (trang SPA render cham)
    thay vi check 1 lan sau 2.5s — mock cho NS hien sau 4s.
(b) cloudflare_add_site: het 30s khong thay 'Continue to activation' thi
    chay lai bang link add-site -> domain da set -> trang Update nameservers.
(c) _read_current_ns: doc NS hien tai o trang Manage.
(d) cloudflare_add_records: vao thang link records, xoa het record cu
    (chon het -> Delete -> go DELETE -> xoa) roi moi them record moi.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chrome_setup as cs
from playwright.sync_api import sync_playwright

ACCT = "c30bace6d8d169fb77ae278b7d0fc82a"
NS1, NS2 = "brit.ns.cloudflare.com", "langston.ns.cloudflare.com"
EXE = "/home/hatch/.cache/ms-playwright/chromium_headless_shell-1255/chrome-headless-shell-linux64/chrome-headless-shell"

# ------------------------------------------------------------------ mock HTML
MANAGE_DELAYED = f"""<html><body>
<h2>Nameservers</h2>
<select id="ns-type"><option>Namecheap BasicDNS</option>
<option>Namecheap Web Hosting DNS</option><option>Custom DNS</option></select>
<div class="ns-section">
<input type="text" placeholder="Nameserver 1" value="">
<input type="text" placeholder="Nameserver 2" value="">
<div class="inline-actions"><a class="save" href="#" onclick="sessionStorage.setItem('saved','1');return false">Save</a>
<a class="cancel" href="#">Cancel</a></div>
<div id="saved-ns"></div></div>
<script>setTimeout(() => {{
  document.getElementById('saved-ns').textContent = '{NS1} {NS2}';
}}, 4000);</script></body></html>"""

MANAGE_CF_NS = f"""<html><body><h2>Nameservers</h2>
<select><option>Custom DNS</option></select>
<div>{NS1}</div><div>{NS2}</div></body></html>"""

MANAGE_REG_NS = """<html><body><h2>Nameservers</h2>
<select><option>Custom DNS</option></select>
<div>dns1.registrar-servers.com</div><div>dns2.registrar-servers.com</div></body></html>"""

CF_HOME = f"""<html><body><h1>Account home</h1><p>Domains</p>
<script>if (location.pathname === '/') location.replace('https://dash.cloudflare.com/{ACCT}');</script>
</body></html>"""

CF_CONNECT = """<html><body><h1>Connect your domain</h1>
<input type="text" placeholder="example.com">
<div>Additional configuration</div>
<div>Enable Bot Preference Sync <button role="switch" aria-checked="true"
 onclick="this.setAttribute('aria-checked','false')">Toggle</button></div>
<button onclick="if (sessionStorage.getItem('tried')) { location.href='https://dash.cloudflare.com/nameservers'; } else { sessionStorage.setItem('tried','1'); location.href='https://dash.cloudflare.com/plan'; }">Continue</button>
</body></html>"""

CF_PLAN = """<html><body><h2>Select a plan</h2>
<div class="plan"><h3>Free</h3><p>$0</p>
<button onclick="location.href='https://dash.cloudflare.com/review-stuck'">Select plan</button></div>
<div class="plan"><h3>Pro</h3><p>$20</p><button>Select plan</button></div>
</body></html>"""

CF_REVIEW_STUCK = """<html><body><h2>Review DNS records</h2>
<p>Please wait while we scan your DNS records...</p></body></html>"""

CF_NAMESERVERS = f"""<html><body><h2>Update your nameservers</h2>
<button>I updated my nameservers</button>
<p>{NS1}</p><p>{NS2}</p></body></html>"""

RECORD_ROWS = "".join(
    f'<tr><td><input type="checkbox"></td><td>elvellia.com</td><td>A</td><td>192.64.119.2</td></tr>'
    for _ in range(8))

CF_RECORDS = f"""<html><body><h1>DNS records for elvellia.com</h1>
<p>You have used 8 of 200 available DNS records in this domain.</p>
<button onclick="document.getElementById('rec-form').style.display=''">Add record</button>
<table><thead><tr><th><input type="checkbox" id="sel-all"
 onclick="document.querySelectorAll('#rec-tbody input[type=checkbox]').forEach(c=>c.checked=true);document.getElementById('del-n').style.display=''"></th>
<th>Name</th><th>Type</th><th>Content</th></tr></thead>
<tbody id="rec-tbody">{RECORD_ROWS}</tbody></table>
<button id="del-n" style="display:none"
 onclick="document.getElementById('del-dialog').style.display=''">Delete 8 records</button>
<div role="dialog" id="del-dialog" style="display:none">
<h2>Delete DNS records</h2><p>Type in "DELETE" to confirm</p>
<input type="text" id="del-input">
<button onclick="document.getElementById('del-dialog').style.display='none'">Cancel</button>
<button onclick="if (document.getElementById('del-input').value === 'DELETE') {{ document.getElementById('rec-tbody').innerHTML=''; document.getElementById('del-dialog').style.display='none'; }}">Delete</button>
</div>
<form id="rec-form" style="display:none">
<label>Type <select><option>A</option><option>AAAA</option><option>CNAME</option><option>TXT</option><option>MX</option></select></label>
<label>Name <input type="text"></label>
<label>IPv4 address <input type="text"></label>
<button type="button" onclick="document.title='record-saved'">Save</button>
</form></body></html>"""


def route_cf(page):
    def handler(route):
        url = route.request.url
        if url == "https://dash.cloudflare.com/" or url == f"https://dash.cloudflare.com/{ACCT}":
            body = CF_HOME
        elif "add-site" in url:
            body = CF_CONNECT
        elif url.endswith("/plan"):
            body = CF_PLAN
        elif url.endswith("/review-stuck"):
            body = CF_REVIEW_STUCK
        elif url.endswith("/nameservers"):
            body = CF_NAMESERVERS
        elif "/dns/records" in url:
            body = CF_RECORDS
        else:
            body = "<html><body>?</body></html>"
        route.fulfill(status=200, content_type="text/html", body=body)
    page.route("**://dash.cloudflare.com/**", handler)


def route_nc(page, html):
    def handler(route):
        route.fulfill(status=200, content_type="text/html", body=html)
    page.route("**://ap.www.namecheap.com/**", handler)


def test_a(p):
    print("TEST A: xac minh sau luu (NS hien sau 4s, poll 15s)...", flush=True)
    b = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page = b.new_page()
    route_nc(page, MANAGE_DELAYED)
    out = cs.namecheap_set_ns(page, "elvellia.com", [NS1, NS2],
                              manage_url="https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert out == "saved", out
    # sessionStorage con nguyen sau khi tai lai trang -> chung to nut save da duoc bam.
    assert page.evaluate("() => sessionStorage.getItem('saved')") == "1", "nut save chua duoc bam"
    print("  OK: tra ve 'saved' du NS hien cham 4s.", flush=True)
    b.close()


def test_b(p):
    print("TEST B: het 30s khong thay CTA -> chay lai bang link add-site...", flush=True)
    b = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page = b.new_page()
    route_cf(page)
    ns, st = cs.cloudflare_add_site(page, "elvellia.com")
    assert st == "exists", st
    assert set(ns) == {NS1, NS2}, ns
    print(f"  OK: retry lay duoc NS tu trang Update nameservers: {ns}", flush=True)
    b.close()


def test_c(p):
    print("TEST C: _read_current_ns...", flush=True)
    b = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page = b.new_page()
    route_nc(page, MANAGE_CF_NS)
    page.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._read_current_ns(page) == [NS1, NS2], cs._read_current_ns(page)
    print("  OK: NS Cloudflare doc duoc.", flush=True)
    route_nc2 = page
    b.close()
    b2 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page2 = b2.new_page()
    route_nc(page2, MANAGE_REG_NS)
    page2.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._read_current_ns(page2) == [], cs._read_current_ns(page2)
    print("  OK: NS registrar -> [] (khong bo qua).", flush=True)
    b2.close()


def test_d(p):
    print("TEST D: link records thang + xoa het record cu (DELETE) + them moi...", flush=True)
    b = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page = b.new_page()
    route_cf(page)
    page.goto(f"https://dash.cloudflare.com/{ACCT}")
    out = cs.cloudflare_add_records(page, "elvellia.com",
                                    [{"type": "A", "name": "@", "content": "23.227.38.65", "proxied": True}])
    assert out == "1/1 records", out
    assert page.title() == "record-saved", "record chua duoc luu"
    assert page.locator("table tbody input[type='checkbox']").count() == 0, "record cu chua xoa het"
    print("  OK: xoa 8 record cu, them 1 record moi.", flush=True)
    b.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        test_a(p)
        test_b(p)
        test_c(p)
        test_d(p)
    print("ALL TESTS PASSED")
