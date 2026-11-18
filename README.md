# Tool tự động setup domain: Namecheap → Cloudflare

## 0. Kéo về máy + chạy web nội bộ (khuyên dùng)

Web nội bộ chạy ở `http://127.0.0.1:8000`, chỉ ở máy bạn, không public. Key nhập ở đó chỉ lưu vào `.env` local, không commit lên git.

```bash
# Lần đầu, trên máy bạn:
git clone <link-repo-cua-ban> domain-setup-tool
cd domain-setup-tool
cp .env.example .env   # điền key vào .env, hoặc nhập thẳng ở web
pip install -r requirements.txt
python3 web_app.py
# mở http://127.0.0.1:8000
```

Đẩy code này lên git (từ máy đã có code):
```bash
cd domain-setup-tool
git init
git add .
git commit -m "Domain setup tool: Namecheap to Cloudflare, 2 phases"
# Tạo repo trống trên GitHub rồi chạy 2 lệnh nó hiện ra, vd:
git remote add origin https://github.com/<ten-ban>/domain-setup-tool.git
git push -u origin main
```
`.env` và `reports/` đã có trong `.gitignore`, không bị đẩy lên git.

Trong web có 2 giai đoạn:
- **Giai đoạn 1:** dán full domain vào ô lớn, chạy hàng loạt tới bước 3 (tạo zone Cloudflare + đổi NS ở Namecheap). Chưa cần record.
- **Giai đoạn 2:** sau đó mới thêm domain + DNS record, mỗi domain 3 ô (Type / Name / Content), ô nào để trống thì bỏ qua. Hỗ trợ A, AAAA, CNAME, TXT, MX. Có nút preset Shopify / VPS / Vercel.

Không muốn dùng API key thì dùng cách Chrome: web có nút mở Cloudflare / Namecheap, bạn đăng nhập tay và làm theo các bước 1-3.

---

Tự động làm tay 5 bước cho mỗi domain:
1. **Cloudflare:** tạo zone (nếu chưa có) → lấy 2 nameserver được cấp
2. **Cloudflare:** quét DNS cũ (tuỳ chọn) + tạo/cập nhật DNS records theo preset — chạy lại lần 2 không tạo trùng
3. **Namecheap:** `domains.dns.setCustom` → đổi NS sang NS Cloudflare
4. **Verify:** hỏi DNS 1.1.1.1 xem NS đã khớp chưa + trạng thái zone
5. **Báo cáo:** ghi CSV vào `reports/setup_YYYYMMDD_HHMMSS.csv`

## 1. Chuẩn bị key (làm 1 lần)

**Cloudflare API Token**
- Vào dash.cloudflare.com → My Profile → API Tokens → Create Token
- Chọn template **Edit zone DNS**, thêm quyền `Zone:Zone:Edit`
- Zone Resources: All zones (cho tiện chạy nhiều domain) → Create Token, copy token

**Namecheap API**
- Vào Namecheap → Profile → Tools → **Namecheap API Access** → Enable
- Lấy `ApiKey`. `ApiUser` = username Namecheap của bạn
- **Bắt buộc:** whitelist IP public của máy chạy tool (mục API Access → Whitelisted IPs). Nếu không sẽ lỗi IP not whitelisted.
- Lưu ý: Namecheap API chỉ dùng được cho domain đang ở tài khoản đó, và tất cả call đều là GET trả XML.

**DNSSEC:** nếu domain đang bật DNSSEC ở Namecheap thì tắt trước, không thì đổi NS xong domain sẽ không resolve được.

## 2. Cài đặt

```bash
cd domain-setup-tool
cp .env.example .env
# mở .env điền CLOUDFLARE_API_TOKEN, NAMECHEAP_API_USER, NAMECHEAP_API_KEY, NAMECHEAP_USERNAME
pip install -r requirements.txt
```

## 3. Chạy

```bash
# GIAI ĐOẠN 1: nhập full domain, chạy hàng loạt tới bước 3 (tạo zone + đổi NS)
python3 setup_domain.py --domains-file domains.txt --phase 1 --dry-run
python3 setup_domain.py --domains-file domains.txt --phase 1

# GIAI ĐOẠN 2: sau đó mới thêm record, 3 ô, ô trống bỏ qua
python3 setup_domain.py --domains-file domains.txt --phase 2 --records-file custom-records.json
python3 setup_domain.py --domain example.com --phase 2 --record "A:@:1.2.3.4" --record "CNAME:www:shops.myshopify.com"

# Xem trước, không đổi gì thật (luôn chạy cái này trước với domain thật đầu tiên)
python3 setup_domain.py --domain example.com --preset shopify --dry-run

# Chỉ đổi NS, không tạo record nào
python3 setup_domain.py --domain example.com --preset only-ns

# Shopify (sửa shops.myshopify.com trong presets.yaml thành shop thật trước)
python3 setup_domain.py --domain example.com --preset shopify

# VPS / server riêng
python3 setup_domain.py --domain example.com --preset vps --target-ip 1.2.3.4

# Chạy hàng loạt
cp domains.txt.example domains.txt   # sửa file, mỗi dòng 1 domain
python3 setup_domain.py --domains-file domains.txt --preset shopify

# Chỉ kiểm tra sau khi chạy (NS propagate thường 5 phút – 24h)
python3 setup_domain.py --domain example.com --verify-only
```

Các flag khác: `--only-cloudflare`, `--only-namecheap`, `--skip-scan`, `--sandbox` (test Namecheap sandbox), `--yes` (bỏ hỏi xác nhận).

## 4. Preset

Sửa trong `presets.yaml`: `shopify`, `vps`, `vercel`, `only-ns` có sẵn. Thêm preset mới thì thêm 1 block rồi chạy `--preset ten-moi` (code đã hỗ trợ đọc từ file, nhưng `--preset` ở CLI hiện chỉ liệt kê 4 tên có sẵn — sửa `choices` trong `setup_domain.py` nếu thêm tên mới).

Chú ý preset Shopify trong file mẫu: `shops.myshopify.com` là placeholder, phải sửa thành domain `.myshopify.com` thật.

## 5. Giới hạn cần biết

- Cloudflare: tối đa ~25 zone mới / 10 phút. Nếu tài khoản đã có >50 domain mà số zone pending nhiều hơn số zone active thì sẽ bị chặn thêm zone mới — cách xử lý là chờ các zone pending active rồi mới thêm tiếp.
- Chạy lại tool trên domain đã setup là an toàn: zone đã có thì dùng lại, record đã đúng thì bỏ qua, NS đã đúng thì không đổi.
- Đổi NS sẽ tắt URL forwarding / Email forwarding / Dynamic DNS của Namecheap cho domain đó (vì DNS giờ do Cloudflare quản). Nếu domain đang dùng mail ở Namecheap thì phải mang MX/TXT sang Cloudflare trước — thêm vào preset.
- Không commit `.env` lên git. File `.env` chỉ ở máy bạn.
