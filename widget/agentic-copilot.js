/* agentic-copilot.js — sidebar IDE bên phải, 1 file duy nhất.
   Nhúng bởi reverse proxy: <script src="/ai/widget.js" defer></script>
   Dock phải kiểu IDE: mở là đẩy nội dung trang sang trái, kéo mép để đổi rộng.
   Phím: Alt+C đóng/mở, Enter gửi, Shift+Enter xuống dòng, Alt+M rộng/hẹp.
*/
(function () {
  "use strict";
  var me = document.currentScript;
  // Client Script nạp động (appendChild) có thể không có currentScript -> fallback querySelector
  if (!me) {
    try {
      me = document.querySelector('script[src*="agentic-copilot.js"]');
    } catch (e) { me = null; }
  }
  var scriptUrl = null;
  try { scriptUrl = me && me.src ? new URL(me.src, location.href) : null; } catch (e) { /* ignore */ }
  var GATEWAY = (me && me.dataset.gateway) ||
    (scriptUrl && scriptUrl.pathname.indexOf("/ai/") === 0 ? "/ai" : (scriptUrl && scriptUrl.origin)) || "/ai";
  var CURRENT_MODE = "chat";
  var CURRENT_CONCEPT = "";
  var routeScript = document.createElement("script");
  routeScript.src = GATEWAY + "/widget/route-adapter.js";
  routeScript.defer = true;
  document.head.appendChild(routeScript);
  var CURRENT_IDENTITY = { user: "Guest", role: "student", roles: [] };
  function newConversationId() {
    return "acp-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }
  var CONVERSATION_ID = newConversationId();
  try {
    CONVERSATION_ID = sessionStorage.getItem("acp-conversation-id") || CONVERSATION_ID;
    sessionStorage.setItem("acp-conversation-id", CONVERSATION_ID);
  } catch (e) { /* sessionStorage có thể bị chặn trong iframe/private mode */ }
  if (window.__acp_mounted && document.querySelector("#acp-host")) return;
  window.__acp_mounted = true;
  try {
    document.querySelectorAll("#acp-host,#acp-side,#acp-fab").forEach(function (n) { n.remove(); });
    document.querySelectorAll("style[data-acp]").forEach(function (n) { n.remove(); });
  } catch (e) { /* DOM chưa sẵn sàng */ }
  var css = [
    "#acp-fab{position:fixed;right:24px;bottom:24px;z-index:2147483000;width:44px;height:44px;border-radius:50%;",
    "border:1px solid #333;background:#1f1f1f;color:#fff;font-size:17px;line-height:1;cursor:pointer;",
    "box-shadow:0 12px 28px rgba(0,0,0,.35);transition:transform .18s ease,background .18s ease}",
    "#acp-fab:hover{background:#2b2b2b;transform:translateY(-2px)}",
    "#acp-fab:focus-visible,#acp-head button:focus-visible,#acp-send:focus-visible,#acp-chip:focus-visible,.acp-suggestion:focus-visible{outline:2px solid #2f81f7;outline-offset:2px}",
    "#acp-side{position:fixed;right:24px;bottom:80px;top:auto;z-index:2147483001;width:var(--acp-w,440px);height:min(760px,calc(100vh - 104px));",
    "max-width:calc(100vw - 32px);background:#111;color:#e6e6e6;border:1px solid #2a2a2a;border-radius:12px;overflow:hidden;",
    "display:flex;flex-direction:column;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;font-size:13.5px;",
    "box-shadow:0 24px 70px rgba(0,0,0,.5);transform:translateY(12px) scale(.98);",
    "opacity:0;visibility:hidden;pointer-events:none;transition:opacity .2s ease,transform .2s ease,visibility .2s ease}",
    "#acp-side.open{transform:none;opacity:1;visibility:visible;pointer-events:auto}",
    "body.acp-shell-open{padding-right:var(--acp-shell-w,560px)!important;transition:padding-right .2s ease}",
    "body.acp-shell-open #app > .w-screen{width:calc(100vw - var(--acp-shell-w,560px))!important;transition:width .2s ease}",
    "body.acp-shell-open #acp-fab{display:none}",
    "#acp-grip{display:none;position:absolute;left:-5px;top:0;bottom:0;width:10px;cursor:ew-resize;z-index:2}",
    "#acp-grip:before{content:'';position:absolute;left:4px;top:50%;width:3px;height:44px;transform:translateY(-50%);border-radius:3px;background:#3a3a3a}",
    "#acp-grip:hover{background:rgba(47,129,247,.12)}#acp-grip:hover:before{background:#2f81f7}",
    "#acp-side.shell{--acp-w:min(560px,calc(100vw - 48px));top:0;right:0;bottom:0;height:auto;max-width:none;",
    "border-top:0;border-right:0;border-bottom:0;border-radius:0;transform:translateX(102%) scale(1)}",
    "#acp-side.shell.open{transform:none}#acp-side.shell #acp-grip{display:block}",
    "#acp-head{display:flex;align-items:center;gap:8px;padding:10px 10px 10px 14px;border-bottom:1px solid #232323;background:#111}",
    "#acp-avatar{width:28px;height:28px;flex:0 0 auto;border-radius:50%;background:#e8e8e8;color:#333;",
    "display:flex;align-items:center;justify-content:center;font-size:14px}",
    "#acp-head-main{flex:1;min-width:0;display:flex;align-items:center;gap:4px}",
    "#acp-title{font-size:13.5px;font-weight:500;color:#ececec;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px}",
    ".acp-chevron{color:#8a8a8a;font-size:13px}",
    "#acp-logo,#acp-sub,#acp-role,#acp-ctx,#acp-quick{display:none!important}",
    ".acp-hbtn{display:grid;place-items:center;width:28px;height:28px;padding:0;background:transparent;border:0;",
    "color:#9a9a9a;border-radius:6px;font-size:15px;line-height:1;cursor:pointer;transition:background .15s ease,color .15s ease}",
    ".acp-hbtn:hover{background:#262626;color:#f0f0f0}",
    "#acp-msgs{flex:1;overflow-y:auto;padding:22px 20px 18px;background:#111;display:flex;flex-direction:column;gap:20px}",
    "#acp-msgs::-webkit-scrollbar{width:8px}#acp-msgs::-webkit-scrollbar-thumb{background:#333;border:2px solid #111;border-radius:8px}",
    ".acp-welcome{padding:56px 8px 0;display:flex;flex-direction:column;align-items:center;text-align:center}",
    ".acp-welcome-icon{width:52px;height:52px;border-radius:50%;background:#e8e8e8;color:#333;display:grid;place-items:center;font-size:24px;margin-bottom:18px}",
    ".acp-welcome h2{margin:0;color:#f0f0f0;font-size:19px;font-weight:600;letter-spacing:-.01em}",
    ".acp-welcome p{margin:8px 0 0;color:#8a8a8a;font-size:12.5px}",
    ".acp-suggestions{width:100%;max-width:340px;margin-top:20px;display:flex;flex-direction:column;gap:2px;text-align:left}",
    ".acp-suggestion{display:flex;align-items:center;gap:10px;width:100%;padding:9px 10px;background:transparent;border:0;border-radius:8px;",
    "color:#d6d6d6;font:inherit;font-size:13.5px;cursor:pointer;transition:background .15s ease}",
    ".acp-suggestion:hover{background:#222;color:#fff}.acp-suggestion-mark{width:20px;text-align:center;color:#8a8a8a;font-size:15px;flex:0 0 auto}",
    ".acp-u{align-self:flex-end;max-width:85%;background:#2c2c2c;color:#fff;border-radius:16px;padding:8px 14px;",
    "white-space:pre-wrap;line-height:1.5;font-size:13.5px}",
    ".acp-a{align-self:flex-start;width:100%;background:transparent;color:#e3e3e3;font-size:13.5px;line-height:1.75}",
    ".acp-a .body{word-wrap:break-word}.acp-a .body b{color:#fff;font-weight:600}",
    ".acp-a ul,.acp-a ol{margin:8px 0;padding-left:22px}.acp-a li{margin:5px 0;padding-left:2px}.acp-a li::marker{color:#8a8a8a}",
    ".acp-a pre{background:#1c1c1c;border:1px solid #2c2c2c;border-radius:8px;padding:0;margin:10px 0;overflow:auto}",
    ".acp-a pre .ph{display:flex;justify-content:space-between;padding:6px 10px;color:#888;font-size:10.5px;border-bottom:1px solid #2c2c2c}",
    ".acp-a pre code{display:block;padding:10px;color:#ddd;font-family:ui-monospace,Consolas,monospace;font-size:12px;white-space:pre}",
    ".acp-a code.ic{background:#262626;border:1px solid #333;border-radius:5px;padding:1px 5px;color:#e0e0e0;font-size:12px}",
    ".acp-actions{display:flex;gap:2px;margin-top:10px}",
    ".acp-actions button{display:grid;place-items:center;width:27px;height:27px;background:transparent;border:0;border-radius:6px;",
    "color:#8a8a8a;font-size:14px;cursor:pointer;transition:background .15s ease,color .15s ease}",
    ".acp-actions button:hover{background:#242424;color:#eee}.acp-actions button.on{background:#2a2a2a;color:#fff}",
    ".acp-src{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.acp-src span{font-size:11px;color:#8a8a8a;",
    "border:1px solid #2b2b2b;background:#181818;border-radius:6px;padding:3px 8px}",
    ".acp-tools,.acp-time,.acp-mtool,.acp-think{display:none!important}",
    "#acp-insight{margin:10px 14px 0;padding:11px 12px;border:1px solid #285247;background:#142720;border-radius:9px;color:#d9eee7;font-size:12px;line-height:1.45}",
    "#acp-insight[hidden]{display:none}#acp-insight b{color:#fff}#acp-insight button{margin:8px 6px 0 0;border:1px solid #3c665b;background:transparent;color:#bfe2d6;border-radius:7px;padding:5px 8px;cursor:pointer}",
    ".acp-ap{margin-top:10px;border:1px solid #554507;background:#221d0c;border-radius:8px;padding:10px;color:#e7c96f;font-size:12.5px}",
    ".acp-ap button{margin-top:8px;background:#2f81f7;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;font-weight:600}",
    "#acp-type{display:none;padding:0 20px 8px;background:#111;color:#777;font-size:12px}",
    "#acp-type i{display:inline-block;width:4px;height:4px;border-radius:50%;background:#777;margin-right:3px;animation:acpb 1s infinite}",
    "@keyframes acpb{0%,100%{opacity:.25}50%{opacity:1}}",
    "#acp-inbar{padding:8px 12px 12px;background:#111;position:relative}",
    ".acp-composer{border:1px solid #333;border-radius:12px;background:#1d1d1d;transition:border-color .15s ease,box-shadow .15s ease}",
    ".acp-composer:focus-within{border-color:#2f81f7;box-shadow:0 0 0 1px #2f81f7}",
    "#acp-side textarea,#acp-side textarea:focus,#acp-side button:focus,#acp-side select:focus{outline:none!important;box-shadow:none!important;",
    "--tw-ring-offset-shadow:0 0 #0000!important;--tw-ring-shadow:0 0 #0000!important;--tw-ring-offset-width:0px!important}",
    ".acp-chiprow{padding:10px 10px 0;display:flex;gap:6px;flex-wrap:wrap}",
    "#acp-chip{display:inline-flex;align-items:center;gap:6px;background:transparent;border:1px solid #3d3d3d;color:#cfcfcf;",
    "border-radius:14px;padding:4px 10px;font-size:11.5px;cursor:pointer;max-width:100%;transition:opacity .15s ease,background .15s ease}",
    "#acp-chip:hover{background:#262626}#acp-chip.off{opacity:.4}#acp-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px}",
    "#acp-in{display:block;width:100%;box-sizing:border-box;background:transparent!important;color:#eee;border:0!important;box-shadow:none!important;",
    "padding:10px 12px 4px;font:inherit;font-size:13.5px;resize:none;outline:none!important;min-height:30px;max-height:150px;line-height:1.55}",
    "#acp-foot{display:flex;justify-content:space-between;align-items:center;padding:4px 6px 6px}",
    ".acp-fleft,.acp-fright{display:flex;align-items:center;gap:2px}",
    ".acp-toolbtn{display:grid;place-items:center;width:28px;height:28px;background:transparent;border:0;border-radius:7px;",
    "color:#9a9a9a;font-size:16px;cursor:pointer;transition:background .15s ease,color .15s ease}",
    ".acp-toolbtn:hover{background:#2b2b2b;color:#eee}.acp-toolbtn.on{background:#2b2b2b;color:#fff}",
    "#acp-auto{background:transparent;border:0;color:#c9c9c9;font-size:12.5px;font-weight:500;padding:6px 8px;border-radius:6px;cursor:pointer}",
    "#acp-auto:hover{background:#2b2b2b;color:#fff}",
    "#acp-send{display:grid;place-items:center;width:28px;height:28px;padding:0;background:#2e2e2e;color:#858585;border:0;",
    "border-radius:50%;font-size:15px;line-height:1;cursor:pointer;transition:background .15s ease,color .15s ease}",
    "#acp-send.ready{background:#e8e8e8;color:#111}#acp-send.ready:hover{background:#fff}#acp-send:disabled{cursor:default;opacity:.8}",
    "#acp-pop{position:absolute;left:12px;right:12px;bottom:86px;background:#222;border:1px solid #3a3a3a;border-radius:10px;",
    "box-shadow:0 16px 40px rgba(0,0,0,.5);padding:6px;display:none;flex-direction:column;gap:2px;z-index:5}",
    "#acp-pop.show{display:flex}#acp-pop button{display:flex;gap:10px;align-items:center;background:transparent;border:0;",
    "color:#ddd;font:inherit;font-size:13px;text-align:left;padding:9px 10px;border-radius:7px;cursor:pointer}",
    "#acp-pop button:hover{background:#2e2e2e;color:#fff}",
    "@media(max-width:560px){#acp-side{right:12px;bottom:72px;width:calc(100vw - 24px);max-width:none;height:min(760px,calc(100vh - 88px))}",
    "#acp-side.shell{right:0;top:0;bottom:0;width:100vw;max-width:100vw;border-radius:0}body.acp-shell-open{padding-right:0!important}",
    "body.acp-shell-open #app > .w-screen{width:100vw!important}#acp-fab{right:16px;bottom:16px}}",
    ".acp-cursor{display:inline-block;width:7px;height:15px;background:#2f81f7;vertical-align:-2px;margin-left:2px;animation:acpb .8s infinite}",
  ].join("\n");
  var host = document.createElement("div");
  host.id = "acp-host";
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  var st = document.createElement("style");
  st.setAttribute("data-acp", "1");
  st.textContent = css;
  root.appendChild(st);
  var pushStyle = document.createElement("style");
  pushStyle.setAttribute("data-acp", "1");
  pushStyle.textContent = "body.acp-shell-open{padding-right:var(--acp-shell-w,560px)!important;transition:padding-right .2s ease}" +
    "body.acp-shell-open #app>.w-screen{width:calc(100vw - var(--acp-shell-w,560px))!important;transition:width .2s ease}" +
    "@media(max-width:560px){body.acp-shell-open{padding-right:0!important}body.acp-shell-open #app>.w-screen{width:100vw!important}}";
  document.head.appendChild(pushStyle);
  var fab = document.createElement("button");
  fab.id = "acp-fab"; fab.textContent = "✦"; fab.title = "Trợ lý LMS (Alt+C)";
  var side = document.createElement("div");
  side.id = "acp-side";
  side.innerHTML =
    '<div id="acp-grip" title="kéo để đổi rộng"></div>' +
    '<div id="acp-head"><div id="acp-avatar">✦</div>' +
    '<div id="acp-head-main"><div id="acp-title">New AI chat</div><span class="acp-chevron">⌄</span></div>' +
    '<button id="acp-new" class="acp-hbtn" title="Đoạn chat mới" aria-label="Đoạn chat mới">✚</button>' +
    '<button id="acp-share" class="acp-hbtn" title="Chia sẻ" aria-label="Chia sẻ">⤴</button>' +
    '<button id="acp-wide" class="acp-hbtn" title="rộng/hẹp (Alt+M)" aria-label="Rộng hoặc hẹp">▭</button>' +
    '<button id="acp-pin" class="acp-hbtn" title="Ghim" aria-label="Ghim">⌖</button>' +
    '<button id="acp-more" class="acp-hbtn" title="Tùy chọn" aria-label="Tùy chọn">⋯</button>' +
    '<button id="acp-x" class="acp-hbtn" title="đóng" aria-label="Đóng">»</button></div>' +
    '<div id="acp-ctx"><span id="acp-dot"></span><span id="acp-ctx-t">…</span></div><div id="acp-insight" hidden></div>' +
    '<div id="acp-msgs"></div><div id="acp-type"><i></i><i></i><i></i> AI đang soạn…</div>' +
    '<div id="acp-inbar"><div id="acp-pop"></div><div class="acp-composer">' +
    '<div class="acp-chiprow"><button id="acp-chip" title="Ngữ cảnh LMS đang dùng" type="button">◎ <span id="acp-chip-t">LMS</span></button></div>' +
    '<textarea id="acp-in" rows="1" placeholder="Hỏi bất cứ điều gì về khóa học…"></textarea>' +
    '<div id="acp-foot"><div class="acp-fleft"><button id="acp-add" class="acp-toolbtn" title="Thêm" type="button">＋</button>' +
    '<button id="acp-mode" class="acp-toolbtn" title="Chế độ" type="button">⚙</button></div>' +
    '<div class="acp-fright"><button id="acp-auto" title="Chế độ tự động" type="button">Auto</button>' +
    '<button id="acp-send" title="Gửi" aria-label="Gửi" type="button">↑</button></div></div></div></div>';
  document.body.appendChild(host);
  root.appendChild(fab);
  root.appendChild(side);
  var msgs = side.querySelector("#acp-msgs");
  var input = side.querySelector("#acp-in");
  var sendBtn = side.querySelector("#acp-send");
  var typing = side.querySelector("#acp-type");
  var ctxT = side.querySelector("#acp-ctx-t");
  var titleEl = side.querySelector("#acp-title");
  var chip = side.querySelector("#acp-chip");
  var chipT = side.querySelector("#acp-chip-t");
  var pop = side.querySelector("#acp-pop");
  var addBtn = side.querySelector("#acp-add");
  var modeBtn = side.querySelector("#acp-mode");
  var autoBtn = side.querySelector("#acp-auto");
  var insight = side.querySelector("#acp-insight");
  sendBtn.textContent = "↑";
  sendBtn.title = "Gửi câu hỏi";
  function shortCtx() {
    var p = location.pathname + location.search;
    var m = p.match(/courses\/([^\/?]+)/);
    if (m) {
      try { return decodeURIComponent(m[1]).replace(/-/g, " ").slice(0, 28); } catch (e) { return m[1].slice(0, 28); }
    }
    var seg = (p.split("/").filter(Boolean).pop() || "LMS").replace(/-/g, " ");
    return decodeURIComponent(seg).slice(0, 28);
  }
  function user() { return CURRENT_IDENTITY.user || "Guest"; }
  function pageCtx() {
    var p = location.pathname + location.search;
    var route = window.LMSAgentRoute && window.LMSAgentRoute.parse(p);
    if (route && route.course) return "course=" + route.course + " · " + p.slice(0, 80);
    var m = p.match(/courses\/([^\/?]+)/);
    return (m ? "course=" + decodeURIComponent(m[1]) + " · " : "") + p.slice(0, 80);
  }
  function syncUiChrome() {
    ctxT.textContent = user() + " · " + CURRENT_IDENTITY.role + " · " + pageCtx();
    chipT.textContent = shortCtx() || "LMS";
  }
  function refreshInsight() {
    fetch(GATEWAY + "/me/mastery?page=" + encodeURIComponent(location.pathname + location.search), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("unavailable"); return r.json(); })
      .then(function (data) {
        var weak = (data.concepts || [])[0], gate = data.soft_gate;
        CURRENT_CONCEPT = weak ? weak.concept_id : "";
        if (!weak && !gate) { insight.hidden = true; insight.innerHTML = ""; return; }
        var html = gate ? "<b>Ôn nhanh 3 phút?</b> Bài này có kiến thức nền chưa vững." :
          "<b>AI nghĩ bạn nên ôn:</b> " + esc(weak.label) + " (khoảng " + Math.round(100 * (weak.p_display || weak.p || 0)) + "%).";
        if (weak && weak.why && weak.why.length) html += "<div>Vì evidence gần nhất: " + esc(weak.why[0].kind) + ".</div>";
        insight.innerHTML = html;
        if (weak) {
          var no = document.createElement("button"); no.textContent = "Không đúng";
          no.onclick = function () {
            fetch(GATEWAY + "/me/feedback", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ concept_id: weak.concept_id, note: "widget-not-accurate" }) });
            insight.hidden = true;
          };
          insight.appendChild(no);
        }
        if (gate) { var review = document.createElement("button"); review.textContent = "Ôn với AI"; review.onclick = function () { setOpen(true); send("Cho tôi ôn nhanh 3 phút kiến thức nền của bài này."); }; insight.appendChild(review); }
        insight.hidden = false;
      }).catch(function () { insight.hidden = true; });
  }
  function syncSend() { sendBtn.classList.toggle("ready", input.value.trim().length > 0); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function md(t) {
    var lines = String(t == null ? "" : t).split("\n");
    var html = "", inList = false, inCode = false, lang = "", code = "";
    function inline(s) {
      var h = esc(s);
      h = h.replace(/`([^`\n]+)`/g, '<code class="ic">$1</code>');
      h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
      return h;
    }
    function closeList() { if (inList) { html += "</ul>"; inList = false; } }
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      var fence = ln.match(/^```(\w*)\s*$/);
      if (fence) {
        if (inCode) { html += '<pre><div class="ph"><span>' + esc(lang || "code") + '</span></div><code>' + code + "</code></pre>"; inCode = false; lang = ""; code = ""; }
        else { closeList(); inCode = true; lang = fence[1] || ""; code = ""; }
        continue;
      }
      if (inCode) { code += (code ? "\n" : "") + esc(ln); continue; }
      var item = ln.match(/^\s*[-•]\s+(.*)$/) || ln.match(/^\s*\d+[.)]\s+(.*)$/);
      if (item) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += "<li>" + inline(item[1]) + "</li>";
        continue;
      }
      if (!ln.trim()) { closeList(); html += "<div style='height:8px'></div>"; continue; }
      closeList();
      html += "<div>" + inline(ln) + "</div>";
    }
    closeList();
    if (inCode) html += '<pre><div class="ph"><span>' + esc(lang || "code") + '</span></div><code>' + code + "</code></pre>";
    return html || "(trống)";
  }
  function srcChips(t) {
    var out = [];
    var re = /(LMS Course:[^,\n]+|Course Lesson:[^,\n]+|LMS Quiz:[^,\n]+)/g, m;
    while ((m = re.exec(t || "")) && out.length < 6) out.push(m[1].trim());
    if (!out.length) return "";
    return '<div class="acp-src">' + out.map(function (s) { return "<span>" + esc(s) + "</span>"; }).join("") + "</div>";
  }
  function assistantActions(box) {
    var bar = document.createElement("div");
    bar.className = "acp-actions";
    var copyBtn = document.createElement("button");
    copyBtn.type = "button"; copyBtn.title = "Sao chép"; copyBtn.textContent = "⧉";
    copyBtn.onclick = function () {
      var text = box.querySelector(".body") ? box.querySelector(".body").innerText : "";
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text);
        else { var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
        copyBtn.textContent = "✓"; setTimeout(function () { copyBtn.textContent = "⧉"; }, 1200);
      } catch (e) { /* clipboard có thể bị chặn */ }
    };
    var good = document.createElement("button");
    good.type = "button"; good.title = "Hữu ích"; good.textContent = "♡";
    good.onclick = function () { good.classList.toggle("on"); good.textContent = good.classList.contains("on") ? "♥" : "♡"; };
    var bad = document.createElement("button");
    bad.type = "button"; bad.title = "Chưa tốt"; bad.textContent = "☹";
    bad.onclick = function () { bad.classList.toggle("on"); };
    var retry = document.createElement("button");
    retry.type = "button"; retry.title = "Thử lại"; retry.textContent = "↻";
    retry.onclick = function () { var u = msgs.querySelector(".acp-u:last-of-type"); if (u) send(u.textContent); };
    bar.appendChild(copyBtn); bar.appendChild(addBtn2("+")); bar.appendChild(good); bar.appendChild(bad);
    box.appendChild(bar);
    function addBtn2(t) { var b = document.createElement("button"); b.type = "button"; b.textContent = t; b.title = "Chèn"; b.onclick = function () { input.value += box.querySelector(".body").innerText.slice(0, 400); input.focus(); syncSend(); }; return b; }
  }
  function addWelcome() {
    var d = document.createElement("div");
    d.className = "acp-welcome";
    d.innerHTML =
      '<div class="acp-welcome-icon">✦</div>' +
      '<h2>Tôi có thể giúp gì hôm nay?</h2>' +
      '<div class="acp-suggestions">' +
      '<button class="acp-suggestion" data-q="Tóm tắt bài học này cho tôi"><span class="acp-suggestion-mark">✦</span><span>Tóm tắt bài học này</span></button>' +
      '<button class="acp-suggestion" data-q="Tiến độ học của tôi thế nào?"><span class="acp-suggestion-mark">◌</span><span>Kiểm tra tiến độ học tập</span></button>' +
      '<button class="acp-suggestion" data-q="Tạo 3 câu quiz nháp cho bài này"><span class="acp-suggestion-mark">Aa</span><span>Tạo quiz cho bài học</span></button>' +
      '<button class="acp-suggestion" data-q="Giải thích nội dung bài học này"><span class="acp-suggestion-mark">⌕</span><span>Giải thích nội dung này</span></button>' +
      '</div>';
    d.querySelectorAll(".acp-suggestion").forEach(function (b) {
      b.onclick = function () { send(b.dataset.q); };
    });
    msgs.appendChild(d);
  }
  function addUser(t) {
    var welcome = msgs.querySelector(".acp-welcome");
    if (welcome) welcome.remove();
    var d = document.createElement("div");
    d.className = "acp-u"; d.textContent = t;
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
  }
  function addAssistant(answer, calls, approvals) {
    var d = document.createElement("div");
    d.className = "acp-a";
    var html = '<div class="body">' + md(answer || "(trống)") + "</div>" + srcChips(answer);
    d.innerHTML = html;
    (approvals || []).forEach(function (a) {
      var box = document.createElement("div");
      box.className = "acp-ap";
      box.innerHTML = "Cần phê duyệt: <b>" + esc(a.tool) + "</b><br><span style='font-size:11px'>id=" + esc(a.approval_id) + "</span> ";
      var b = document.createElement("button");
      b.textContent = "Duyệt & chạy";
      b.onclick = function () { approve(a.approval_id, b); };
      box.appendChild(b);
      d.appendChild(box);
    });
    assistantActions(d);
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
  }
  function approve(id, btn) {
    btn.disabled = true; btn.textContent = "…";
    fetch(GATEWAY + "/approve/" + encodeURIComponent(id), { method: "POST", credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        btn.textContent = "Đã duyệt ✓";
        addAssistant("Đã thực hiện sau phê duyệt: " + JSON.stringify(j.result).slice(0, 800), [], []);
      })
      .catch(function () { btn.textContent = "Lỗi, thử lại"; btn.disabled = false; });
  }
  function send(text) {
    text = (text || input.value || "").trim();
    if (!text) return;
    input.value = "";
    input.style.height = "auto";
    syncSend();
    if (msgs.querySelector(".acp-welcome")) titleEl.textContent = text.length > 28 ? text.slice(0, 28) + "…" : text;
    addUser(text);
    typing.style.display = "block"; sendBtn.disabled = true;
    var t0 = Date.now();
    fetch(GATEWAY + "/chat/stream", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: CONVERSATION_ID, page: location.pathname + location.search, mode: CURRENT_MODE })
    }).then(function (r) {
      if (!r.ok || !r.body || !r.body.getReader) return r.json().then(function (j) { addAssistant(j.answer, j.tool_calls, j.approvals, j.timings); });
      return streamSSE(r.body.getReader(), t0);
    }).catch(function (e) {
      addAssistant("Mất kết nối gateway " + GATEWAY + " (" + e.message + "). Kiểm tra server python -m gateway.api.server.", [], []);
    }).finally(function () { typing.style.display = "none"; sendBtn.disabled = false; input.focus(); });
  }
  function streamSSE(reader, t0) {
    var dec = new TextDecoder();
    var buf = "", evName = "";
    var box = null, bodyEl = null, full = "";
    function newBox() {
      box = document.createElement("div"); box.className = "acp-a";
      box.innerHTML = '<div class="body"><span class="acp-cursor"></span></div>';
      msgs.appendChild(box); msgs.scrollTop = msgs.scrollHeight;
      bodyEl = box.querySelector(".body");
    }
    newBox();
    function paint() { bodyEl.innerHTML = md(full) + '<span class="acp-cursor"></span>'; msgs.scrollTop = msgs.scrollHeight; }
    function onEvent(ev) {
      if (ev.t === "token") { full += ev.text || ""; paint(); }
      else if (ev.t === "thought" || ev.t === "tool") { return; }
      else if (ev.t === "round" && ev.n > 1 && full) { full += "\n\n— vòng " + ev.n + " —\n"; paint(); }
      else if (ev.t === "done") { finishBox(ev); }
    }
    function finishBox(ev) {
      var cur = box.querySelector(".acp-cursor"); if (cur) cur.remove();
      bodyEl.innerHTML = md(ev.answer || full || "(trống)");
      bodyEl.insertAdjacentHTML("afterend", srcChips(ev.answer || full));
      (ev.approvals || []).forEach(function (a) {
        var ab = document.createElement("div"); ab.className = "acp-ap";
        ab.innerHTML = "Cần phê duyệt: <b>" + esc(a.tool) + "</b><br><span style='font-size:11px'>id=" + esc(a.approval_id) + "</span> ";
        var b = document.createElement("button"); b.textContent = "Duyệt & chạy";
        b.onclick = function () { approve(a.approval_id, b); };
        ab.appendChild(b); box.appendChild(ab);
      });
      assistantActions(box);
      msgs.scrollTop = msgs.scrollHeight;
    }
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) return;
        buf += dec.decode(r.value, { stream: true });
        var idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          var raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
          var data = "";
          raw.split("\n").forEach(function (ln) {
            if (ln.indexOf("event:") === 0) evName = ln.slice(6).trim();
            else if (ln.indexOf("data:") === 0) data += ln.slice(5).trim();
          });
          if (data) { try { onEvent(JSON.parse(data)); } catch (e) { /* bỏ chunk lỗi */ } }
        }
        return pump();
      });
    }
    return pump();
  }
  function fmtMs(v) { return (v == null ? "–" : (v / 1000).toFixed(1) + "s"); }
  function syncShellBody() {
    var shellOpen = side.classList.contains("open") && side.classList.contains("shell");
    document.body.classList.toggle("acp-shell-open", shellOpen);
    fab.style.display = shellOpen ? "none" : "";
    if (shellOpen) document.documentElement.style.setProperty("--acp-shell-w", side.getBoundingClientRect().width + "px");
    else document.documentElement.style.removeProperty("--acp-shell-w");
  }
  function setOpen(on) {
    side.classList.toggle("open", !!on);
    document.body.classList.toggle("acp-open", !!on);
    syncShellBody();
    if (on) { syncUiChrome(); syncSend(); input.focus(); }
  }
  function toggleShell() {
    var on = side.classList.toggle("shell");
    side.style.removeProperty("--acp-w");
    side.querySelector("#acp-wide").textContent = on ? "◧" : "▣";
    syncShellBody();
    if (side.classList.contains("open")) input.focus();
  }
  (function () { // kéo mép trái để đổi rộng, giữ nguyên shell full-height
    var grip = side.querySelector("#acp-grip"), startX = 0, startW = 0, drag = false;
    function w() { return side.getBoundingClientRect().width; }
    grip.addEventListener("mousedown", function (e) { drag = true; startX = e.clientX; startW = w(); e.preventDefault(); });
    document.addEventListener("mousemove", function (e) {
      if (!drag) return;
      var nw = Math.round(Math.min(window.innerWidth - 32, Math.max(280, startW + (startX - e.clientX))));
      side.style.setProperty("--acp-w", nw + "px");
      syncShellBody();
    });
    document.addEventListener("mouseup", function () { drag = false; });
  })();
  fab.onclick = function () { setOpen(!side.classList.contains("open")); };
  side.querySelector("#acp-wide").onclick = toggleShell;
  side.querySelector("#acp-x").onclick = function () { setOpen(false); };
  side.querySelector("#acp-new").onclick = function () {
    CONVERSATION_ID = newConversationId();
    try { sessionStorage.setItem("acp-conversation-id", CONVERSATION_ID); } catch (e) { /* ignore */ }
    msgs.innerHTML = "";
    input.value = "";
    input.style.height = "auto";
    titleEl.textContent = "New AI chat";
    addWelcome();
    syncSend();
    input.focus();
  };
  function closePop() { pop.classList.remove("show"); pop.innerHTML = ""; }
  function openPop(items) {
    pop.innerHTML = "";
    items.forEach(function (it) {
      var b = document.createElement("button");
      b.type = "button";
      b.innerHTML = "<span>" + it[0] + "</span><span>" + it[1] + "</span>";
      b.onclick = function () { closePop(); it[2](); };
      pop.appendChild(b);
    });
    pop.classList.add("show");
  }
  chip.onclick = function (e) { e.stopPropagation(); chip.classList.toggle("off"); };
  addBtn.onclick = function (e) {
    e.stopPropagation();
    if (pop.classList.contains("show")) { closePop(); return; }
    openPop([
      ["◎", "Dùng ngữ cảnh LMS hiện tại", function () { chip.classList.remove("off"); syncUiChrome(); }],
      ["✦", "Tóm tắt bài học này", function () { send("Tóm tắt bài học này cho tôi"); }],
      ["◌", "Kiểm tra tiến độ học tập", function () { send("Tiến độ học của tôi thế nào?"); }]
    ]);
  };
  modeBtn.onclick = function (e) {
    e.stopPropagation();
    if (pop.classList.contains("show")) { closePop(); return; }
    openPop([
      ["⚙", "Auto: trả lời toàn diện", function () { CURRENT_MODE = "chat"; autoBtn.textContent = "Auto"; }],
      ["F", "Feynman: tôi giảng lại", function () { CURRENT_MODE = "feynman"; autoBtn.textContent = "Feynman"; send("Hãy bắt đầu phiên Feynman cho bài này."); }],
      ["?", "Kiểm tra hội thoại", function () { CURRENT_MODE = "check"; autoBtn.textContent = "Kiểm tra"; send("Hãy bắt đầu kiểm tra hội thoại cho bài này."); }],
      ["✓", "Chấm phiên học hiện tại", function () {
        if (!CURRENT_CONCEPT || CURRENT_MODE === "chat") { addAssistant("Hãy mở bài có concept đã duyệt và chọn Feynman hoặc Kiểm tra trước.", [], []); return; }
        fetch(GATEWAY + "/learning/session", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: CURRENT_MODE, concept_id: CURRENT_CONCEPT, transcript: msgs.innerText.slice(0, 12000) }) })
          .then(function (r) { return r.json(); }).then(function (d) { addAssistant("Điểm phiên: " + Math.round(100 * d.score) + "%\n" + ((d.rubric || {}).feedback || "Đã lưu evidence ngoài LMS."), [], []); });
      }],
      ["✦", "Tóm tắt ngắn gọn", function () { CURRENT_MODE = "chat"; autoBtn.textContent = "Summary"; send("Tóm tắt ngắn gọn bài học này"); }],
      ["⌕", "Giải thích chi tiết", function () { CURRENT_MODE = "chat"; autoBtn.textContent = "Explain"; send("Giải thích chi tiết nội dung bài học này"); }]
    ]);
  };
  autoBtn.onclick = function () { autoBtn.textContent = autoBtn.textContent === "Auto" ? "Auto ✓" : "Auto"; };
  side.querySelector("#acp-share").onclick = function () {
    try {
      var text = msgs.innerText.slice(0, 2000);
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text);
    } catch (e) { /* clipboard có thể bị chặn */ }
  };
  side.querySelector("#acp-pin").onclick = function () { if (!side.classList.contains("open")) setOpen(true); toggleShell(); };
  side.querySelector("#acp-more").onclick = function () { side.querySelector("#acp-new").click(); };
  document.addEventListener("click", function () { closePop(); });
  sendBtn.onclick = function () { send(); };
  input.onkeydown = function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  input.oninput = function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 150) + "px";
    syncSend();
  };
  document.addEventListener("keydown", function (e) {
    if (e.altKey && (e.key === "c" || e.key === "C")) { e.preventDefault(); fab.onclick(); }
    if (e.altKey && (e.key === "m" || e.key === "M")) { e.preventDefault(); if (!side.classList.contains("open")) setOpen(true); toggleShell(); }
  });
  fetch(GATEWAY + "/identity", { credentials: "same-origin" })
    .then(function (response) {
      if (!response.ok) throw new Error("unauthorized");
      return response.json();
    })
    .then(function (identity) { CURRENT_IDENTITY = identity; syncUiChrome(); refreshInsight(); })
    .catch(function () { CURRENT_IDENTITY = { user: "Guest", role: "student", roles: [] }; syncUiChrome(); });
  syncUiChrome();
  syncSend();
  if (!msgs.children.length) addWelcome();
  var lastRoute = location.pathname + location.search;
  setInterval(function () {
    var nextRoute = location.pathname + location.search;
    if (nextRoute !== lastRoute) { lastRoute = nextRoute; syncUiChrome(); refreshInsight(); }
  }, 1000);
})();
