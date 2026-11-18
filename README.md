# Tool tự động setup domain: Namecheap → Cloudflare (bằng Chrome đã đăng nhập)

**Không cần API key / token. Không nhập mật khẩu vào tool.**
Tool điều khiển Chrome ở máy bạn — Chrome đó đã đăng nhập sẵn Cloudflare
+ Namecheap — và tự click thay bạn, y như bạn làm tay.

## 0. App desktop (khuyên dùng) — double-click là chạy

```bash
# Lần đầu, trên máy bạn:
git clone https://github.com/Qaneg131/domain-setup-tool.git
cd domain-setup-tool
pip install -r requirements.txt
playwright install chromium   # 1 lần duy nhất, để có driver Playwright
```

Rồi double-click **`run-app.bat`** (hoặc chạy `python app.py`).

App hiện 1 cửa sổ: dán full domain, làm 2 bước, xem log + kết quả ngay
trong app. Không cần mở web, không cần đóng Chrome:
- **Nút 1 "Mở tab đăng nhập":** mở Chrome (bản copy profile) với 2 tab
  đăng nhập Cloudflare + Namecheap rồi DỪNG — tool không chạm vào trang
  nữa, bạn tự đăng nhập trong 2 tab đó, không bị reload làm gián đoạn.
  (Bản cũ chờ đăng nhập bằng cách load lại trang mỗi ~7 giây nên bạn
  đang gõ mật khẩu / làm xác minh thì bị reset — đã sửa, giờ chỉ đọc
  nội dung trang khi chờ, không load lại.)
- **Nút 2 "Chạy tiếp":** sau khi đăng nhập xong cả 2 tab, bấm để tool
  nối vào Chrome đó và chạy hàng loạt tới bước 3 (tạo zone + đổi NS).
  Bấm luôn nút 2 không qua nút 1 cũng được — tool tự mở Chrome như cũ.
- App tự copy profile Chrome sang thư mục tạm rồi mở Chrome từ bản copy
  đó. Cookie Chrome mã hoá gắn với tài khoản Windows của chính máy đó,
  nên bản copy mở lên vẫn đăng nhập sẵn như Chrome thật. Chrome thật của
  bạn cứ mở bình thường. Chạy xong Chrome vẫn mở để bạn kiểm tra lại.
- Nếu bản copy không mang được đăng nhập, cứ đăng nhập trong cửa sổ
  Chrome đó — đăng nhập xong là tool tự chạy tiếp, không cần bấm gì.
  Quá 5 phút chưa đăng nhập thì tool báo lỗi.
- Tab làm việc mở riêng và đưa lên trước để bạn xem trực tiếp từng bước.
- **Mở lộn hồ sơ?** Nếu bạn có nhiều hồ sơ Chrome (vd vừa "Default" vừa
  "Profile 1"), chọn đúng hồ sơ hay dùng để đăng nhập Cloudflare /
  Namecheap ở ô **Hồ sơ Chrome** trong app (mặc định app chọn hồ sơ bạn
  dùng gần nhất). Sai hồ sơ thì Chrome mở lên sẽ không đăng nhập sẵn —
  lúc đó cứ đăng nhập trong cửa sổ đó, tool sẽ tự chạy tiếp.

## 1. Web nội bộ (cách cũ, vẫn dùng được)

Web nội bộ chạy ở `http://127.0.0.1:8000`, chỉ ở máy bạn, không public.

```bash
git clone https://github.com/Qaneg131/domain-setup-tool.git
cd domain-setup-tool
pip install -r requirements.txt
playwright install chromium   # 1 lần duy nhất, để có driver Playwright
python3 web_app.py
# mở http://127.0.0.1:8000
```

Trong web (không còn mục key nữa, chỉ còn Giai đoạn 1):
- Dán full domain vào ô lớn, chạy hàng loạt tới bước 3
  (Namecheap Domain List → ô Search cạnh Filters → MANAGE →
  Cloudflare Add site Free → lấy 2 nameserver → quay lại trang Manage →
  Custom DNS → dán 2 NS → lưu).
- Kết quả hiện ngay tại chỗ dưới form, không nhảy trang khác.
- **Giai đoạn 2** (thêm DNS record) tạm ẩn khỏi web — khi cần thì chạy
  bằng dòng lệnh, xem mục 2 (`--phase 2`, 3 ô Type/Name/Content,
  ô nào trống thì bỏ qua; hỗ trợ A, AAAA, CNAME, TXT, MX).
- Bấm **Chạy bằng Chrome**: tool tự mở Chrome từ bản copy profile (vẫn
  đăng nhập sẵn) và tự click. Bạn không cần thao tác gì, chỉ ngồi xem.
  Chrome thật cứ mở bình thường, không cần đóng. Chạy xong Chrome vẫn mở
  để bạn kiểm tra lại.

