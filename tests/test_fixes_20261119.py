#!/usr/bin/env python3
"""Test cac fix 2026-11-19 tren Chromium that (mock HTML + route):
(a) namecheap_set_ns: xac minh sau luu POLL toi 20s, check innerText (chu
    hien thi) thay vi chi page.content() — mock cho NS hien sau 4s, ca
    truong hop NS tach thanh nhieu <span>.
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
<select><option selected>Custom DNS</option><option>Namecheap BasicDNS</option></select>
<div>{NS1}</div><div>{NS2}</div>
<div class="inline-actions"><a class="save" href="#" onclick="sessionStorage.setItem('saved','1');return false">Save</a>
<a class="cancel" href="#">Cancel</a></div>
<div>ADD NAMESERVER</div></body></html>"""

MANAGE_BASIC = """<html><body><h2>Nameservers</h2>
<select><option selected>Namecheap BasicDNS</option><option>Custom DNS</option></select>
</body></html>"""

MANAGE_CUSTOM_EMPTY = """<html><body><h2>Nameservers</h2>
<select><option selected>Custom DNS</option><option>Namecheap BasicDNS</option></select>
<div>ADD NAMESERVER</div></body></html>"""

# Cap NS khac (Cloudflare doi ten NS theo zone) — check khong fix cung brit/langston.
MANAGE_OTHER_NS = """<html><body><h2>Nameservers</h2>
<select><option selected>Custom DNS</option><option>Namecheap BasicDNS</option></select>
<div>ada.ns.cloudflare.com</div><div>bob.ns.cloudflare.com</div></body></html>"""

# 2 dong sau Custom DNS co noi dung bat ky (khong phai dang *.ns.cloudflare.com)
# — theo luat user: thay 3 dong la du, khong fix cung ten NS.
MANAGE_OTHER_CONTENT = """<html><body><h2>Nameservers</h2>
<select><option selected>Custom DNS</option><option>Namecheap BasicDNS</option></select>
<div>dns1.myprovider.net</div><div>dns2.myprovider.net</div></body></html>"""

# Dong chu thuong xen vao giua Custom DNS va 2 dong NS (loi chay that 10:06:
# lay nham 'redirect domain' / 'alert') — chi lay token hostname.
MANAGE_POLLUTED = f"""<html><body><h2>Nameservers</h2>
<select><option selected>Custom DNS</option><option>Namecheap BasicDNS</option></select>
<div>redirect domain</div><div>alert</div>
<div>{NS1}</div><div>{NS2}</div></body></html>"""

# Dau cham cuoi cau ('account.' / 'default.' trong doan chu
# '...your Namecheap account. ...to Namecheap default.') — loi chay that
# 10:21 tra ve 'account.' / 'default.' thay vi 2 NS that. Chi nhan
# hostname co TLD that, dau cham cuoi cau khong phai hostname.
MANAGE_POLLUTED2 = f"""<html><body><h2>Nameservers</h2>
<select><option selected>Custom DNS</option><option>Namecheap BasicDNS</option></select>
<div>You can create redirects via your DNS provider or your Namecheap account.</div>
<div>To perform this function you must first change your nameservers to Namecheap default.</div>
<div>{NS1}</div><div>{NS2}</div></body></html>"""

# NS hien sau 4s NHUNG tach thanh nhieu <span> (trang that render kieu nay):
# page.content() khong chua chuoi lien tuc 'brit.ns.cloudflare.com' trong
# khi innerText (chu hien thi) van co — loi chay that 10:21 o welvella:
# anh chup da thay 2 NS nhung check HTML tho van truot.
MANAGE_DELAYED_SPLIT = f"""<html><body>
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
  document.getElementById('saved-ns').innerHTML = '<span>brit</span><span>.ns.cloudflare.com</span> <span>langston</span><span>.ns.cloudflare.com</span>';
}}, 4000);</script></body></html>"""

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

