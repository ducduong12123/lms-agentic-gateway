# Nhúng widget vào Frappe hiện tại (không sửa LMS core)

## Cách 1: Website (khuyên dùng, 1 dòng)
Vào Website Settings > HTML Block / Custom Script, dán:

```html
<script src="http://127.0.0.1:8001/widget/agentic-copilot.js"
        data-gateway="http://127.0.0.1:8001" data-role="student"></script>
```

Đổi `data-role` theo trang: `student` / `teacher` / `evaluator` / `admin`.
Khi Frappe chạy trực tiếp ở `:8000`, thêm origin của trang vào `PUBLIC_ORIGINS` của gateway; cấu hình Docker mặc định đã có `localhost:8000` và `lms.localhost:8000`.
Widget tự lấy `window.frappe.session.user` làm member, gateway ép vào tool.

## Cách 2: Desk (dev only)
Thêm vào `lms/hooks.py` (app LMS, không phải gateway này):

```python
web_include_js = ["http://127.0.0.1:8001/widget/agentic-copilot.js"]
```

Prod thì copy file `widget/agentic-copilot.js` vào `lms/public/js/` rồi include local.

## Webhook Frappe -> Gateway
Trong Frappe: Settings > Webhook > New, chọn DocType
`LMS Course, Course Chapter, Course Lesson, LMS Quiz, LMS Enrollment...`,
Request URL: `http://gateway:8001/webhook/frappe`,
Secret trùng `FRAPPE_WEBHOOK_SECRET`.
