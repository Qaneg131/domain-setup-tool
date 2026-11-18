#!/usr/bin/env python3
"""
App desktop: Namecheap -> Cloudflare BẰNG CHROME ĐÃ ĐĂNG NHẬP.
Mở bằng cách double-click run-app.bat (hoặc: python app.py).

KHÔNG cần API key / token, không nhập mật khẩu.
KHÔNG cần mở web http://127.0.0.1:8000, không cần đóng Chrome:
- Dán domain vào ô, bấm "Chạy bằng Chrome".
- App tự copy profile Chrome sang thư mục tạm rồi mở Chrome từ bản copy
  (vẫn đăng nhập sẵn Cloudflare + Namecheap), tab làm việc hiện lên trước
  để bạn xem trực tiếp từng bước. Chrome thật của bạn cứ mở bình thường.
- Chạy xong Chrome vẫn mở để bạn kiểm tra lại, kết quả hiện trong bảng
  bên dưới, log đầy đủ ở ô cuối. Bản copy profile tạm được xoá sau 1 ngày.
"""
import csv
import glob
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

BASE_DIR = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Đọc danh sách hồ sơ Chrome để user chọn đúng hồ sơ đã đăng nhập.
try:
    sys.path.insert(0, str(BASE_DIR))
    from chrome_setup import default_profile_dir, list_chrome_profiles
    _PROFILES, _LAST_USED = list_chrome_profiles(default_profile_dir())
