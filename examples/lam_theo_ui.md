# Làm theo UI, không chạm code (2 mục)

## 1. Desk form giảng viên — Tập lệnh ứng dụng khách

Đường đi đúng như ảnh bạn chụp: **Xây dựng > Viết kịch bản > Tập lệnh ứng dụng khách > Mới**.

Tạo **2 bản ghi** (Frappe cho chọn 1 View/bản ghi), cùng 1 script:

| Trường | Bản ghi 1 | Bản ghi 2 |
|---|---|---|
| DocType (dt) | `LMS Course` | `LMS Course` |
| View | `Form` | `List` |
| Enabled | ✓ | ✓ |
| Script | nội dung file `frappe_client_script_ui.js` | giống hệt |

Muốn hiện cả form bài học: lặp lại 2 bản ghi cho DocType `Course Lesson`.

Lưu xong **không cần restart**. Mở form LMS Course, hard-reload
(`Ctrl+Shift+R`), góc phải dưới có nút ✦, bấm hoặc `Alt+C` mở sidebar.
Role mặc định `teacher` (đổi hằng `ACP_ROLE` trong script nếu cần).

## 2. Trang Website / Workspace — khối HTML Tùy chỉnh

- **Workspace** (ví dụ workspace Learning): mở workspace > nút `···` > Edit >
  Add Block > **Custom HTML** > paste nguyên nội dung file
  `frappe_html_block.html` > Save.
- **Web Page**: Website > Web Page > mở trang > thêm section Custom HTML >
  paste cùng nội dung > Save.

Reload trang là thấy nút ✦. Đổi `data-role="student|teacher|evaluator|admin"`
tùy đối tượng xem trang đó.

## Kiểm tra (cả 2 mục)

1. DevTools > Network: `agentic-copilot.js` status 200 (lấy từ gateway `:8001`).
2. Gửi thử "Tiến độ của tôi" — gateway phải log tool kèm `member` là user
   đang login (LLM không tự đặt được).
3. Tool `update_course_content_after_approval` phải trả `approval_id`,
   bấm "Duyệt & chạy" mới ghi vào Frappe.

## Lỗi hay gặp

- **Không thấy nút**: cache trình duyệt — hard-reload; hoặc Client Script chưa
  tick Enabled; hoặc sai DocType/View.
- **Console báo mixed-content**: site chạy `https` mà script là `http` —
  trình duyệt chặn. Demo local `http` thì OK; prod phải serve gateway `https`
  hoặc copy file JS vào `lms/public/js/` (lúc đó mới là 1 lần chạm code).
- **Gateway offline**: sidebar báo "Mất kết nối gateway" — bật server:
  `cd src && python -m gateway.api.server`.
