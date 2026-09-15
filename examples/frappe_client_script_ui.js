// Paste nguyên khối này vào: Xây dựng > Viết kịch bản > Tập lệnh ứng dụng khách > Mới
// DocType: LMS Course (làm thêm 1 cái cho Course Lesson nếu muốn)
// View: Form + List đều được. Enabled: ✓
// Không cần bench restart, Ctrl+Shift+R là thấy nút ✦.

// Paste nguyên khối này vào ô Script của Tập lệnh ứng dụng khách. Enabled: ✓
// Đổi 2 hằng dưới khi lên prod. Không cần bench restart, hard-reload (Ctrl+Shift+R) là thấy nút ✦.
var ACP_GATEWAY = "http://127.0.0.1:8001";
var ACP_ROLE = "teacher"; // Desk form là giảng viên/quản trị -> teacher

(function () {
  if (window.__acp_mounted) return;
  if (document.querySelector('script[src*="agentic-copilot.js"]')) return;
  var s = document.createElement("script");
  s.src = ACP_GATEWAY + "/widget/agentic-copilot.js";
  s.dataset.gateway = ACP_GATEWAY;
  s.dataset.role = ACP_ROLE;
  document.head.appendChild(s);
})();