# Bang records THAT cua Cloudflare (HTML user gui 2026-11-19): div grid
# (role=row/columnheader), checkbox la <span role='checkbox'> (Select All
# co aria-label='Select All'), input checkbox that bi an — KHONG phai <table>.
RECORD_ROWS = "".join(
    '<div role="row"><div role="cell"><span role="checkbox" aria-checked="false">✓</span></div>'
    '<div role="cell">elvellia.com</div><div role="cell">A</div>'
    '<div role="cell">192.64.119.2</div></div>'
    for _ in range(8))

CF_RECORDS = f"""<html><body><h1>DNS records for elvellia.com</h1>
<p id="count-text">You have used 8 of 200 available DNS records in this domain.</p>
<button onclick="document.getElementById('rec-form').style.display=''">Add record</button>
<div role="table">
<div role="row" aria-rowindex="1">
<div role="columnheader"><span role="checkbox" tabindex="0" aria-checked="false" aria-label="Select All" data-kumo-component="Checkbox" onclick="selectAll(this)">✓</span></div>
<div role="columnheader">Name</div><div role="columnheader">Type</div><div role="columnheader">Content</div>
</div>
<div id="rec-rows">{RECORD_ROWS}</div>
</div>
<button id="del-n" style="display:none"
 onclick="document.getElementById('del-dialog').style.display=''">Delete 8 records</button>
<div role="dialog" id="del-dialog" style="display:none">
<h2>Delete DNS records</h2><p>Type in "DELETE" to confirm</p>
<input type="text" id="del-input">
<button onclick="document.getElementById('del-dialog').style.display='none'">Cancel</button>
<button onclick="if (document.getElementById('del-input').value === 'DELETE') {{ document.getElementById('rec-rows').innerHTML=''; document.getElementById('count-text').textContent='You have used 0 of 200 available DNS records in this domain.'; document.getElementById('del-dialog').style.display='none'; }}">Delete</button>
</div>
<form id="rec-form" style="display:none">
<label>Type <select><option>A</option><option>AAAA</option><option>CNAME</option><option>TXT</option><option>MX</option></select></label>
<label>Name <input type="text" id="name-in" placeholder="Use @ for root" oninput="document.getElementById('save-btn').disabled = !this.value"></label>
<label>IPv4 address <input type="text"></label>
<button type="button" id="save-btn" disabled onclick="document.title='record-saved'">Save</button>
</form>
<script>
function selectAll(el){{ document.querySelectorAll('#rec-rows [role=checkbox]').forEach(c=>c.setAttribute('aria-checked','true')); el.setAttribute('aria-checked','true'); document.getElementById('del-n').style.display=''; }}
</script></body></html>"""


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
    print("TEST A: xac minh sau luu (NS hien sau 4s, poll 20s, check innerText)...", flush=True)
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
    # NS tach thanh nhieu <span>: page.content() khong chua chuoi lien tuc,
    # chi innerText (chu hien thi) moi thay — loi chay that 10:21 o welvella.
    b2 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page2 = b2.new_page()
    route_nc(page2, MANAGE_DELAYED_SPLIT)
    out2 = cs.namecheap_set_ns(page2, "elvellia.com", [NS1, NS2],
                               manage_url="https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert out2 == "saved", out2
    assert NS1 not in page2.content(), "mock split khong dung: content() van chua NS lien tuc"
    print("  OK: NS tach span van xac minh duoc qua innerText.", flush=True)
    b2.close()


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
    print("TEST C: _ns_already_set (dropdown + 3 dong)...", flush=True)
    b = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page = b.new_page()
    route_nc(page, MANAGE_CF_NS)
    page.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._ns_already_set(page) == [NS1, NS2], cs._ns_already_set(page)
    print("  OK: Custom DNS + 2 dong NS -> da setup.", flush=True)
    b.close()
    b2 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page2 = b2.new_page()
    route_nc(page2, MANAGE_BASIC)
    page2.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._ns_already_set(page2) == [], cs._ns_already_set(page2)
    print("  OK: Namecheap BasicDNS -> chua setup.", flush=True)
    b2.close()
    b3 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page3 = b3.new_page()
    route_nc(page3, MANAGE_CUSTOM_EMPTY)
    page3.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._ns_already_set(page3) == [], cs._ns_already_set(page3)
    print("  OK: Custom DNS nhung khong co dong NS -> chua setup.", flush=True)
    b3.close()
    # Cap NS khac ten (ada/bob): van nhan, khong fix cung brit/langston.
    b4 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page4 = b4.new_page()
    route_nc(page4, MANAGE_OTHER_NS)
    page4.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._ns_already_set(page4) == ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"], cs._ns_already_set(page4)
    print("  OK: cap NS ten khac (ada/bob) van nhan — khong fix cung ten.", flush=True)
    b4.close()
    # 2 dong sau Custom DNS co noi dung bat ky (khong phai dang CF NS): van tinh la da setup.
    b5 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page5 = b5.new_page()
    route_nc(page5, MANAGE_OTHER_CONTENT)
    page5.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._ns_already_set(page5) == ["dns1.myprovider.net", "dns2.myprovider.net"], cs._ns_already_set(page5)
    print("  OK: 2 dong sau co noi dung bat ky van tinh la da setup.", flush=True)
    b5.close()
    # Dong chu thuong xen vao ('redirect domain' / 'alert'): bo qua, chi lay 2 hostname.
    b6 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page6 = b6.new_page()
    route_nc(page6, MANAGE_POLLUTED)
    page6.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._ns_already_set(page6) == [NS1, NS2], cs._ns_already_set(page6)
    print("  OK: dong chu xen vao bi bo qua, lay dung 2 NS.", flush=True)
    b6.close()
    # Dau cham cuoi cau ('account.' / 'default.'): khong phai hostname,
    # chi nhan hostname co TLD that — loi chay that 10:21.
    b7 = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    page7 = b7.new_page()
    route_nc(page7, MANAGE_POLLUTED2)
    page7.goto("https://ap.www.namecheap.com/domains/domaincontrolpanel/elvellia.com/domain")
    assert cs._ns_already_set(page7) == [NS1, NS2], cs._ns_already_set(page7)
    print("  OK: 'account.' / 'default.' bi loai, lay dung 2 NS.", flush=True)
    b7.close()


def test_e(p):
    print("TEST E: setup_one — NS da setup thi nhay thang Phase 2...", flush=True)
    from types import SimpleNamespace
    b = p.chromium.launch(headless=True, executable_path=EXE, args=["--no-sandbox"])
    nc_page = b.new_page()
    cf_page = b.new_page()
    route_nc(nc_page, MANAGE_CF_NS)
    route_cf(cf_page)
    args = SimpleNamespace(phase="all", dry_run=False, only_cloudflare=False,
                           only_namecheap=False, record=["A:@:23.227.38.65"],
                           preset=None, records_file=None)
    res = cs.setup_one(nc_page, cf_page, "elvellia.com", args, cs.load_presets(), {})
    assert res["status"] == "done", res
    assert res["records"] == "1/1 records", res
    assert res["nameservers"] == f"{NS1}, {NS2}", res
    # Chung to Phase 1 bi bo qua hoan toan: nut save o Namecheap khong
    # duoc bam, trang Connect your domain o Cloudflare khong duoc mo.
    assert nc_page.evaluate("() => sessionStorage.getItem('saved')") is None, "set_ns da chay (khong duoc chay)"
    assert cf_page.evaluate("() => sessionStorage.getItem('tried')") is None, "add_site da chay (khong duoc chay)"
    print("  OK: bo qua add_site + set_ns, di thang records, done.", flush=True)
    b.close()


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
    assert "You have used 0 of 200" in page.content(), "record cu chua xoa het"
    assert page.locator("#rec-rows [role='row']").count() == 0, "record cu chua xoa het"
    # O Name phai duoc dien '@' that (khong de trong) thi nut Save moi mo.
    assert page.locator("#name-in").input_value() == "@", "o Name khong duoc dien '@'"
    print("  OK: xoa 8 record cu, them 1 record moi (Name='@').", flush=True)
    b.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        test_a(p)
        test_b(p)
        test_c(p)
        test_d(p)
        test_e(p)
    print("ALL TESTS PASSED")
