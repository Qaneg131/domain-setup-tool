#!/usr/bin/env python3
"""
App desktop: Namecheap -> Cloudflare BẰNG CHROME ĐÃ ĐĂNG NHẬP.
Mở bằng cách double-click run-app.bat (hoặc: python app.py).

KHÔNG cần API key / token, không nhập mật khẩu.
KHÔNG cần mở web http://127.0.0.1:8000, không cần đóng Chrome.
2 nút, làm theo thứ tự:
- Nút 1 "Mở tab đăng nhập": mở Chrome (bản copy profile) với 2 tab
  đăng nhập Cloudflare + Namecheap rồi DỪNG — không chạm vào trang nữa,
  bạn tự đăng nhập trong 2 tab đó, không bị reload làm gián đoạn.
- Nút 2 "Chạy tiếp": sau khi đăng nhập xong cả 2, bấm để tool nối vào
  Chrome đó và chạy hàng loạt tới bước 3 (tạo zone + đổi NS).
- Chạy xong Chrome vẫn mở để bạn kiểm tra lại, kết quả hiện trong bảng
  bên dưới, log đầy đủ ở ô cuối.
- Đăng nhập CHỈ CẦN 1 LẦN: tool giữ profile riêng (thư mục
  chrome-profile-tool/) nên cookie đăng nhập còn nguyên giữa các lần chạy.
  Lần sau mở app bấm nút 2 là chạy luôn, không cần nút 1 nữa — trừ khi
  phiên đăng nhập hết hạn, lúc đó đăng nhập lại 1 lần trong Chrome của tool.
  Nút "Reset profile tool" để xoá hẳn profile đã lưu (đăng xuất hết).
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
    from chrome_setup import default_profile_dir, list_chrome_profiles, tool_profile_dir, tool_version
    _PROFILES, _LAST_USED = list_chrome_profiles(default_profile_dir())
    _VERSION = tool_version()
except Exception:
    _PROFILES, _LAST_USED = [], None
    tool_profile_dir = None
    _VERSION = "?"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Domain Setup — Namecheap → Cloudflare (bản {_VERSION})")
        self.geometry("780x800")
        self.proc = None
        self.thread = None
        self.msg_queue = queue.Queue()

        ttk.Label(self, text="Domain Setup — Namecheap → Cloudflare",
                  font=("Segoe UI", 14, "bold")).pack(padx=12, pady=(12, 2), anchor="w")
        # Hiện rõ bản đang chạy ngay trên cửa sổ để khỏi nhầm bản cũ
        # với bản mới (nhiều lần user chạy lại vẫn là bản cũ vì chưa
        # pull mà không biết — đối chiếu với số mình báo trong chat).
        ttk.Label(self, text=f"Bản đang chạy: {_VERSION}",
                  foreground="gray").pack(padx=12, pady=(0, 2), anchor="w")
        ttk.Label(self, text="Bước 1: bấm nút 1 để mở 2 tab đăng nhập, đăng nhập xong "
                  "Cloudflare + Namecheap. Bước 2: bấm nút 2 để chạy hàng loạt "
                  "tới bước 3 (tạo zone + đổi NS). Nếu đã nhập record ở dưới thì "
                  "chạy tiếp Phase 2 (xóa record cũ rồi thêm mới); nếu NS đã set "
                  "sẵn thì đi thẳng tới thêm record. Không cần key, không cần đóng Chrome.",
                  wraplength=740).pack(padx=12, pady=2, anchor="w")

        ttk.Label(self, text="Full domain (mỗi dòng 1 domain):",
                  font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(10, 2), anchor="w")
        self.domains_box = scrolledtext.ScrolledText(self, height=7, font=("Consolas", 11))
        self.domains_box.pack(padx=12, fill="x")
        self.domains_box.insert("1.0", "elvellia.com\nwelvella.com")

        # Phase 2 — DNS records: 3 ô (Type / Name / Content), ô nào trống
        # Content thì bỏ qua. Nhập xong bấm nút 2 — nếu NS đã set sẵn ở
        # Namecheap thì tool đi thẳng tới thêm record, khỏi đổi NS lại.
        ttk.Label(self, text="Phase 2 — DNS records (mỗi dòng 1 record, để trống Content là bỏ qua):",
                  font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(10, 2), anchor="w")
        self.rec_rows = []
        for i in range(3):
            rrow = ttk.Frame(self)
            rrow.pack(padx=12, pady=2, fill="x")
            ttk.Label(rrow, text=f"Record {i+1}:").pack(side="left")
            type_box = ttk.Combobox(rrow, width=8,
                                    values=["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA"])
            type_box.pack(side="left", padx=(6, 0))
            name_e = ttk.Entry(rrow, width=18)
            name_e.pack(side="left", padx=(6, 0))
            content_e = ttk.Entry(rrow, width=34)
            content_e.pack(side="left", padx=(6, 0), fill="x", expand=True)
            self.rec_rows.append((type_box, name_e, content_e))
        ttk.Label(self, text="Type: A/AAAA/CNAME/TXT/MX... — Name: @ là tên domain, www là www — "
                  "Content: IP hoặc đích đến. Trước khi thêm, tool xóa hết record cũ "
                  "(chọn hết → gõ DELETE → xóa) rồi mới thêm mới.",
                  wraplength=740, foreground="#666").pack(padx=12, anchor="w")

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
        self.profile_note = ttk.Label(self, text="", wraplength=740, foreground="#a06a35")
        self.profile_note.pack(padx=12, anchor="w")
        self.profile_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_profile_note())
        self.refresh_profile_note()

        row = ttk.Frame(self)
        row.pack(padx=12, pady=8, fill="x")
        self.dry_run = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Dry-run (xem trước, không đổi gì thật)",
                        variable=self.dry_run).pack(side="left")
        self.reset_btn = ttk.Button(row, text="Reset profile tool", command=self.on_reset)
        self.reset_btn.pack(side="left", padx=(10, 0))
        self.prepare_btn = ttk.Button(row, text="1. Mở tab đăng nhập", command=self.on_prepare)
        self.prepare_btn.pack(side="right", padx=(6, 0))
        self.run_btn = ttk.Button(row, text="2. Chạy tiếp", command=self.on_run)
        self.run_btn.pack(side="right")
        self.status = ttk.Label(self, text="Sẵn sàng — bấm nút 1 để mở tab đăng nhập trước.",
                                foreground="#a06a35")
        self.status.pack(padx=12, anchor="w")

        ttk.Label(self, text="Kết quả:", font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(8, 2), anchor="w")
        cols = ("domain", "status", "nameservers", "records", "error")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=6)
        for c, w, t in (("domain", 140, "Domain"), ("status", 110, "Status"),
                        ("nameservers", 170, "Nameservers"), ("records", 130, "Records"),
                        ("error", 170, "Lỗi")):
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

    def _profile_args(self):
        chosen = self.profile_map.get(self.profile_box.get())
        return ["--profile-dir", chosen] if chosen else []

    def refresh_profile_note(self):
        """Cho biết hồ sơ đang chọn đã có đăng nhập lưu sẵn trong profile
        tool chưa — có rồi thì lần sau bấm nút 2 là chạy luôn."""
        if tool_profile_dir is None:
            return
        chosen = self.profile_map.get(self.profile_box.get())
        if not chosen:
            self.profile_note.configure(text="")
            return
        d = tool_profile_dir(chosen)
        if (d / "Local State").exists() or (d / "Default").exists():
            self.profile_note.configure(
                text="✓ Hồ sơ này đã lưu đăng nhập trong tool — lần sau bấm nút 2 là chạy luôn, không cần đăng nhập lại.")
        else:
            self.profile_note.configure(
                text="Chưa lưu đăng nhập cho hồ sơ này — đăng nhập 1 lần ở bước 1, các lần sau không cần nữa.")

    def _start(self, cmd, mode, status_text):
        self.current_mode = mode
        self.prepare_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.reset_btn.configure(state="disabled")
        self.status.configure(text=status_text)
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.thread = threading.Thread(target=self.run_worker, args=(cmd,), daemon=True)
        self.thread.start()

    def on_reset(self):
        # Xoá profile tool đã lưu (đăng xuất hết) — dùng khi phiên đăng nhập
        # lưu sẵn bị hỏng/hết hạn mà tool cứ kẹt ở trang đăng nhập.
        if not messagebox.askyesno("Reset profile tool",
                                   "Xoá hẳn đăng nhập đã lưu trong tool?\n"
                                   "Lần chạy sau sẽ copy mới từ hồ sơ Chrome thật "
                                   "và có thể phải đăng nhập lại 1 lần."):
            return
        cmd = [sys.executable, str(BASE_DIR / "chrome_setup.py"), "--reset-profile"]
        cmd += self._profile_args()
        self._start(cmd, "reset", "Đang xoá profile tool đã lưu...")

    def on_prepare(self):
        # Nút 1: chỉ mở Chrome với 2 tab đăng nhập rồi dừng — không chạm
        # vào trang, không reload, để bạn tự đăng nhập thoải mái.
        cmd = [sys.executable, str(BASE_DIR / "chrome_setup.py"), "--prepare"]
        cmd += self._profile_args()
        self._start(cmd, "prepare", "Đang mở Chrome với 2 tab đăng nhập...")

    def on_run(self):
        # Nút 2: nối vào Chrome đã mở ở bước 1 và chạy hàng loạt.
        # Bấm luôn không qua bước 1 cũng được — tool tự mở Chrome như cũ.
        domains = [l.strip().lower() for l in self.domains_box.get("1.0", "end").splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        if not domains:
            messagebox.showwarning("Thiếu domain", "Dán domain vào ô trên, mỗi dòng 1 domain.")
            return
        # Phase 2: gom record từ 3 ô (ô nào Content trống thì bỏ qua).
        rec_args = []
        for type_box, name_e, content_e in self.rec_rows:
            rtype = type_box.get().strip().upper()
            name = name_e.get().strip()
            content = content_e.get().strip()
            if not content:
                continue
            if not rtype:
                rtype = "A"
            if not name:
                name = "@"
            rec_args.append(f"{rtype}:{name}:{content}")
        for item in self.tree.get_children():
            self.tree.delete(item)
        tmpdir = Path(tempfile.mkdtemp(prefix="domain-app-"))
        dom_file = tmpdir / "domains.txt"
        dom_file.write_text("\n".join(domains) + "\n", encoding="utf-8")
        # Có nhập record thì chạy luôn Phase 2 (phase "all": NS đã set sẵn
        # thì bỏ qua đổi NS, đi thẳng tới thêm record); không nhập thì
        # chỉ chạy Phase 1 như cũ.
        cmd = [sys.executable, str(BASE_DIR / "chrome_setup.py"),
               "--domains-file", str(dom_file),
               "--phase", "all" if rec_args else "1", "--keep-open"]
        cmd += self._profile_args()
        for r in rec_args:
            cmd += ["--record", r]
        if self.dry_run.get():
            cmd.append("--dry-run")
        self._start(cmd, "run", "Đang chạy bằng Chrome, xem cửa sổ Chrome để theo dõi...")

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
        mode = getattr(self, "current_mode", "run")
        if mode == "reset":
            self.status.configure(text="Đã xoá profile tool — lần chạy sau sẽ copy mới "
                                       "từ hồ sơ Chrome thật." if rc == 0
                                  else "Xoá profile tool lỗi — xem log bên dưới.")
            self.prepare_btn.configure(state="normal")
            self.run_btn.configure(state="normal")
            self.reset_btn.configure(state="normal")
            self.refresh_profile_note()
            return
        if mode == "prepare":
            if rc == 0:
                self.status.configure(text="Đã mở Chrome — đăng nhập xong Cloudflare + "
                                           "Namecheap rồi bấm nút 2 (Chạy tiếp).")
            else:
                self.status.configure(text="Mở tab đăng nhập lỗi — xem log bên dưới.")
            self.prepare_btn.configure(state="normal")
            self.run_btn.configure(state="normal")
            self.reset_btn.configure(state="normal")
            self.refresh_profile_note()
            return
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
                            r.get("records", ""), r.get("error", "")))
            except Exception as e:
                self.log(f"Không đọc được báo cáo CSV: {e}\n")
        if total:
            self.status.configure(text=f"Xong: {ok_n}/{total} domain OK. "
                                       "Chrome vẫn mở để bạn kiểm tra lại.")
        elif rc == 0:
            self.status.configure(text="Chạy xong nhưng chưa có báo cáo — xem log bên dưới.")
        else:
            self.status.configure(text="Chạy lỗi — xem log bên dưới.")
        self.prepare_btn.configure(state="normal")
        self.run_btn.configure(state="normal")
        self.reset_btn.configure(state="normal")
        self.refresh_profile_note()

    def on_close(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