Nếu phiên đăng nhập hết hạn (Cloudflare/Namecheap bắt đăng nhập lại),
tool sẽ mở tab và đợi bạn đăng nhập trong cửa sổ Chrome đó — đăng nhập
xong là tool tự chạy tiếp, không cần bấm gì. Quá 5 phút chưa đăng nhập
thì tool báo lỗi.

---

Tự động làm tay 5 bước cho mỗi domain:
1. **Cloudflare:** Add site (Free) → lấy 2 nameserver được cấp
2. **Cloudflare:** tạo/cập nhật DNS records theo 3 ô bạn nhập — chạy lại
   lần 2, record đã có thì bỏ qua, không tạo trùng
3. **Namecheap:** Manage domain → Nameservers → Custom DNS → dán 2 NS
   Cloudflare → bấm dấu tick xanh để lưu
4. **Verify:** hỏi DNS 1.1.1.1 xem NS đã khớp chưa
5. **Báo cáo:** ghi CSV vào `reports/chrome_setup_YYYYMMDD_HHMMSS.csv`.
   Nếu 1 bước kẹt vì giao diện đổi nút, tool chụp màn hình vào `reports/`
   rồi chạy tiếp domain khác — mở ảnh đó lên là biết nó kẹt ở đâu.

## 2. Chạy bằng dòng lệnh (không cần mở web)

```bash
# GIAI ĐOẠN 1: nhập full domain, chạy hàng loạt tới bước 3
# (không cần đóng Chrome — tool tự copy profile rồi mở Chrome từ bản copy)
python3 chrome_setup.py --domains-file domains.txt --phase 1 --dry-run
python3 chrome_setup.py --domains-file domains.txt --phase 1

# GIAI ĐOẠN 2: sau đó mới thêm record, 3 ô, ô trống bỏ qua
python3 chrome_setup.py --domains-file domains.txt --phase 2 --records-file custom-records.json
python3 chrome_setup.py --domain example.com --phase 2 --record "A:@:1.2.3.4" --record "CNAME:www:shops.myshopify.com"

# Xem trước, không đổi gì thật (luôn chạy cái này trước với domain thật đầu tiên)
python3 chrome_setup.py --domain example.com --phase 1 --dry-run

# Cả 2 giai đoạn trong 1 lần
python3 chrome_setup.py --domain example.com --phase all --preset shopify
```

Các flag khác: `--preset shopify|vps|vercel|only-ns`, `--target-ip 1.2.3.4`
(cho preset vps), `--only-cloudflare`, `--only-namecheap`, `--keep-open`.

## 3. Preset

Sửa trong `presets.yaml`: `shopify`, `vps`, `vercel`, `only-ns` có sẵn.

Chú ý preset Shopify trong file mẫu: `shops.myshopify.com` là placeholder,
phải sửa thành domain `.myshopify.com` thật.

## 4. Giới hạn cần biết

- Cloudflare: tối đa ~25 zone mới / 10 phút. Nếu tài khoản đã có >50 domain
  mà số zone pending nhiều hơn số zone active thì sẽ bị chặn thêm zone mới —
  cách xử lý là chờ các zone pending active rồi mới thêm tiếp.
- Chạy lại tool trên domain đã setup là an toàn: zone đã có thì dùng lại,
  record đã đúng thì bỏ qua, NS đã đúng thì không đổi.
- Đổi NS sẽ tắt URL forwarding / Email forwarding / Dynamic DNS của
  Namecheap cho domain đó (vì DNS giờ do Cloudflare quản). Nếu domain đang
  dùng mail ở Namecheap thì phải mang MX/TXT sang Cloudflare trước — thêm
  vào 3 ô record.
- **DNSSEC:** nếu domain đang bật DNSSEC ở Namecheap thì tắt trước, không thì
  đổi NS xong domain sẽ không resolve được.
- Giao diện Cloudflare/Namecheap thỉnh thoảng đổi nút/chữ. Tool đã dùng
  nhiều cách tìm nút dự phòng, nhưng nếu 1 bước không tìm được nút, nó sẽ
  chụp màn hình vào `reports/` và dừng ở domain đó.

## 5. Bản API cũ (chỉ dùng khi muốn chạy không cần mở Chrome)

Bản này CẦN key, giữ lại ở `setup_domain.py` + `web_app_api.py`:
- Cloudflare API Token (quyền Zone:Edit + DNS:Edit)
- Namecheap API User / API Key / Username + whitelist IP máy chạy tool
- Key chỉ lưu ở file `.env` local, đã có trong `.gitignore`, không commit
  lên git. Xem hướng dẫn lấy key trong `web_app_api.py` / `.env.example`.
- Chạy: `python3 web_app_api.py` rồi mở `http://127.0.0.1:8000`,
  hoặc `python3 setup_domain.py --domains-file domains.txt --phase 1`.