except Exception:
    _PROFILES, _LAST_USED = [], None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Domain Setup — Namecheap → Cloudflare")
        self.geometry("780x680")
        self.proc = None
        self.thread = None
        self.msg_queue = queue.Queue()

        ttk.Label(self, text="Domain Setup — Namecheap → Cloudflare",
                  font=("Segoe UI", 14, "bold")).pack(padx=12, pady=(12, 2), anchor="w")
        ttk.Label(self, text="Dán full domain, chạy hàng loạt tới bước 3 (tạo zone + đổi NS). "
                  "Không cần key, không cần đóng Chrome — Chrome thật cứ mở bình thường.",
                  wraplength=740).pack(padx=12, pady=2, anchor="w")

        ttk.Label(self, text="Full domain (mỗi dòng 1 domain):",
                  font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(10, 2), anchor="w")
        self.domains_box = scrolledtext.ScrolledText(self, height=7, font=("Consolas", 11))
        self.domains_box.pack(padx=12, fill="x")
        self.domains_box.insert("1.0", "elvellia.com\nwelvella.com")

        # Chọn hồ sơ Chrome: trước đây tool luôn lấy 'Default' nên mở lộn hồ sơ
        # nếu bạn đăng nhập Cloudflare/Namecheap ở hồ sơ khác (vd 'Profile 1').
        prof_row = ttk.Frame(self)
        prof_row.pack(padx=12, pady=(8, 0), fill="x")
        ttk.Label(prof_row, text="Hồ sơ Chrome:").pack(side="left")
        self.profile_map = {f"{name} ({d})": d for d, name in _PROFILES}
        self.profile_box = ttk.Combobox(prof_row, state="readonly", width=40,
                                        values=list(self.profile_map.keys()))
        self.profile_box.pack(side="left", padx=(6, 0))
        if _PROFILES:
            default_key = next((k for k, d in self.profile_map.items() if d == _LAST_USED),
                               list(self.profile_map.keys())[0])
            self.profile_box.set(default_key)
        else:
            self.profile_box.set("Default (không tìm thấy hồ sơ — sẽ dùng Default)")
            self.profile_box.configure(state="disabled")
        ttk.Label(self, text="Chọn đúng hồ sơ bạn hay dùng để đăng nhập Cloudflare / Namecheap. "
                  "Sai hồ sơ thì Chrome mở lên sẽ không đăng nhập sẵn.",
                  wraplength=740, foreground="#666").pack(padx=12, anchor="w")

        row = ttk.Frame(self)
        row.pack(padx=12, pady=8, fill="x")
        self.dry_run = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Dry-run (xem trước, không đổi gì thật)",
                        variable=self.dry_run).pack(side="left")
        self.run_btn = ttk.Button(row, text="Chạy bằng Chrome", command=self.on_run)
        self.run_btn.pack(side="right")
        self.status = ttk.Label(self, text="Sẵn sàng.", foreground="#a06a35")
        self.status.pack(padx=12, anchor="w")

        ttk.Label(self, text="Kết quả:", font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(8, 2), anchor="w")
        cols = ("domain", "status", "nameservers", "error")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=6)
        for c, w, t in (("domain", 170, "Domain"), ("status", 110, "Status"),
                        ("nameservers", 230, "Nameservers"), ("error", 230, "Lỗi")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.pack(padx=12, fill="x")

        ttk.Label(self, text="Log:", font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(8, 2), anchor="w")
        self.log_box = scrolledtext.ScrolledText(self, height=12, font=("Consolas", 10),
                                                 state="disabled")
        self.log_box.pack(padx=12, pady=(0, 12), fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(150, self.poll_queue)

    def log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "done":
                    self.on_done(payload)
        except queue.Empty:
            pass
        self.after(150, self.poll_queue)

    def on_run(self):
        domains = [l.strip().lower() for l in self.domains_box.get("1.0", "end").splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        if not domains:
            messagebox.showwarning("Thiếu domain", "Dán domain vào ô trên, mỗi dòng 1 domain.")
            return
        self.run_btn.configure(state="disabled")
        self.status.configure(text="Đang chạy bằng Chrome, xem cửa sổ Chrome để theo dõi...")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        tmpdir = Path(tempfile.mkdtemp(prefix="domain-app-"))
        dom_file = tmpdir / "domains.txt"
        dom_file.write_text("\n".join(domains) + "\n", encoding="utf-8")
        cmd = [sys.executable, str(BASE_DIR / "chrome_setup.py"),
               "--domains-file", str(dom_file), "--phase", "1", "--keep-open"]
        chosen = self.profile_map.get(self.profile_box.get())
        if chosen:
            cmd += ["--profile-dir", chosen]
        if self.dry_run.get():
            cmd.append("--dry-run")
        self.thread = threading.Thread(target=self.run_worker, args=(cmd,), daemon=True)
        self.thread.start()

    def run_worker(self, cmd):
        before = set(glob.glob(str(BASE_DIR / "reports" / "chrome_setup_*.csv")))
        try:
            env = dict(os.environ)
            env["PYTHONUTF8"] = "1"
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace",
                                         env=env, cwd=str(BASE_DIR))
            for line in self.proc.stdout:
                self.msg_queue.put(("log", line))
            rc = self.proc.wait()
            after = sorted(set(glob.glob(str(BASE_DIR / "reports" / "chrome_setup_*.csv"))) - before)
            self.msg_queue.put(("done", (rc, after[-1] if after else None)))
        except Exception as e:
            self.msg_queue.put(("log", f"LỖI chạy tool: {e}\n"))
            self.msg_queue.put(("done", (1, None)))
        finally:
            self.proc = None

    def on_done(self, result):
        rc, csv_path = result
        ok_n = 0
        total = 0
        if csv_path:
            try:
                with open(csv_path, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        total += 1
                        st = r.get("status", "")
                        if st != "failed":
                            ok_n += 1
                        self.tree.insert("", "end", values=(
                            r.get("domain", ""), st, r.get("nameservers", ""),
                            r.get("error", "")))
            except Exception as e:
                self.log(f"Không đọc được báo cáo CSV: {e}\n")
        if total:
            self.status.configure(text=f"Xong: {ok_n}/{total} domain OK. "
                                       "Chrome vẫn mở để bạn kiểm tra lại.")
        elif rc == 0:
            self.status.configure(text="Chạy xong nhưng chưa có báo cáo — xem log bên dưới.")
        else:
            self.status.configure(text="Chạy lỗi — xem log bên dưới.")
        self.run_btn.configure(state="normal")

    def on_close(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
