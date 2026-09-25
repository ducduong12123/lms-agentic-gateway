# Bài tập và tiêu chí chấm — Python cơ bản

## Bài tập tuần 3: Phân loại khoản chi

Viết chương trình đọc một danh sách số tiền và in ra mỗi khoản thuộc loại "lớn" (trên 500.000đ), "vừa" (trên 100.000đ) hay "nhỏ". Dùng câu lệnh điều kiện và vòng lặp for.

## Dự án cuối khóa: Quản lý chi tiêu cá nhân

Viết chương trình đọc tệp `chi_tieu.json` (danh sách khoản chi, mỗi khoản có `so_tien`, `danh_muc`, `ngay`) và:

1. Tính tổng chi.
2. Tính tổng theo từng danh mục.
3. Liệt kê các khoản chi bất thường (lớn hơn 5 lần trung bình).
4. Ghi báo cáo ra `bao_cao.json`.

Nộp link repo GitHub công khai. Repo cần có README ngắn và ít nhất 3 test pytest.

## Tiêu chí chấm dự án (thang 3 mức mỗi tiêu chí)

| Tiêu chí | 1 — Chưa đạt | 2 — Đạt | 3 — Tốt |
|---|---|---|---|
| Đúng chức năng | Sai tổng hoặc thiếu yêu cầu | Đủ 4 yêu cầu, sai trường hợp biên | Đủ yêu cầu, đúng cả danh sách rỗng |
| Tổ chức hàm | Viết liền một khối | Có hàm nhưng hàm làm nhiều việc | Mỗi hàm một việc, có main() |
| Đặt tên rõ ràng | Tên a, b, x1 | Phần lớn tên rõ nghĩa | Tên nhất quán, đúng quy tắc |
| Đọc ghi tệp | Không đọc được JSON | Đọc được, quên encoding | Dùng with, utf-8, ensure_ascii=False |
| Kiểm thử | Không có test | Có test cho trường hợp thường | Có test trường hợp biên |
