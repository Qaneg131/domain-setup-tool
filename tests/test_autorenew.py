#!/usr/bin/env python3
"""Test che do auto-renew (bam toggle that trong Domain List):
- Domain da BAT toggle roi -> bo qua, KHONG bam (bam se TAT di).
- Domain dang TAT -> bam toggle, hop thoai Privacy hien, bam No
  (hoac Yes) -> toggle thanh BAT, tra ve ok.
- Domain khong co trong list -> failed (khong crash).
Dung mock HTML + page.route nhu test_fixes_20261119.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chrome_setup as cs
from playwright.sync_api import sync_playwright

EXE = "/home/hatch/.cache/ms-playwright/chromium_headless_shell-1255/chrome-headless-shell-linux64/chrome-headless-shell"
LIST_URL = "https://ap.www.namecheap.com/domains/list/"

LIST_HTML = """<html><body>
<h1>Domain List</h1>
<input placeholder="Search" id="q">
<table>
<tr><th>Domains</th><th>Status</th><th>Auto-Renew</th><th>Expiration</th><th></th></tr>
<tr class="domain-row" data-domain="baezmail.com">
  <td><a href="#">baezmail.com</a><div>Domain Privacy protection is ON</div></td>
  <td>ACTIVE</td>
  <td><button role="switch" aria-checked="false" class="toggle">toggle</button></td>
  <td>May 1, 2027</td><td><a href="#">MANAGE</a></td>
</tr>
<tr class="domain-row" data-domain="timlassaire.com">
  <td><a href="#">timlassaire.com</a><div>Domain Privacy protection is ON</div></td>
  <td>ACTIVE</td>
  <td><button role="switch" aria-checked="true" class="toggle on">toggle</button></td>
  <td>Dec 4, 2026</td><td><a href="#">MANAGE</a></td>
</tr>
<tr class="domain-row" data-domain="mixbudget.com">
  <td><a href="#">mixbudget.com</a></td>
  <td>ACTIVE</td>
  <td><button role="switch" aria-checked="false" class="toggle">toggle</button></td>
  <td>Jan 2, 2027</td><td><a href="#">MANAGE</a></td>
</tr>
</table>
<div id="dlg" style="display:none" role="dialog">
  <h2>Turn On Domain Auto-Renewal</h2>
  <p>You're about to turn on auto-renewal for your domain. Would you like
  to turn on auto-renewal for your domain's Domain Privacy service as well?</p>
  <button id="dlg-yes">Yes</button>
  <button id="dlg-no">No</button>
</div>
<script>
const q = document.getElementById('q');
q.addEventListener('input', () => {
  const v = q.value.trim().toLowerCase();
  document.querySelectorAll('tr.domain-row').forEach(tr => {
    tr.style.display = (!v || tr.dataset.domain.includes(v)) ? '' : 'none';
  });
});
document.querySelectorAll('button[role="switch"]').forEach(btn => {
  btn.addEventListener('click', () => {
    const on = btn.getAttribute('aria-checked') === 'true';
    if (!on) {
      window._pendingToggle = btn;
      document.getElementById('dlg').style.display = 'block';
    } else {
      btn.setAttribute('aria-checked', 'false');
    }
  });
});
document.getElementById('dlg-yes').addEventListener('click', () => {
  if (window._pendingToggle) window._pendingToggle.setAttribute('aria-checked', 'true');
  document.getElementById('dlg').style.display = 'none';
  window._privacyChoice = 'yes';
});
document.getElementById('dlg-no').addEventListener('click', () => {
  if (window._pendingToggle) window._pendingToggle.setAttribute('aria-checked', 'true');
  document.getElementById('dlg').style.display = 'none';
  window._privacyChoice = 'no';
});
</script>
</body></html>"""


def toggle_state(page, domain):
    return page.evaluate("""(domain) => {
      const tr = [...document.querySelectorAll('tr.domain-row')]
        .find(t => t.dataset.domain === domain);
      if (!tr) return null;
      return tr.querySelector('button[role="switch"]')
        .getAttribute('aria-checked') === 'true';
    }""", domain)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=EXE,
                                    args=["--no-sandbox"])
        page = browser.new_page()

        def route_handler(route):
            route.fulfill(status=200, content_type="text/html", body=LIST_HTML)

        page.route("**://ap.www.namecheap.com/**", route_handler)
        page.goto(LIST_URL)
        page.wait_for_selector("input[placeholder='Search']")

        print("TEST A: domain da BAT toggle -> bo qua, khong bam.", flush=True)
        assert toggle_state(page, "timlassaire.com") is True
        r = cs.autorenew_one(page, "timlassaire.com", enabled=True, privacy_yes=False)
        assert r["status"] == "da-bat", r
        assert toggle_state(page, "timlassaire.com") is True, "da BAT ma bi bam tat!"
        print("  OK: bo qua, toggle van BAT.", flush=True)

        print("TEST B: domain dang TAT -> bam toggle, hop thoai bam No -> BAT.", flush=True)
        assert toggle_state(page, "baezmail.com") is False
        r = cs.autorenew_one(page, "baezmail.com", enabled=True, privacy_yes=False)
        assert r["status"] == "ok", r
        assert toggle_state(page, "baezmail.com") is True, "toggle chua BAT sau khi bam"
        assert page.evaluate("window._privacyChoice") == "no", "phai bam No cho Privacy"
        print("  OK: da BAT, hop thoai bam No.", flush=True)

        print("TEST C: domain dang TAT -> bam toggle, hop thoai bam Yes -> BAT.", flush=True)
        assert toggle_state(page, "mixbudget.com") is False
        r = cs.autorenew_one(page, "mixbudget.com", enabled=True, privacy_yes=True)
        assert r["status"] == "ok", r
        assert toggle_state(page, "mixbudget.com") is True
        assert page.evaluate("window._privacyChoice") == "yes", "phai bam Yes cho Privacy"
        print("  OK: da BAT, hop thoai bam Yes.", flush=True)

        print("TEST D: domain khong co trong list -> failed (khong crash).", flush=True)
        r = cs.autorenew_one(page, "khongco.com", enabled=True, privacy_yes=False)
        assert r["status"] == "failed", r
        assert "không tìm thấy" in r["error"], r
        print("  OK: bao loi ro rang.", flush=True)

        browser.close()
    print("ALL AUTORENEW TESTS PASSED")


if __name__ == "__main__":
    main()
