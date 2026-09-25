# Demo repos: "Dự án cuối khóa: ứng dụng quản lý chi tiêu dòng lệnh"

`bench --site lms.localhost execute lms_copilot.demo.seed` creates the course
"Python cơ bản (Copilot demo)" and project submissions that point at
`https://github.com/copilot-demo-learner/chi-tieu-<ten>`. That GitHub owner does not
exist (checked 2026-09-25, HTTP 404), so the seed never downloads code and never
calls the AI Gateway. Test results and feedback drafts are written directly.

To demo a real review, create public repos that match the table below and submit
them from `/copilot/submit/<assignment>` as a demo learner.

## Brief every repo implements

- `main.py` at the repo root, run with `python main.py`, standard library only.
- Menu: add an expense (amount, category, note), list expenses, totals per category, quit.
- Data is stored in `chi_tieu.json` next to `main.py`.

A correct reference solution ("clean") has `nhap_so_tien()`, `nhap_khoan_chi()`,
`tong_theo_danh_muc()`, `doc_du_lieu()`, `luu_du_lieu()` and a short `main()`, and a README
with the run command.

## Planted mistakes

| # | Mistake | Where to plant it | Rubric criterion | Sandbox test that fails | Lesson block |
| --- | --- | --- | --- | --- | --- |
| A | No `try/except` around the amount: `so_tien = float(input("Số tiền: "))` | the add-expense code | Xử lý lỗi đầu vào → level 1 | `nhập số tiền 'abc' không làm chương trình dừng` | Bài 3 · `b3-try` |
| B | The whole program in one `main()` of 100+ lines: menu, arithmetic and file writes mixed together | `main.py` | Tách hàm, cấu trúc code → level 1 | none, it still runs | Bài 2 · `b2-split` |
| C | JSON not kept between runs: `open("chi_tieu.json", "w")` with only the new expense, or no file at all, or a crash with `FileNotFoundError` on the first run | the save code | Lưu dữ liệu vào file JSON → level 1–2 | `dữ liệu còn sau khi chạy lại` | Bài 4 · `b4-overwrite` |

## Seeded learners

All are adults. Emails are `copilot-demo-<n>@example.com`.

| n | Learner | Repo | Mistakes | Review state after seeding |
| --- | --- | --- | --- | --- |
| 1 | Nguyễn Văn An | `chi-tieu-an` | A | Draft pending review, medium confidence |
| 2 | Trần Thị Bình | `chi-tieu-binh` | none | Draft pending review, high confidence (quick approve) |
| 3 | Lê Hoàng Châu | `chi-tieu-chau` | B | Feedback approved and sent |
| 4 | Phạm Minh Dũng | `chi-tieu-dung` | C (overwrite) | Teacher requested a rewrite |
| 5 | Võ Thu Hà | `chi-tieu-ha`, then `chi-tieu-ha-v2` | A + C | First draft superseded; second draft pending, low confidence |
| 6 | Đặng Quốc Khoa | `chi-tieu-khoa` | no `main.py` | Tests could not run, waiting for a teacher; also sent an escalated question |

The seed also adds a pending lesson-change proposal on Bài 3 (a `while True` + `try/except`
example) and the escalated question from learner 6. Remove everything with
`bench --site lms.localhost execute lms_copilot.demo.clear`.
