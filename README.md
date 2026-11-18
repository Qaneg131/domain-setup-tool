# Tool tự động setup domain: Namecheap → Cloudflare (bằng Chrome đã đăng nhập)

**Không cần API key / token. Không nhập mật khẩu vào tool.**
Tool điều khiển Chrome ở máy bạn — Chrome đó đã đăng nhập sẵn Cloudflare
+ Namecheap — và tự click thay bạn, y như bạn làm tay.

## 0. Kéo về máy + chạy web nội bộ (khuyên dùng)

Web nội bộ chạy ở `http://127.0.0.1:8000`, chỉ ở máy bạn, không public.

```bash
# Lần đầu, trên máy bạn:
git clone https://github.com/Qaneg131/domain-setup-tool.git
cd domain-setup-tool
pip install -r requirements.txt
playwright install chromium   # 1 lần duy nhất, để có driver Playwright
python3 web_app.py
# mở http://127.0.0.1:8000
```

Trong web (không còn mục key nữa):
- **Giai đoạn 1:** dán full domain vào ô lớn, chạy hàng loạt tới bước 3
  (Cloudflare Add site Free → lấy 2 nameserver → Namecheap Manage →
  Nameservers → Custom DNS → dán 2 NS → lưu). Chưa cần record.
- **Giai đoạn 2:** sau đó mới thêm domain + DNS record, mỗi domain 3 ô
  (Type / Name / Content), ô nào để trống thì bỏ qua. Hỗ trợ A, AAAA,
  CNAME, TXT, MX. Có nút preset Shopify / VPS / Vercel.
- Bấm **Chạy bằng Chrome**: tool tự mở Chrome bằng đúng profile của bạn
  (đã đăng nhập sẵn) và tự click. Bạn không cần thao tác gì, chỉ ngồi xem.
  Chạy xong Chrome vẫn mở để bạn kiểm tra lại.

**Nếu tool báo "profile đang được dùng":** đóng hết cửa sổ Chrome rồi bấm
Chạy lại — tool sẽ tự mở Chrome đã đăng nhập. Hoặc làm cách này 1 lần:
- Windows: đóng Chrome → double-click `start-chrome.bat`
- Mac/Linux: đóng Chrome → `bash start-chrome.sh`
- Rồi để Chrome đó mở, chạy tool — tool nối vào Chrome đang mở, không cần
  đóng/mở lại nữa.

Nếu phiên đăng nhập hết hạn (Cloudflare/Namecheap bắt đăng nhập lại),
tool sẽ dừng và nhắn trong cửa sổ terminal: bạn đăng nhập ngay trong cửa
sổ Chrome đó rồi nhấn Enter, tool chạy tiếp. Chỉ cần 1 lần đó.

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

## 1. Chạy bằng dòng lệnh (không cần mở web)

```bash
# GIAI ĐOẠN 1: nhập full domain, chạy hàng loạt tới bước 3
# (nhớ đóng Chrome trước, tool sẽ tự mở Chrome đã đăng nhập)
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

## 2. Preset

Sửa trong `presets.yaml`: `shopify`, `vps`, `vercel`, `only-ns` có sẵn.

Chú ý preset Shopify trong file mẫu: `shops.myshopify.com` là placeholder,
phải sửa thành domain `.myshopify.com` thật.

## 3. Giới hạn cần biết

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

## 4. Bản API cũ (chỉ dùng khi muốn chạy không cần mở Chrome)

Bản này CẦN key, giữ lại ở `setup_domain.py` + `web_app_api.py`:
- Cloudflare API Token (quyền Zone:Edit + DNS:Edit)
- Namecheap API User / API Key / Username + whitelist IP máy chạy tool
- Key chỉ lưu ở file `.env` local, đã có trong `.gitignore`, không commit
  lên git. Xem hướng dẫn lấy key trong `web_app_api.py` / `.env.example`.
- Chạy: `python3 web_app_api.py` rồi mở `http://127.0.0.1:8000`,
  hoặc `python3 setup_domain.py --domains-file domains.txt --phase 1`.
