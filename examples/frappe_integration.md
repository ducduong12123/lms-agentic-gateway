# Cấu hình widget vào Frappe Framework — 3 cách, không sửa LMS core

Gateway chạy ở `http://127.0.0.1:8001`, Frappe LMS ở `:8000`.
Widget là 1 file: `http://127.0.0.1:8001/widget/agentic-copilot.js`.

## Cách 1 — Không chạm code (khuyên dùng khi demo)

1. Mở Desk > Website Settings, hoặc trang LMS bất kỳ cho phép Custom HTML.
2. Dán đúng 1 dòng này vào footer / HTML Block:

```html
<script src="http://127.0.0.1:8001/widget/agentic-copilot.js" data-gateway="http://127.0.0.1:8001" data-role="student"></script>
```

3. Đổi `data-role` theo đối tượng: `student | teacher | evaluator | admin`.
4. Reload trang, bấm nút ✦ góc phải, hoặc `Alt+C`. Sidebar IDE dock phải hiện ra.
5. Nếu mở Frappe trực tiếp ở `:8000` thay vì qua proxy `:8080`, origin đó phải có trong `PUBLIC_ORIGINS`; cấu hình Docker mặc định đã bao gồm `localhost:8000` và `lms.localhost:8000`.

Vì sao không sợ CSP: script serve từ gateway có header `Access-Control-Allow-Origin: *`,
widget chỉ `fetch POST /chat`, không đọc cookie Frappe, member lấy từ
`window.frappe.session.user` rồi gateway ép lại ở tool.

## Cách 2 — Chuẩn Frappe (áp dụng cho mọi trang Website/LMS)

Sửa duy nhất `lms/hooks.py` (repo frappelms), dòng `web_include_js = []`:

```python
web_include_js = [
    "http://127.0.0.1:8001/widget/agentic-copilot.js",
]
```

Prod thì đừng để URL gateway dev. Copy file vào app:

```bash
cp D:/lms-agentic-gateway/widget/agentic-copilot.js apps/lms/lms/public/js/agentic-copilot.js
```

rồi trong `hooks.py`:

```python
web_include_js = ["/assets/lms/js/agentic-copilot.js"]
```

Kèm config gateway qua `site_config.json`:

```json
{
  "agentic_gateway_url": "https://gateway.ten-mien.com",
  "agentic_default_role": "student"
}
```

Widget đọc `data-gateway/data-role`, nên nếu dùng nhiều site thì render thẻ
script từ `lms/www/_lms.py:get_context()` thay vì hardcode.

## Cách 3 — Chỉ trang học (SPA Vue ở frontend/index.html)

LMS trang học là Vue SPA render từ `lms/www/_lms.py` + `frontend/index.html`.
Muốn widget chỉ hiện trong `/lms/*`, thêm trước `</body>` trong `frontend/index.html`:

```html
<script src="http://127.0.0.1:8001/widget/agentic-copilot.js"
        data-gateway="http://127.0.0.1:8001" data-role="student"></script>
```

Ưu điểm: Desk quản trị không bị vướng sidebar. Nhược: mỗi lần `yarn build`
phải giữ lại dòng này.

## Kiểm tra sau khi cấu hình

1. Mở DevTools > Network, lọc `agentic-copilot.js` phải 200.
2. Console không báo CORS. Bấm ✦, gửi "Tiến độ của tôi", gateway log tool
   `get_my_progress` với `member` = user đang login (không phải do LLM tự điền).
3. Tool ghi `update_course_content_after_approval` phải trả `approval_id`,
   bấm "Duyệt & chạy" mới ghi vào Frappe.

## Gỡ ra

Xóa 1 dòng script là xong. Frappe LMS không còn dư code AI nào.
