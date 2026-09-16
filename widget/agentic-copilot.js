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
  var isProxy = location.port === "8080" || (location.host && location.host.indexOf(":8080") >= 0);
  var GATEWAY = isProxy ? "/ai" : ((me && me.dataset.gateway) ||
    (scriptUrl && scriptUrl.pathname.indexOf("/ai/") === 0 ? "/ai" : (scriptUrl && scriptUrl.origin)) || "/ai");
  var CURRENT_MODE = "chat";
  var CURRENT_EFFORT = "medium";
  var CURRENT_APPROVAL_MODE = "ask";
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
    "body.acp-shell-open{padding-right:var(--acp-shell-w,560px)!important;overflow-x:hidden;transition:padding-right .2s ease}",
    "body.acp-shell-open #app > .w-screen{width:calc(100vw - var(--acp-shell-w,560px))!important;transition:width .2s ease}",
    "body.acp-shell-open #acp-fab{display:none}",
    "#acp-grip{display:none;position:absolute;left:-5px;top:0;bottom:0;width:10px;cursor:ew-resize;z-index:2}",
    "#acp-grip:before{content:'';position:absolute;left:4px;top:50%;width:3px;height:44px;transform:translateY(-50%);border-radius:3px;background:#3a3a3a}",
    "#acp-grip:hover{background:rgba(47,129,247,.12)}#acp-grip:hover:before{background:#2f81f7}",
    "#acp-side.shell{--acp-w:min(560px,calc(100vw - 48px));top:0;right:0;bottom:0;height:auto;max-width:none;",
    "border-top:0;border-right:0;border-bottom:0;border-radius:0;transform:translateX(102%) scale(1)}",
    "#acp-side.shell.open{transform:none}#acp-side.shell #acp-grip{display:block}",
    "#acp-canvas{position:fixed;top:0;bottom:0;left:0;right:var(--acp-shell-w,560px);z-index:2147483000;background:#111;color:#e6e6e6;display:none;overflow:auto;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}",
    "#acp-canvas.show{display:block}.acp-canvas-head{display:flex;align-items:center;justify-content:space-between;padding:16px 22px;border-bottom:1px solid #292929}",
    ".acp-canvas-head h2{margin:0;font-size:18px;font-weight:600}.acp-canvas-close{background:transparent;border:0;color:#aaa;font-size:22px;cursor:pointer}",
    ".acp-canvas-body{max-width:760px;margin:0 auto;padding:24px}.acp-canvas-card{background:#1b1b1b;border:1px solid #303030;border-radius:12px;padding:16px;margin-bottom:12px}",
    ".acp-canvas-card h3{margin:0 0 8px;color:#fff;font-size:15px}.acp-canvas-card p{margin:6px 0;color:#c6c6c6;line-height:1.55}",
    ".acp-canvas-card pre{white-space:pre-wrap;color:#cfcfcf;font:inherit;line-height:1.55}.acp-canvas-card button,.acp-action-card button{background:#2f81f7;color:#fff;border:0;border-radius:7px;padding:7px 11px;cursor:pointer}",
    ".acp-action-card{margin-top:12px;padding:12px;border:1px solid #31527d;background:#152337;border-radius:10px;color:#d9e7fa}.acp-action-card h4{margin:0 0 5px;color:#fff;font-size:13px}.acp-action-card p{margin:4px 0;line-height:1.45}.acp-action-changes{margin:8px 0;padding-left:18px;color:#b8c6d9;font-size:12px}.acp-action-card button{margin:4px 6px 0 0;font-size:12px}.acp-action-card button.secondary{background:#283342;color:#d6e2f0}",
    "#acp-head{position:relative;display:flex;align-items:center;gap:6px;padding:8px 10px 8px 12px;border-bottom:1px solid #1f1f1f;background:#111}",
    "#acp-avatar{width:26px;height:26px;flex:0 0 auto;border-radius:50%;background:#e8e8e8;color:#333;",
    "display:flex;align-items:center;justify-content:center;font-size:13px}",
    "#acp-head-main{flex:0 0 auto;min-width:0;max-width:190px;display:flex;align-items:center;gap:4px;cursor:pointer;background:#222;border:0;",
    "padding:5px 9px;border-radius:7px;text-align:left;font:inherit;color:#ececec;transition:background .15s ease}",
    "#acp-head-main:hover{background:#2a2a2a;color:#fff}",
    "#acp-title{flex:1;min-width:0;font-size:13px;font-weight:500;color:inherit;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".acp-chevron{color:#8a8a8a;font-size:12px}",
    "#acp-hist{position:absolute;top:44px;left:10px;width:min(340px,calc(100% - 20px));background:#1e1e1e;border:1px solid #333;border-radius:12px;",
    "box-shadow:0 20px 50px rgba(0,0,0,.6);padding:6px;display:none;flex-direction:column;gap:1px;z-index:6;max-height:380px;overflow-y:auto}",
    "#acp-hist.show{display:flex}",
    ".acp-hist-search{display:flex;align-items:center;gap:8px;padding:8px 10px;color:#8a8a8a;font-size:12.5px}",
    ".acp-hist-search input{flex:1;background:transparent;border:0;outline:none;color:#eee;font:inherit;font-size:12.5px;min-width:0}",
    ".acp-hist-group{padding:10px 10px 4px;color:#8a8a8a;font-size:12px;font-weight:500}",
    ".acp-hist-item{display:flex;align-items:center;gap:6px;width:100%;background:transparent;border:0;border-radius:8px;",
    "color:#e2e2e2;font:inherit;font-size:13.5px;text-align:left;padding:9px 10px;cursor:pointer}",
    ".acp-hist-item:hover{background:#2a2a2a;color:#fff}",
    ".acp-hist-item .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".acp-hist-item .x{background:transparent;border:0;color:#666;font-size:14px;cursor:pointer;padding:2px 6px;border-radius:5px;opacity:0;transition:opacity .15s ease}",
    ".acp-hist-item:hover .x{opacity:1}.acp-hist-item .x:hover{background:#3a2a2a;color:#ff9d9d}",
    ".acp-hist-empty{padding:12px;color:#777;font-size:12.5px;text-align:center}",
    ".acp-hbtn{display:grid;place-items:center;width:28px;height:28px;padding:0;background:transparent;border:0;",
    "color:#9a9a9a;border-radius:6px;font-size:15px;line-height:1;cursor:pointer;transition:background .15s ease,color .15s ease}",
    ".acp-hbtn:hover{background:#262626;color:#f0f0f0}",
    "#acp-msgs{flex:1;overflow-y:auto;padding:20px 22px 18px;background:#111;display:flex;flex-direction:column;gap:18px}",
    "#acp-msgs::-webkit-scrollbar{width:8px}#acp-msgs::-webkit-scrollbar-thumb{background:#333;border:2px solid #111;border-radius:8px}",
    ".acp-welcome{flex:1;display:flex;flex-direction:column;justify-content:center;padding:20px 10px 30px;max-width:360px;margin:0 auto;width:100%;box-sizing:border-box}",
    ".acp-welcome-icon{width:44px;height:44px;border-radius:50%;background:#e8e8e8;color:#333;display:grid;place-items:center;font-size:21px;margin:0 0 14px}",
    ".acp-welcome h2{margin:0;color:#f0f0f0;font-size:22px;font-weight:600;letter-spacing:-.02em;line-height:1.25}",
    ".acp-welcome p{margin:6px 0 0;color:#8a8a8a;font-size:13px}",
    ".acp-suggestions{width:100%;margin-top:18px;display:flex;flex-direction:column;gap:2px;text-align:left}",
    ".acp-suggestion{display:flex;align-items:center;gap:11px;width:100%;padding:9px 10px;background:transparent;border:0;border-radius:9px;",
    "color:#c9c9c9;font:inherit;font-size:13.5px;cursor:pointer;transition:background .15s ease,color .15s ease}",
    ".acp-suggestion:hover{background:#1f1f1f;color:#fff}.acp-suggestion-mark{width:20px;text-align:center;color:#8a8a8a;font-size:15px;flex:0 0 auto}",
    ".acp-suggestion .tag{background:#1d3a5f;color:#6aa5ff;border-radius:4px;padding:1px 5px;font-size:10.5px;margin-left:6px}",
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
    ".acp-think{margin:0 0 8px;background:transparent;border:0}",
    ".acp-think summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:7px;color:#8a8a8a;font-size:12.5px;padding:2px 0}",
    ".acp-think summary::-webkit-details-marker{display:none}",
    ".acp-think summary:hover{color:#d5d5d5}",
    ".acp-think .th-spin{width:13px;height:13px;flex:0 0 auto;border-radius:50%;border:2px solid #3a3a3a;border-top-color:#8a8a8a;animation:acpspin .8s linear infinite}",
    ".acp-think.done .th-spin{animation:none;border-color:#2f81f7;background:#2f81f7;position:relative}",
    "@keyframes acpspin{to{transform:rotate(360deg)}}",
    ".acp-think .th-label{animation:acppulse 1.6s ease-in-out infinite}",
    ".acp-think.done .th-label{animation:none;color:#8a8a8a}",
    "@keyframes acppulse{0%,100%{opacity:.45}50%{opacity:1}}",
    ".acp-think .th-body{margin:6px 0 2px 20px;padding-left:10px;border-left:1px solid #2c2c2c;color:#9a9a9a;font-size:12.5px;line-height:1.6;",
    "white-space:pre-wrap;max-height:220px;overflow-y:auto}",
    ".acp-toolrow{display:flex;align-items:center;gap:7px;margin:6px 0 2px 20px;padding-left:10px;border-left:1px solid #2c2c2c;",
    "color:#8a8a8a;font-size:12.5px}",
    ".acp-toolrow .tick{color:#4caf7d;font-size:13px}",
    ".acp-toolrow .tname{color:#c9c9c9;font-size:12.5px}",
    "#acp-insight{margin:10px 14px 0;padding:11px 12px;border:1px solid #285247;background:#142720;border-radius:9px;color:#d9eee7;font-size:12px;line-height:1.45}",
    "#acp-insight[hidden]{display:none}#acp-insight b{color:#fff}#acp-insight button{margin:8px 6px 0 0;border:1px solid #3c665b;background:transparent;color:#bfe2d6;border-radius:7px;padding:5px 8px;cursor:pointer}",
    ".acp-ap{margin-top:10px;border:1px solid #554507;background:#221d0c;border-radius:8px;padding:10px;color:#e7c96f;font-size:12.5px}",
    ".acp-ap button{margin-top:8px;background:#2f81f7;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;font-weight:600}",
    "#acp-type{display:none;padding:0 20px 8px;background:#111;color:#777;font-size:12px}",
    "#acp-type i{display:inline-block;width:4px;height:4px;border-radius:50%;background:#777;margin-right:3px;animation:acpb 1s infinite}",
    "@keyframes acpb{0%,100%{opacity:.25}50%{opacity:1}}",
    "#acp-inbar{padding:6px 12px 12px;background:#111;position:relative}",
    ".acp-composer{border:1px solid #2e2e2e;border-radius:14px;background:#1a1a1a;transition:border-color .15s ease,box-shadow .15s ease}",
    ".acp-composer:focus-within{border-color:#2f81f7;box-shadow:0 0 0 1px #2f81f7}",
    "#acp-side textarea,#acp-side textarea:focus,#acp-side button:focus,#acp-side select:focus{outline:none!important;box-shadow:none!important;",
    "--tw-ring-offset-shadow:0 0 #0000!important;--tw-ring-shadow:0 0 #0000!important;--tw-ring-offset-width:0px!important}",
    ".acp-chiprow{padding:10px 10px 0;display:flex;gap:6px;flex-wrap:wrap}",
    "#acp-chip{display:inline-flex;align-items:center;gap:6px;background:#222;border:1px solid #383838;color:#cfcfcf;",
    "border-radius:14px;padding:5px 11px;font-size:11.5px;cursor:pointer;max-width:100%;transition:opacity .15s ease,background .15s ease}",
    "#acp-chip:hover{background:#2a2a2a}#acp-chip.off{opacity:.4}#acp-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px}",
    "#acp-in{display:block;width:100%;box-sizing:border-box;background:transparent!important;color:#eee;border:0!important;box-shadow:none!important;",
    "padding:10px 12px 2px;font:inherit;font-size:14px;resize:none;outline:none!important;min-height:28px;max-height:150px;line-height:1.5}",
    "#acp-foot{display:flex;justify-content:space-between;align-items:center;padding:4px 6px 6px}",
    ".acp-fleft,.acp-fright{display:flex;align-items:center;gap:2px}",
    ".acp-toolbtn{display:grid;place-items:center;width:28px;height:28px;background:transparent;border:0;border-radius:7px;",
    "color:#9a9a9a;font-size:16px;cursor:pointer;transition:background .15s ease,color .15s ease}",
    ".acp-toolbtn:hover{background:#2b2b2b;color:#eee}.acp-toolbtn.on{background:#2b2b2b;color:#fff}",
    "#acp-effort{display:inline-flex;align-items:center;gap:6px;background:#222;border:1px solid #333;color:#c9c9c9;font-size:12px;font-weight:500;padding:5px 9px;border-radius:14px;cursor:pointer;min-width:96px;justify-content:center}",
    "#acp-effort:hover{background:#2b2b2b;color:#fff;border-color:#444}",
    "#acp-approval{display:inline-flex;align-items:center;gap:5px;background:transparent;border:0;color:#9a9a9a;font-size:12px;padding:5px 7px;border-radius:7px;cursor:pointer}",
    "#acp-approval:hover{background:#2b2b2b;color:#eee}#acp-approval .mode{max-width:92px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    "#acp-pop.acp-approval-pop{left:12px;right:12px;bottom:54px;padding:0;background:#f8f8f8;border-color:#d5d5d5;color:#262626;overflow:hidden}",
    ".acp-approval-title{padding:13px 15px 9px;font-size:13px;color:#5f6368;border-bottom:1px solid #e2e2e2}.acp-approval-option{display:grid!important;grid-template-columns:22px 1fr 18px;align-items:start!important;gap:9px!important;padding:11px 14px!important;border-radius:0!important;color:#252525!important}",
    ".acp-approval-option:hover{background:#ededed!important}.acp-approval-option .icon{font-size:16px;line-height:20px}.acp-approval-option .label{display:block;font-size:14px;color:#242424}.acp-approval-option .desc{display:block;margin-top:2px;font-size:12px;line-height:1.35;color:#777}.acp-approval-option .check{font-size:15px;color:#202124}.acp-approval-option.danger .label{color:#c43b18}.acp-approval-option.danger .desc{color:#a4523e}",
    "#acp-send{display:grid;place-items:center;width:28px;height:28px;padding:0;background:#2e2e2e;color:#858585;border:0;",
    "border-radius:50%;font-size:15px;line-height:1;cursor:pointer;transition:background .15s ease,color .15s ease}",
    "#acp-send.ready{background:#e8e8e8;color:#111}#acp-send.ready:hover{background:#fff}#acp-send:disabled{cursor:default;opacity:.8}",
    "#acp-pop{position:absolute;left:12px;right:12px;bottom:86px;background:#222;border:1px solid #3a3a3a;border-radius:10px;",
    "box-shadow:0 16px 40px rgba(0,0,0,.5);padding:6px;display:none;flex-direction:column;gap:2px;z-index:5}",
    "#acp-pop.show{display:flex}#acp-pop button{display:flex;gap:10px;align-items:center;background:transparent;border:0;",
    "color:#ddd;font:inherit;font-size:13px;text-align:left;padding:9px 10px;border-radius:7px;cursor:pointer}",
    "#acp-pop button:hover{background:#2e2e2e;color:#fff}",
    "#acp-pop.acp-effort-pop{left:auto;right:12px;bottom:54px;width:236px;max-width:calc(100% - 24px);box-sizing:border-box;padding:10px;background:#202020;border-color:#3a3a3a;display:block}",
    ".acp-effort-head{display:flex;align-items:center;gap:8px}",
    ".acp-effort-bolt{width:18px;height:18px;display:inline-flex;align-items:center;justify-content:center;color:#aeb5bd;line-height:0;flex:0 0 auto}",
    ".acp-effort-bolt .acp-icon{width:16px;height:16px}",
    ".acp-effort-current{color:#1677c8;font-size:14px;font-weight:600;line-height:18px}",
    ".acp-effort-current span{color:#9aa0a6;font-size:14px;margin-left:3px;line-height:0}",
    ".acp-effort-range{position:relative;margin:11px 2px 0;padding:0}",
    ".acp-effort-range input{display:block;width:100%;height:18px;margin:0;appearance:none;background:transparent;cursor:pointer}",
    ".acp-effort-range input::-webkit-slider-runnable-track{height:6px;border-radius:6px;background:linear-gradient(90deg,#0878d1 var(--effort-fill,50%),#d7dce1 var(--effort-fill,50%))}",
    ".acp-effort-range input::-webkit-slider-thumb{appearance:none;width:20px;height:20px;margin-top:-7px;border-radius:50%;border:1px solid #ccd3da;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.24)}",
    ".acp-effort-range input::-moz-range-track{height:6px;border-radius:6px;background:#d7dce1}",
    ".acp-effort-range input::-moz-range-progress{height:6px;border-radius:6px;background:#0878d1}",
    ".acp-effort-range input::-moz-range-thumb{width:18px;height:18px;border-radius:50%;border:1px solid #ccd3da;background:#fff}",
    ".acp-effort-dots{position:absolute;left:8px;right:8px;top:7px;display:flex;justify-content:space-between;pointer-events:none}",
    ".acp-effort-dots i{width:4px;height:4px;border-radius:50%;background:rgba(255,255,255,.34)}",
    ".acp-effort-labels{display:flex;justify-content:space-between;gap:4px;margin:4px 0 0;color:#8d969e;font-size:9.5px;line-height:12px}",
    ".acp-effort-labels span{min-width:0;text-align:center;white-space:nowrap}",
    "@media(max-width:560px){#acp-side{right:12px;bottom:72px;width:calc(100vw - 24px);max-width:none;height:min(760px,calc(100vh - 88px))}",
    "#acp-side.shell{right:0;top:0;bottom:0;width:100vw;max-width:100vw;border-radius:0}body.acp-shell-open{padding-right:0!important;overflow-x:hidden}",
    "body.acp-shell-open #app > .w-screen{width:100vw!important}#acp-fab{right:16px;bottom:16px}}",
    ".acp-cursor{display:inline-block;width:7px;height:15px;background:#2f81f7;vertical-align:-2px;margin-left:2px;animation:acpb .8s infinite}",
    ":host{--acp-surface:var(--surface-base,#fff);--acp-surface-muted:var(--surface-gray-1,#f8f9fa);--acp-surface-raised:var(--surface-gray-2,#f1f3f5);--acp-border:var(--outline-gray-2,#e2e5e8);--acp-border-strong:var(--outline-gray-3,#cbd0d5);--acp-ink:var(--ink-gray-8,#202124);--acp-ink-muted:var(--ink-gray-6,#687078);--acp-ink-subtle:var(--ink-gray-5,#8a9096);--acp-accent:var(--blue-600,#3b82f6);--acp-accent-soft:var(--surface-blue-1,#eff6ff);--acp-danger:var(--surface-red-6,#d64545);color-scheme:light}",
    ":host([data-theme='dark']){color-scheme:dark}",
    "#acp-side,#acp-canvas{background:var(--acp-surface);color:var(--acp-ink);border-color:var(--acp-border)}",
    "#acp-head,#acp-msgs,#acp-inbar,#acp-type{background:var(--acp-surface);color:var(--acp-ink);border-color:var(--acp-border)}",
    "#acp-head{background:var(--acp-surface-muted)}#acp-title,.acp-a{color:var(--acp-ink)}#acp-head-main{background:transparent;color:var(--acp-ink)}#acp-head-main:hover{background:var(--acp-surface-raised)}",
    ".acp-hbtn,.acp-toolbtn,#acp-approval{color:var(--acp-ink-muted)}.acp-hbtn:hover,.acp-toolbtn:hover,#acp-approval:hover{background:var(--acp-surface-raised);color:var(--acp-ink)}",
    "#acp-pop,#acp-hist{background:var(--acp-surface);border-color:var(--acp-border-strong);color:var(--acp-ink)}#acp-pop button{color:var(--acp-ink)}#acp-pop button:hover{background:var(--acp-surface-raised)}",
    "#acp-in{color:var(--acp-ink)}#acp-in::placeholder{color:var(--acp-ink-subtle)}#acp-chip{background:var(--acp-surface-muted);border-color:var(--acp-border);color:var(--acp-ink-muted)}#acp-chip:hover{background:var(--acp-surface-raised)}",
    "#acp-effort{background:var(--acp-surface-muted);border-color:var(--acp-border);color:var(--acp-ink-muted)}#acp-effort:hover{background:var(--acp-surface-raised);color:var(--acp-ink);border-color:var(--acp-border-strong)}",
    "#acp-send.ready{background:var(--acp-accent);color:#fff}#acp-send{background:var(--acp-surface-raised);color:var(--acp-ink-muted)}",
    ".acp-u{background:var(--acp-surface-raised);color:var(--acp-ink)}.acp-action-card{background:var(--acp-accent-soft);border-color:var(--acp-accent);color:var(--acp-ink)}.acp-action-card h4{color:var(--acp-ink)}.acp-action-card p,.acp-action-changes{color:var(--acp-ink-muted)}.acp-action-card button{background:var(--acp-accent)}.acp-action-card button.secondary{background:var(--acp-surface-raised);color:var(--acp-ink)}",
    "#acp-insight{background:var(--surface-green-1,var(--acp-surface-muted));border-color:var(--outline-green-3,var(--acp-border));color:var(--acp-ink-muted)}#acp-insight b{color:var(--acp-ink)}",
    ".acp-canvas-head{border-color:var(--acp-border)}.acp-canvas-card{background:var(--acp-surface-muted);border-color:var(--acp-border)}.acp-canvas-card h3{color:var(--acp-ink)}.acp-canvas-card p,.acp-canvas-card pre{color:var(--acp-ink-muted)}",
    "#acp-pop.acp-approval-pop{left:auto;right:8px;bottom:52px;width:296px;max-width:calc(100% - 16px);padding:0;background:var(--acp-surface);border-color:var(--acp-border);border-radius:10px;box-shadow:0 12px 30px rgba(0,0,0,.14);overflow:hidden}",
    ".acp-approval-title{padding:10px 12px 8px;font-size:11px;letter-spacing:.01em;color:var(--acp-ink-muted);border-color:var(--acp-border)}.acp-approval-option{grid-template-columns:18px 1fr 16px!important;gap:7px!important;padding:8px 11px!important;color:var(--acp-ink)!important}.acp-approval-option:hover{background:var(--acp-surface-muted)!important}.acp-approval-option .icon{font-size:14px;line-height:17px;color:var(--acp-ink-muted)}.acp-approval-option .label{font-size:12px;line-height:17px;color:var(--acp-ink)}.acp-approval-option .desc{margin-top:1px;font-size:10.5px;line-height:14px;color:var(--acp-ink-muted)}.acp-approval-option .check{font-size:13px;color:var(--acp-accent)}.acp-approval-option.danger .label{color:var(--acp-danger)}.acp-approval-option.danger .desc{color:var(--acp-danger)}",
    ".acp-ap{background:var(--acp-surface-muted);border-color:var(--outline-amber-6,var(--acp-border));color:var(--ink-amber-10,var(--acp-ink-muted))}.acp-ap button{background:var(--acp-accent)}",
    ".acp-suggestion{color:var(--acp-ink-muted)}.acp-suggestion:hover{background:var(--acp-surface-muted);color:var(--acp-ink)}.acp-welcome h2{color:var(--acp-ink)}.acp-welcome p{color:var(--acp-ink-muted)}",
    "#acp-fab{background:var(--acp-ink);color:var(--acp-surface);border-color:var(--acp-border)}#acp-fab:hover{background:var(--acp-ink-muted)}",
    ".acp-effort-current{color:var(--acp-accent)}.acp-effort-bolt{color:var(--acp-ink-muted)}",
    ".acp-composer{background:var(--acp-surface);border-color:var(--acp-border)}#acp-pop.acp-effort-pop{background:var(--acp-surface);border-color:var(--acp-border)}.acp-effort-range input::-webkit-slider-runnable-track{background:linear-gradient(90deg,var(--acp-accent) var(--effort-fill,78%),var(--acp-surface-raised) var(--effort-fill,78%))}.acp-effort-range input::-moz-range-track{background:var(--acp-surface-raised)}.acp-effort-range input::-moz-range-progress{background:var(--acp-accent)}",
    ":host{font-family:InterVar,ui-sans-serif,system-ui,sans-serif,\"Apple Color Emoji\",\"Segoe UI Emoji\",\"Segoe UI Symbol\",\"Noto Color Emoji\"}",
    "#acp-side,#acp-side *,#acp-canvas,#acp-canvas *{font-family:inherit}",
    ".acp-icon{display:inline-block;width:14px;height:14px;flex:0 0 auto;overflow:visible;vertical-align:middle;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;fill:none}",
    "#acp-fab .acp-icon{width:18px;height:18px}#acp-head-main .acp-chevron,#acp-effort .chev{display:inline-flex;align-items:center;justify-content:center;margin-left:3px;line-height:0}.acp-chevron .acp-icon,#acp-effort .chev .acp-icon{width:16px;height:16px}",
    "#acp-approval .approval-icon{width:14px;height:14px}.acp-approval .mode{font-weight:420}.acp-effort-current .acp-icon{width:14px;height:14px;margin-left:3px;color:var(--acp-ink-muted)}.acp-pop-icon{width:15px;height:15px;color:var(--acp-ink-muted)}",
  ].join("\n");
  var LUCIDE_PATHS = {
    sparkles: '<path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275Z"></path><path d="M5 3v4"></path><path d="M19 17v4"></path><path d="M3 5h4"></path><path d="M17 19h4"></path>',
    chevronDown: '<path d="m6 9 6 6 6-6"></path>',
    chevronRight: '<path d="m9 18 6-6-6-6"></path>',
    plus: '<path d="M5 12h14"></path><path d="M12 5v14"></path>',
    panelRight: '<rect width="18" height="18" x="3" y="3" rx="2"></rect><path d="M15 3v18"></path>',
    moreHorizontal: '<circle cx="5" cy="12" r="1"></circle><circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle>',
    x: '<path d="M18 6 6 18"></path><path d="m6 6 12 12"></path>',
    circleDot: '<circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="3"></circle>',
    shieldCheck: '<path d="M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3Z"></path><path d="m9 12 2 2 4-4"></path>',
    triangleAlert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>',
    arrowUp: '<path d="m5 12 7-7 7 7"></path><path d="M12 19V5"></path>',
    search: '<circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>',
    check: '<path d="m5 12 4 4L19 6"></path>',
    sliders: '<path d="M4 21v-7"></path><path d="M4 10V3"></path><path d="M12 21v-9"></path><path d="M12 8V3"></path><path d="M20 21v-5"></path><path d="M20 12V3"></path><path d="M1 14h6"></path><path d="M9 8h6"></path><path d="M17 16h6"></path>',
    circleHelp: '<circle cx="12" cy="12" r="10"></circle><path d="M9.1 9a3 3 0 1 1 5.8 1c0 2-3 2-3 4"></path><path d="M12 17h.01"></path>',
    messageCircle: '<path d="M7.9 20a9 9 0 1 1 3.7 1.9L7 22Z"></path>',
    bookOpen: '<path d="M12 7v14"></path><path d="M3 18a1 1 0 0 1-1-1V5a2 2 0 0 1 2-2h5a3 3 0 0 1 3 3v15a3 3 0 0 0-3-3Z"></path><path d="M21 18a1 1 0 0 0 1-1V5a2 2 0 0 0-2-2h-5a3 3 0 0 0-3 3v15a3 3 0 0 1 3-3Z"></path>',
  };
  function svgIcon(name, extraClass) {
    var className = "acp-icon" + (extraClass ? " " + extraClass : "");
    return '<svg class="' + className + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + (LUCIDE_PATHS[name] || LUCIDE_PATHS.circleDot) + "</svg>";
  }
  var host = document.createElement("div");
  host.id = "acp-host";
  var root = host.attachShadow({ mode: "open" });
  var st = document.createElement("style");
  st.setAttribute("data-acp", "1");
  st.textContent = css;
  root.appendChild(st);
  var pushStyle = document.createElement("style");
  pushStyle.setAttribute("data-acp", "1");
  pushStyle.textContent = "body.acp-shell-open{padding-right:var(--acp-shell-w,560px)!important;overflow-x:hidden;transition:padding-right .2s ease}" +
    "body.acp-shell-open #app>.w-screen{width:calc(100vw - var(--acp-shell-w,560px))!important;transition:width .2s ease}" +
    "@media(max-width:560px){body.acp-shell-open{padding-right:0!important;overflow-x:hidden}body.acp-shell-open #app>.w-screen{width:100vw!important}}";
  document.head.appendChild(pushStyle);
  var fab = document.createElement("button");
  fab.id = "acp-fab";
  fab.innerHTML = svgIcon("sparkles");
  fab.title = "Trợ lý LMS (Alt+C)";
  var side = document.createElement("div");
  side.id = "acp-side";
  side.innerHTML =
    '<div id="acp-grip" title="kéo để đổi rộng"></div>' +
    '<div id="acp-head">' +
    '<button id="acp-head-main" type="button" title="Xem các cuộc trò chuyện cũ" aria-label="Xem các cuộc trò chuyện cũ" aria-haspopup="true"><span id="acp-title">New AI chat</span><span class="acp-chevron">' + svgIcon("chevronDown") + '</span></button>' +
    '<button id="acp-new" class="acp-hbtn" title="Đoạn chat mới" aria-label="Đoạn chat mới">' + svgIcon("plus") + '</button>' +
    '<button id="acp-wide" class="acp-hbtn" title="rộng/hẹp (Alt+M)" aria-label="Rộng hoặc hẹp">' + svgIcon("panelRight") + '</button>' +
    '<button id="acp-more" class="acp-hbtn" title="Tùy chọn" aria-label="Tùy chọn">' + svgIcon("moreHorizontal") + '</button>' +
    '<button id="acp-x" class="acp-hbtn" title="đóng" aria-label="Đóng">' + svgIcon("x") + '</button></div>' +
    '<div id="acp-hist"></div>' +
    '<div id="acp-msgs"></div><div id="acp-type"><i></i><i></i><i></i> AI đang soạn…</div>' +
    '<div id="acp-inbar"><div id="acp-pop"></div><div class="acp-composer">' +
    '<div class="acp-chiprow"><button id="acp-chip" title="Trạng thái học tập" type="button">' + svgIcon("circleDot") + '<span id="acp-chip-t">LMS</span></button></div>' +
    '<textarea id="acp-in" rows="1" placeholder="Hỏi bất cứ điều gì về khóa học…"></textarea>' +
    '<div id="acp-foot"><div class="acp-fleft"><button id="acp-add" class="acp-toolbtn" title="Thêm" type="button">' + svgIcon("plus") + '</button>' +
    '<button id="acp-mode" class="acp-toolbtn" title="Chế độ" type="button">' + svgIcon("sliders") + '</button><button id="acp-approval" title="Quyền thực thi" type="button">' + svgIcon("shieldCheck", "approval-icon") + '<span class="mode">Hỏi trước</span></button></div>' +
    '<div class="acp-fright"><button id="acp-effort" title="Độ suy luận" type="button">Select effort <span class="chev">' + svgIcon("chevronDown") + '</span></button>' +
    '<button id="acp-send" title="Gửi" aria-label="Gửi" type="button">' + svgIcon("arrowUp") + '</button></div></div></div></div>';
  document.body.appendChild(host);
  function detectLmsTheme() {
    var value = "";
    try { value = document.documentElement.getAttribute("data-theme") || ""; } catch (e) { /* ignore */ }
    if (value === "dark" || value === "light") return value;
    try {
      var preference = localStorage.getItem("themePreference") || localStorage.getItem("theme");
      if (preference === "dark" || preference === "light") return preference;
    } catch (e) { /* storage unavailable */ }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function syncLmsTheme() {
    host.setAttribute("data-theme", detectLmsTheme());
  }
  syncLmsTheme();
  try {
    if (window.__acp_theme_observer) window.__acp_theme_observer.disconnect();
    window.__acp_theme_observer = new MutationObserver(syncLmsTheme);
    window.__acp_theme_observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncLmsTheme);
  } catch (e) { /* theme observation unavailable */ }
  root.appendChild(fab);
  root.appendChild(side);
  var canvas = document.createElement("div");
  canvas.id = "acp-canvas";
  canvas.innerHTML = '<div class="acp-canvas-head"><h2 id="acp-canvas-title">Agentic view</h2><button class="acp-canvas-close" type="button" aria-label="Đóng">' + svgIcon("x") + '</button></div><div class="acp-canvas-body"></div>';
  root.appendChild(canvas);
  var msgs = side.querySelector("#acp-msgs");
  var input = side.querySelector("#acp-in");
  var sendBtn = side.querySelector("#acp-send");
  var typing = side.querySelector("#acp-type");
  var titleEl = side.querySelector("#acp-title");
  var chip = side.querySelector("#acp-chip");
  var chipT = side.querySelector("#acp-chip-t");
  var pop = side.querySelector("#acp-pop");
  var addBtn = side.querySelector("#acp-add");
  var modeBtn = side.querySelector("#acp-mode");
  var approvalBtn = side.querySelector("#acp-approval");
  var canvasTitle = canvas.querySelector("#acp-canvas-title");
  var canvasBody = canvas.querySelector(".acp-canvas-body");
  var effortBtn = side.querySelector("#acp-effort");
  var insight = side.querySelector("#acp-insight");
  var histBox = side.querySelector("#acp-hist");
  histBox.onclick = function (e) { e.stopPropagation(); };
  var headMain = side.querySelector("#acp-head-main");
  sendBtn.innerHTML = svgIcon("arrowUp");
  sendBtn.title = "Gửi câu hỏi";
  function setConversation(id) {
    CONVERSATION_ID = id || newConversationId();
    try { sessionStorage.setItem("acp-conversation-id", CONVERSATION_ID); } catch (e) { /* ignore */ }
  }
  function closeHist() { histBox.classList.remove("show"); }
  function toggleHist(e) {
    if (e) e.stopPropagation();
    closePop();
    if (histBox.classList.contains("show")) { closeHist(); return; }
    loadHistory();
    histBox.classList.add("show");
  }
  function histGroup(ts) {
    var d = new Date(ts * 1000), now = new Date();
    var day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var diff = Math.round((today - day) / 86400000);
    if (diff <= 0) return "Today";
    if (diff === 1) return "Yesterday";
    if (diff <= 7) return "Previous 7 days";
    return "Past 30 days";
  }
  function histItem(s) {
    var item = document.createElement("div");
    item.className = "acp-hist-item";
    item.setAttribute("role", "button");
    item.setAttribute("tabindex", "0");
    item.onclick = function (e) {
      e.stopPropagation();
      closeHist();
      openSession(s.id);
    };
    item.onkeydown = function (e) {
      if (e.target === item && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        e.stopPropagation();
        closeHist();
        openSession(s.id);
      }
    };
    var t = document.createElement("span");
    t.className = "t";
    t.textContent = s.title || "New AI chat";
    var del = document.createElement("button");
    del.type = "button";
    del.className = "x";
    del.innerHTML = svgIcon("x");
    del.title = "Xóa đoạn chat";
    del.onclick = function (e) {
      e.stopPropagation();
      fetch(GATEWAY + "/chat/sessions/" + encodeURIComponent(s.id), { method: "DELETE", credentials: "include" })
        .then(function () { loadHistory(); if (s.id === CONVERSATION_ID) newChat(); });
    };
    item.appendChild(t);
    item.appendChild(del);
    return item;
  }
  function renderHistory(sessions) {
    histBox.innerHTML = "";
    var search = document.createElement("div");
    search.className = "acp-hist-search";
    search.innerHTML = "⌕ <input placeholder='Search chats...' aria-label='Tìm chat'>";
    var box = document.createElement("div");
    box.style.display = "flex"; box.style.flexDirection = "column"; box.style.gap = "1px";
    histBox.appendChild(search); histBox.appendChild(box);
    var query = "";
    var inputEl = search.querySelector("input");
    inputEl.oninput = function () { query = inputEl.value.toLowerCase(); paint(); };
    inputEl.onclick = function (e) { e.stopPropagation(); };
    function paint() {
      box.innerHTML = "";
      var list = sessions.filter(function (s) {
        return !query || (s.title || "").toLowerCase().indexOf(query) >= 0;
      });
      if (!list.length) {
        var empty = document.createElement("div");
        empty.className = "acp-hist-empty";
        empty.textContent = query ? "Không tìm thấy." : "Chưa có đoạn chat nào.";
        box.appendChild(empty);
        return;
      }
      var lastGroup = "";
      list.forEach(function (s) {
        var g = histGroup(s.updated || s.created || Date.now() / 1000);
        if (g !== lastGroup) {
          var h = document.createElement("div");
          h.className = "acp-hist-group"; h.textContent = g;
          box.appendChild(h); lastGroup = g;
        }
        box.appendChild(histItem(s));
      });
    }
    paint();
    setTimeout(function () { try { inputEl.focus(); } catch (e) {} }, 30);
  }
  function loadHistory() {
    fetch(GATEWAY + "/chat/sessions?limit=30", { credentials: "include" })
      .then(function (r) { return r.ok ? r.json() : { sessions: [] }; })
      .then(function (d) { renderHistory(d.sessions || []); })
      .catch(function () { /* offline thì giữ dropdown cũ */ });
  }
  function openSession(id) {
    fetch(GATEWAY + "/chat/sessions/" + encodeURIComponent(id), { credentials: "include" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d || !d.session) return;
        setConversation(d.session.id);
        if (titleEl) titleEl.textContent = d.session.title || "New AI chat";
        msgs.innerHTML = "";
        (d.messages || []).forEach(function (m) {
          if (m.role === "user") addUser(m.content);
          else addAssistant(m.content, [], []);
        });
        if (!d.messages || !d.messages.length) addWelcome();
        input.focus();
      })
      .catch(function (err) {
        console.error("Failed to open session:", err);
      });
  }
  function newChat() {
    setConversation("");
    msgs.innerHTML = "";
    input.value = "";
    input.style.height = "auto";
    titleEl.textContent = "New AI chat";
    addWelcome();
    syncSend();
    input.focus();
  }
  function shortCtx() {
    var p = location.pathname + location.search;
    var m = p.match(/courses\/([^\/?]+)/);
    if (m) {
      try { return decodeURIComponent(m[1]).replace(/-/g, " ").slice(0, 28); } catch (e) { return m[1].slice(0, 28); }
    }
    var seg = (p.split("/").filter(Boolean).pop() || "LMS").replace(/-/g, " ");
    return decodeURIComponent(seg).slice(0, 28);
  }
  function syncUiChrome() {
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
    var re = /(LMS Course:\s*[^,\n]+|Course Lesson:\s*[^,\n]+|LMS Quiz:\s*[^,\n]+)/g, m;
    while ((m = re.exec(t || "")) && out.length < 6) {
      var label = m[1].replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
      var value = label.replace(/^[^:]+:\s*/, "").trim();
      if (/^(toàn bộ dữ liệu|không áp dụng|n\/a|not applicable)$/i.test(value)) continue;
      if (out.indexOf(label) < 0) out.push(label);
    }
    if (!out.length) return "";
    return '<div class="acp-src">' + out.map(function (s) { return "<span>" + esc(s) + "</span>"; }).join("") + "</div>";
  }
  function assistantActions(box) {
    var bar = document.createElement("div");
    bar.className = "acp-actions";
    var copyBtn = document.createElement("button");
    copyBtn.type = "button"; copyBtn.title = "Sao chép"; copyBtn.innerHTML = svgIcon("copy");
    copyBtn.onclick = function () {
      var text = box.querySelector(".body") ? box.querySelector(".body").innerText : "";
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text);
        else { var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
        copyBtn.innerHTML = svgIcon("check"); setTimeout(function () { copyBtn.innerHTML = svgIcon("copy"); }, 1200);
      } catch (e) { /* clipboard có thể bị chặn */ }
    };
    bar.appendChild(copyBtn); bar.appendChild(addBtn2("plus"));
    box.appendChild(bar);
    function addBtn2(iconName) { var b = document.createElement("button"); b.type = "button"; b.innerHTML = svgIcon(iconName); b.title = "Chèn"; b.onclick = function () { input.value += box.querySelector(".body").innerText.slice(0, 400); input.focus(); syncSend(); }; return b; }
  }
  function addWelcome() {
    var d = document.createElement("div");
    d.className = "acp-welcome";
    d.innerHTML =
      '<div class="acp-welcome-icon">' + svgIcon("sparkles") + '</div>' +
      '<h2>How can I help you today?</h2>' +
      '<div class="acp-suggestions">' +
      '<button class="acp-suggestion" data-q="Tóm tắt bài học này cho tôi"><span class="acp-suggestion-mark">' + svgIcon("sparkles") + '</span><span>Personalize your LMS AI</span></button>' +
      '<button class="acp-suggestion" data-q="Tạo 3 câu quiz nháp cho bài này"><span class="acp-suggestion-mark">' + svgIcon("bookOpen") + '</span><span>Create quiz <span class="tag">New</span></span></button>' +
      '<button class="acp-suggestion" data-q="Giải thích nội dung bài học này"><span class="acp-suggestion-mark">' + svgIcon("circleHelp") + '</span><span>Explain this lesson</span></button>' +
      '<button class="acp-suggestion" data-q="Tiến độ học của tôi thế nào?"><span class="acp-suggestion-mark">' + svgIcon("search") + '</span><span>Analyze my progress</span></button>' +
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
  function addAssistant(answer, calls, approvals, timings, actions, directives) {
    var d = document.createElement("div");
    d.className = "acp-a";
    var html = '<div class="body">' + md(answer || "(trống)") + "</div>" + srcChips(answer);
    d.innerHTML = html;
    (approvals || []).forEach(function (a) {
      if ((actions || []).some(function (card) { return card.approval_id === a.approval_id; })) return;
      var box = document.createElement("div");
      box.className = "acp-ap";
      box.innerHTML = "Cần phê duyệt: <b>" + esc(a.tool) + "</b><br><span style='font-size:11px'>id=" + esc(a.approval_id) + "</span> ";
      var b = document.createElement("button");
      b.textContent = "Duyệt & chạy";
      b.onclick = function () { approve(a.approval_id, b); };
      box.appendChild(b);
      d.appendChild(box);
    });
    (actions || []).forEach(function (card) { appendActionCard(d, card); });
    (directives || []).forEach(handleDirective);
    assistantActions(d);
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
  }
  function actionChangeText(change) {
    return [change.op || "change", change.doctype || "", change.name || ""].join(" · ");
  }
  function appendActionCard(target, card) {
    if (!card || !card.action_id) return;
    var existing = target.querySelector('[data-action-id="' + String(card.action_id).replace(/"/g, "") + '"]');
    if (existing) return;
    var box = document.createElement("div");
    box.className = "acp-action-card";
    box.dataset.actionId = String(card.action_id);
    var h = document.createElement("h4"); h.textContent = card.title || card.action || "Đã thực hiện";
    var p = document.createElement("p"); p.textContent = card.summary || "";
    box.appendChild(h); box.appendChild(p);
    var undo = card.undo || {};
    if ((card.changes || []).length) {
      var ul = document.createElement("ul"); ul.className = "acp-action-changes";
      card.changes.forEach(function (change) { var li = document.createElement("li"); li.textContent = actionChangeText(change); ul.appendChild(li); });
      box.appendChild(ul);
    }
    if (card.status === "pending_approval" && card.approval_id) {
      var approveBtn = document.createElement("button"); approveBtn.textContent = "Duyệt & chạy";
      approveBtn.onclick = function () { approve(card.approval_id, approveBtn); };
      box.appendChild(approveBtn);
    }
    var expiresAt = Number(undo.expires_at || 0);
    if (undo.available && card.status === "done" && (!expiresAt || expiresAt > Date.now() / 1000)) {
      var undoBtn = document.createElement("button"); undoBtn.className = "secondary"; undoBtn.textContent = "Hoàn tác";
      undoBtn.onclick = function () {
        undoBtn.disabled = true; undoBtn.textContent = "Đang hoàn tác…";
        fetch(GATEWAY + "/actions/" + encodeURIComponent(card.action_id) + "/undo", { method: "POST", credentials: "same-origin" })
          .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || "undo failed"); return j; }); })
          .then(function () { undoBtn.textContent = "Đã hoàn tác"; })
          .catch(function (e) { undoBtn.disabled = false; undoBtn.textContent = e.message || "Hoàn tác lỗi"; });
      };
      box.appendChild(undoBtn);
      if (expiresAt) setTimeout(function () { if (undoBtn.parentNode) undoBtn.remove(); }, Math.max(0, (expiresAt - Date.now() / 1000) * 1000));
    }
    if (card.view) {
      var openBtn = document.createElement("button"); openBtn.className = "secondary"; openBtn.textContent = "Mở";
      openBtn.onclick = function () { renderView(card.view); };
      box.appendChild(openBtn);
    }
    target.appendChild(box);
  }
  function showCanvas(title) {
    canvasTitle.textContent = title || "Agentic view";
    canvas.classList.add("show");
    canvasBody.scrollTop = 0;
  }
  function renderView(spec) {
    if (!spec || !spec.type) return;
    canvasBody.replaceChildren();
    var title = { review_set: "Bộ ôn tập", session: "Phiên học", knowledge_map: "Bản đồ kiến thức", mastery_detail: "Chi tiết mastery", plan: "Kế hoạch học", diff: "Phân tích thay đổi", course: "Khóa học", lesson: "Bài học" }[spec.type] || "Agentic view";
    showCanvas(title);
    if (spec.type === "review_set") {
      var loading = document.createElement("p"); loading.textContent = "Đang tải bộ ôn…"; canvasBody.appendChild(loading);
      fetch(GATEWAY + "/review-sets/" + encodeURIComponent(spec.set_id), { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("Không tải được bộ ôn"); return r.json(); })
        .then(function (review) {
          canvasBody.replaceChildren();
          (review.items || []).forEach(function (item) {
            var section = document.createElement("div"); section.className = "acp-review-item";
            var prompt = document.createElement("p"); prompt.textContent = item.prompt || "Câu hỏi ôn tập"; section.appendChild(prompt);
            var area = document.createElement("textarea"); area.placeholder = "Câu trả lời của bạn…"; section.appendChild(area);
            var button = document.createElement("button"); button.textContent = "Kiểm tra";
            var result = document.createElement("p");
            button.onclick = function () {
              button.disabled = true;
              fetch(GATEWAY + "/review-sets/" + encodeURIComponent(spec.set_id) + "/answer", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ item_id: item.id, answer: area.value }) })
                .then(function (r) { return r.json().then(function (data) { if (!r.ok) throw new Error(data.detail || "Không chấm được"); return data; }); })
                .then(function (data) { result.textContent = data.correct ? "Đúng · mastery: " + (data.mastery_after == null ? "–" : Math.round(data.mastery_after * 100) + "%") : data.explanation; })
                .catch(function (e) { result.textContent = e.message; })
                .finally(function () { button.disabled = false; });
            };
            section.appendChild(button); section.appendChild(result); canvasBody.appendChild(section);
          });
        })
        .catch(function (e) { canvasBody.replaceChildren(); var error = document.createElement("p"); error.textContent = e.message; canvasBody.appendChild(error); });
      return;
    }
    if (spec.type === "knowledge_map" || spec.type === "mastery_detail") {
      var masteryLoading = document.createElement("p"); masteryLoading.textContent = "Đang tải learner state…"; canvasBody.appendChild(masteryLoading);
      fetch(GATEWAY + "/me/mastery?course=" + encodeURIComponent(spec.course || ""), { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("Không tải được mastery"); return r.json(); })
        .then(function (data) {
          canvasBody.replaceChildren();
          var rows = data.concepts || [];
          if (spec.concept_id) rows = rows.filter(function (row) { return row.concept_id === spec.concept_id || row.id === spec.concept_id; });
          var card = document.createElement("div"); card.className = "acp-canvas-card";
          if (!rows.length) { var empty = document.createElement("p"); empty.textContent = "Chưa có dữ liệu mastery."; card.appendChild(empty); }
          rows.forEach(function (row) {
            var item = document.createElement("p"); item.textContent = (row.label || row.concept_id || "Concept") + " · " + Math.round(100 * (row.p_display || row.p || 0)) + "%"; card.appendChild(item);
          });
          canvasBody.appendChild(card);
        })
        .catch(function (e) { canvasBody.replaceChildren(); var error = document.createElement("p"); error.textContent = e.message; canvasBody.appendChild(error); });
      return;
    }
    var card = document.createElement("div"); card.className = "acp-canvas-card";
    var heading = document.createElement("h3"); heading.textContent = title; card.appendChild(heading);
    if (spec.type === "diff") {
      var before = document.createElement("pre"); before.textContent = String(spec.before || ""); var after = document.createElement("pre"); after.textContent = String(spec.after || "");
      card.appendChild(before); card.appendChild(after);
    } else {
      var text = document.createElement("p");
      text.textContent = spec.type === "plan" ? "Kế hoạch ngày " + String(spec.date || "") : spec.concept_id ? "Concept: " + spec.concept_id : spec.course ? "Course: " + spec.course : "Sẵn sàng.";
      card.appendChild(text);
      if (spec.type === "session") {
        var start = document.createElement("button"); start.textContent = "Bắt đầu phiên";
        start.onclick = function () { canvas.classList.remove("show"); CURRENT_MODE = spec.mode === "viva" ? "check" : spec.mode; send("Bắt đầu phiên học cho concept " + spec.concept_id); };
        card.appendChild(start);
      }
      if (spec.type === "course" || spec.type === "lesson") {
        var open = document.createElement("button"); open.textContent = "Mở trong LMS";
        open.onclick = function () {
          var route = spec.type === "course" ? "/lms/courses/" + encodeURIComponent(spec.course) : "/lms/courses/" + encodeURIComponent(spec.course) + "/learn/" + spec.chapter + "-" + spec.lesson_number;
          handleDirective({ op: "navigate", route: route });
        };
        card.appendChild(open);
      }
    }
    canvasBody.appendChild(card);
  }
  function handleDirective(event) {
    var directive = event && event.directive ? event.directive : event;
    if (!directive) return;
    if (directive.op === "navigate") {
      try { localStorage.setItem("acp-reopen", JSON.stringify({ open: true, session_id: CONVERSATION_ID })); } catch (e) { /* storage unavailable */ }
      location.assign(directive.route);
    } else if (directive.op === "render_view") {
      renderView(directive.spec);
    }
  }
  function approve(id, btn) {
    btn.disabled = true; btn.textContent = "…";
    fetch(GATEWAY + "/approve/" + encodeURIComponent(id), { method: "POST", credentials: "same-origin" })
      .then(function (r) {
        return r.text().then(function (text) {
          var payload = {};
          try { payload = text ? JSON.parse(text) : {}; } catch (e) {
            payload = { detail: text || ("HTTP " + r.status) };
          }
          if (!r.ok) {
            var httpError = new Error(payload.detail || ("HTTP " + r.status));
            httpError.retryable = r.status >= 500;
            throw httpError;
          }
          return payload;
        });
      })
      .then(function (j) {
        if (!j.ok) {
          var actionError = new Error((j.result && j.result.error) || "Thao tác không thành công");
          actionError.retryable = false;
          throw actionError;
        }
        btn.textContent = "Đã duyệt";
        if (j.result && j.result.kind === "action") addAssistant("", [], [], null, [j.result], j.result.directives || []);
        else addAssistant("Đã thực hiện sau phê duyệt: " + JSON.stringify(j.result).slice(0, 800), [], []);
      })
      .catch(function (err) {
        var message = String(err && err.message || "Không rõ lỗi").replace(/\s+/g, " ").slice(0, 180);
        var retryable = !err || err.retryable !== false;
        btn.textContent = (retryable ? "Lỗi: " : "Thất bại: ") + message;
        btn.disabled = !retryable;
      });
  }
  function send(text) {
    text = (text || input.value || "").trim();
    if (!text) return;
    input.value = "";
    input.style.height = "auto";
    syncSend();
    if (msgs.querySelector(".acp-welcome")) titleEl.textContent = text.length > 28 ? text.slice(0, 28) + "…" : text;
    addUser(text);
    var t0 = Date.now();
    fetch(GATEWAY + "/chat/stream", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: CONVERSATION_ID, page: location.pathname + location.search, mode: CURRENT_MODE, effort: CURRENT_EFFORT, approval_mode: CURRENT_APPROVAL_MODE })
    }).then(function (r) {
      if (!r.ok) {
        return r.text().then(function (text) {
          var j = {};
          try { j = text ? JSON.parse(text) : {}; } catch (e) { /* keep raw response */ }
          throw new Error(j.answer || j.detail || text || ("HTTP " + r.status));
        });
      }
      if (!r.body || !r.body.getReader) {
        return r.json().then(function (j) { addAssistant(j.answer, j.tool_calls, j.approvals, j.timings, j.actions, j.directives); });
      }
      return streamSSE(r.body.getReader(), t0);
    }).catch(function (e) {
      addAssistant("Gateway không xử lý được yêu cầu: " + e.message, [], []);
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
      if (ev.t === "session" && ev.session_id) { setConversation(ev.session_id); loadHistory(); return; }
      if (ev.t === "token") { full += ev.text || ""; paint(); }
      else if (ev.t === "thought") { pushThought(ev.text || ""); }
      else if (ev.t === "tool") { pushTool(ev); }
      else if (ev.t === "action") { appendActionCard(box, ev.card); }
      else if (ev.t === "client") { box.dataset.directiveSeen = "1"; handleDirective(ev); }
      else if (ev.t === "round" && ev.n > 1 && full) { full += "\n\n— vòng " + ev.n + " —\n"; paint(); }
      else if (ev.t === "done") {
        if (ev.session_id) setConversation(ev.session_id);
        finishBox(ev); loadHistory();
      }
    }
    function thinkBox() {
      var el = box.querySelector(".acp-think");
      if (el) return el;
      el = document.createElement("details");
      el.className = "acp-think";
      el.open = true;
      el.innerHTML = '<summary><span class="th-spin"></span><span class="th-label">Thinking…</span></summary><div class="th-body"></div>';
      box.insertBefore(el, bodyEl);
      return el;
    }
    var TOOL_VI = {
      search_courses: "Tìm khóa học",
      get_course_outline: "Lấy đề cương khóa học",
      get_lesson_context: "Đọc nội dung bài học",
      get_my_progress: "Xem tiến độ học",
      get_batch_progress: "Xem tiến độ lớp học",
      find_at_risk_students: "Tìm học viên cần hỗ trợ",
      list_at_risk_students: "Liệt kê học viên nguy cơ",
      get_student_mastery: "Xem điểm yếu học viên",
      get_quiz_submissions: "Xem bài nộp quiz",
      get_assignment_submissions: "Xem bài nộp tự luận",
      draft_quiz: "Soạn quiz nháp",
      draft_assignment_feedback: "Soạn nhận xét nháp",
      update_course_content_after_approval: "Cập nhật bài học",
      remember_user_fact: "Ghi nhớ thông tin",
      recall_user_facts: "Nhớ lại thông tin",
      forget_user_fact: "Quên thông tin",
      get_my_mastery: "Xem điểm yếu",
      record_feedback_correction: "Ghi nhận góp ý",
      enroll_course: "Ghi danh khóa học",
      mark_lesson_complete: "Đánh dấu bài học hoàn tất",
      save_note: "Lưu ghi chú",
      create_review_set: "Tạo bộ ôn tập",
      schedule_review: "Lên lịch ôn tập",
      set_goal: "Cập nhật mục tiêu",
      start_session: "Mở phiên học",
      navigate: "Mở trang LMS",
      render_view: "Mở khung Agentic",
      message_students: "Nhắn học viên",
      create_live_class: "Tạo lớp trực tiếp",
      publish_lesson_draft: "Xuất bản bài học",
      analyze_course_gaps: "Phân tích khoảng trống"
    };
    function toolVi(name) { return TOOL_VI[name] || name; }
    function pushThought(text) {
      if (!text) return;
      var el = thinkBox();
      var body = el.querySelector(".th-body");
      var buf = body.querySelector(".th-stream");
      if (!buf) { buf = document.createElement("div"); buf.className = "th-stream"; body.insertBefore(buf, body.firstChild); }
      buf.dataset.raw = (buf.dataset.raw || "") + (buf.dataset.raw ? "\n" : "") + text;
      buf.innerHTML = md(buf.dataset.raw);
      msgs.scrollTop = msgs.scrollHeight;
    }
    function pushTool(ev) {
      var el = thinkBox();
      el.open = true;
      var row = document.createElement("div");
      row.className = "acp-toolrow";
      var raw = ev.tool || "tool";
      var label = toolVi(raw);
      if (ev.ms != null) label += " · " + ev.ms + "ms";
      row.innerHTML = svgIcon("check", "tick") + '<span>Đã dùng</span> <span class="tname">' + esc(label) + "</span>";
      var body = el.querySelector(".th-body");
      if (ev.result) row.title = raw + ": " + String(ev.result).slice(0, 400);
      body.appendChild(row);
      msgs.scrollTop = msgs.scrollHeight;
    }
    function closeThought(answer) {
      var el = box.querySelector(".acp-think");
      if (!el) return;
      el.classList.add("done");
      el.open = false;
      var label = el.querySelector(".th-label");
      if (label) label.textContent = "Đã suy nghĩ xong";
      if (!el.querySelector(".th-body").children.length && !el.querySelector(".th-body").textContent.trim()) el.remove();
    }
    function finishBox(ev) {
      var cur = box.querySelector(".acp-cursor"); if (cur) cur.remove();
      closeThought(ev.answer || full);
      bodyEl.innerHTML = md(ev.answer || full || "(trống)");
      bodyEl.insertAdjacentHTML("afterend", srcChips(ev.answer || full));
      (ev.approvals || []).forEach(function (a) {
        if ((ev.actions || []).some(function (card) { return card.approval_id === a.approval_id; })) return;
        var ab = document.createElement("div"); ab.className = "acp-ap";
        ab.innerHTML = "Cần phê duyệt: <b>" + esc(a.tool) + "</b><br><span style='font-size:11px'>id=" + esc(a.approval_id) + "</span> ";
        var b = document.createElement("button"); b.textContent = "Duyệt & chạy";
        b.onclick = function () { approve(a.approval_id, b); };
        ab.appendChild(b); box.appendChild(ab);
      });
      (ev.actions || []).forEach(function (card) { appendActionCard(box, card); });
      if (!box.dataset.directiveSeen) (ev.directives || []).forEach(handleDirective);
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
  canvas.querySelector(".acp-canvas-close").onclick = function () { canvas.classList.remove("show"); };
  function toggleShell() {
    var on = side.classList.toggle("shell");
    side.style.removeProperty("--acp-w");
    side.querySelector("#acp-wide").innerHTML = svgIcon("panelRight");
    side.querySelector("#acp-wide").title = on ? "Thu nhỏ" : "Mở rộng";
    side.querySelector("#acp-wide").setAttribute("aria-label", on ? "Thu nhỏ" : "Mở rộng");
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
  headMain.onclick = toggleHist;
  side.querySelector("#acp-new").onclick = function () { closeHist(); closePop(); newChat(); };
  document.addEventListener("click", function () { closeHist(); closePop(); });
  function closePop() { pop.className = ""; pop.innerHTML = ""; pop.onclick = null; }
  function openPop(items) {
    pop.className = "";
    pop.innerHTML = "";
    items.forEach(function (it) {
      var b = document.createElement("button");
      b.type = "button";
      b.innerHTML = svgIcon(it[0], "acp-pop-icon") + "<span>" + it[1] + "</span>";
      b.onclick = function () { closePop(); it[2](); };
      pop.appendChild(b);
    });
    pop.classList.add("show");
  }
  var APPROVAL_MODES = [
    { value: "ask", icon: "shieldCheck", label: "Hỏi trước khi làm", short: "Hỏi trước", description: "Luôn yêu cầu bạn duyệt trước mọi thao tác thay đổi." },
    { value: "auto", icon: "circleDot", label: "Tự duyệt cho tôi", short: "Tự duyệt", description: "Tự chạy thay đổi thường; vẫn hỏi khi xóa, gửi thông báo, tạo lớp hoặc xuất bản khóa học." },
    { value: "full_access", icon: "triangleAlert", label: "Toàn quyền", short: "Toàn quyền", description: "Không hỏi lại. AI được dùng mọi tool trong quyền LMS của tài khoản.", danger: true }
  ];
  function approvalStorageKey() {
    return "acp-approval-mode:" + String(CURRENT_IDENTITY.user || "Guest");
  }
  function syncApprovalButton() {
    var selected = APPROVAL_MODES.find(function (item) { return item.value === CURRENT_APPROVAL_MODE; }) || APPROVAL_MODES[0];
    approvalBtn.querySelector(".mode").textContent = selected.short;
    approvalBtn.title = "Quyền thực thi: " + selected.label;
  }
  function setApprovalMode(value) {
    if (!APPROVAL_MODES.some(function (item) { return item.value === value; })) value = "ask";
    CURRENT_APPROVAL_MODE = value;
    try { localStorage.setItem(approvalStorageKey(), value); } catch (e) { /* storage unavailable */ }
    syncApprovalButton();
  }
  function loadApprovalMode() {
    var value = "ask";
    try { value = localStorage.getItem(approvalStorageKey()) || "ask"; } catch (e) { /* storage unavailable */ }
    setApprovalMode(value);
  }
  function openApprovalPop() {
    closePop();
    pop.className = "acp-approval-pop show";
    pop.onclick = function (e) { e.stopPropagation(); };
    var title = document.createElement("div");
    title.className = "acp-approval-title";
    title.textContent = "AI được phép thực hiện hành động thế nào?";
    pop.appendChild(title);
    APPROVAL_MODES.forEach(function (item) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "acp-approval-option" + (item.danger ? " danger" : "");
      button.innerHTML = svgIcon(item.icon, "icon") + '<span><span class="label">' + item.label + '</span><span class="desc">' + item.description + '</span></span><span class="check">' + (item.value === CURRENT_APPROVAL_MODE ? svgIcon("check") : "") + '</span>';
      button.onclick = function () { setApprovalMode(item.value); closePop(); };
      pop.appendChild(button);
    });
  }
  approvalBtn.onclick = function (e) {
    e.stopPropagation();
    if (pop.classList.contains("acp-approval-pop")) { closePop(); return; }
    openApprovalPop();
  };
  chip.onclick = function (e) { e.stopPropagation(); chip.classList.toggle("off"); };
  addBtn.onclick = function (e) {
    e.stopPropagation();
    if (pop.classList.contains("show")) { closePop(); return; }
    openPop([
      ["circleDot", "Dùng ngữ cảnh LMS hiện tại", function () { chip.classList.remove("off"); syncUiChrome(); }],
      ["sparkles", "Tóm tắt bài học này", function () { send("Tóm tắt bài học này cho tôi"); }],
      ["search", "Kiểm tra tiến độ học tập", function () { send("Tiến độ học của tôi thế nào?"); }]
    ]);
  };
  var EFFORT_LEVELS = [
    { value: "minimal", label: "Minimal" },
    { value: "low", label: "Low" },
    { value: "medium", label: "Medium" },
    { value: "high", label: "High" },
    { value: "xhigh", label: "Extra High" }
  ];
  function effortIndex() {
    var index = EFFORT_LEVELS.findIndex(function (item) { return item.value === CURRENT_EFFORT; });
    return index < 0 ? 2 : index;
  }
  function renderEffortCard() {
    var current = pop.querySelector("#acp-effort-current");
    var range = pop.querySelector("#acp-effort-range");
    if (!current || !range) return;
    current.textContent = EFFORT_LEVELS[effortIndex()].label;
    var arrow = document.createElement("span"); arrow.innerHTML = svgIcon("chevronRight"); current.appendChild(arrow);
    var index = effortIndex();
    range.value = String(index);
    range.style.setProperty("--effort-fill", (index / (EFFORT_LEVELS.length - 1) * 100) + "%");
    range.setAttribute("aria-valuetext", EFFORT_LEVELS[index].label);
  }
  function syncEffortButton(label) {
    effortBtn.innerHTML = label + ' <span class="chev">' + svgIcon("chevronDown") + '</span>';
    effortBtn.title = "Độ suy luận: " + label;
  }
  function setEffort(value, label) {
    CURRENT_EFFORT = value;
    syncEffortButton(label);
    renderEffortCard();
  }
  modeBtn.onclick = function (e) {
    e.stopPropagation();
    if (pop.classList.contains("show")) { closePop(); return; }
    openPop([
      ["sliders", "Auto: trả lời toàn diện", function () { CURRENT_MODE = "chat"; }],
      ["bookOpen", "Feynman: tôi giảng lại", function () { CURRENT_MODE = "feynman"; send("Hãy bắt đầu phiên Feynman cho bài này."); }],
      ["circleHelp", "Kiểm tra hội thoại", function () { CURRENT_MODE = "check"; send("Hãy bắt đầu kiểm tra hội thoại cho bài này."); }],
      ["messageCircle", "Viva: hỏi đáp trực tiếp", function () { CURRENT_MODE = "viva"; send("Hãy bắt đầu phiên viva cho bài này."); }],
      ["check", "Chấm phiên học hiện tại", function () {
        if (!CURRENT_CONCEPT || CURRENT_MODE === "chat") { addAssistant("Hãy mở bài có concept đã duyệt và chọn Feynman, Viva hoặc Kiểm tra trước.", [], []); return; }
        fetch(GATEWAY + "/learning/session", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: CURRENT_MODE, concept_id: CURRENT_CONCEPT, transcript: msgs.innerText.slice(0, 12000) }) })
          .then(function (r) { return r.json(); }).then(function (d) { addAssistant("Điểm phiên: " + Math.round(100 * d.score) + "%\n" + ((d.rubric || {}).feedback || "Đã lưu evidence ngoài LMS."), [], []); });
      }],
      ["sparkles", "Tóm tắt ngắn gọn", function () { CURRENT_MODE = "chat"; send("Tóm tắt ngắn gọn bài học này"); }],
      ["search", "Giải thích chi tiết", function () { CURRENT_MODE = "chat"; send("Giải thích chi tiết nội dung bài học này"); }]
    ]);
  };
  function openEffortPop() {
    closePop();
    pop.className = "acp-effort-pop show";
    pop.innerHTML =
      '<div class="acp-effort-card">' +
      '<div class="acp-effort-head">' +
      '<span class="acp-effort-bolt">' + svgIcon("sparkles") + '</span>' +
      '<div id="acp-effort-current" class="acp-effort-current">Medium <span>' + svgIcon("chevronRight") + '</span></div>' +
      '</div>' +
      '<div class="acp-effort-range"><input id="acp-effort-range" type="range" min="0" max="4" step="1" value="2" aria-label="Mức effort">' +
      '<div class="acp-effort-dots"><i></i><i></i><i></i><i></i><i></i></div></div>' +
      '<div class="acp-effort-labels"><span>Minimal</span><span>Low</span><span>Medium</span><span>High</span><span>Extra High</span></div>' +
      '</div>';
    pop.onclick = function (e) { e.stopPropagation(); };
    var range = pop.querySelector("#acp-effort-range");
    range.oninput = function () {
      var item = EFFORT_LEVELS[Number(range.value)];
      setEffort(item.value, item.label);
    };
    renderEffortCard();
  }
  effortBtn.onclick = function (e) {
    e.stopPropagation();
    if (pop.classList.contains("show")) { closePop(); return; }
    openEffortPop();
  };
  side.querySelector("#acp-more").onclick = function (e) {
    e.stopPropagation();
    closePop();
    if (histBox.classList.contains("show")) { closeHist(); return; }
    loadHistory();
    histBox.classList.add("show");
  };
  sendBtn.onclick = function () { send(); };
  input.addEventListener("input", function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 150) + "px";
    syncSend();
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.altKey && (e.key === "c" || e.key === "C")) { e.preventDefault(); fab.onclick(); }
    if (e.altKey && (e.key === "m" || e.key === "M")) { e.preventDefault(); if (!side.classList.contains("open")) setOpen(true); toggleShell(); }
  });
  fetch(GATEWAY + "/identity", { credentials: "same-origin" })
    .then(function (response) {
      if (!response.ok) throw new Error("unauthorized");
      return response.json();
    })
    .then(function (identity) { CURRENT_IDENTITY = identity; loadApprovalMode(); syncUiChrome(); refreshInsight(); loadHistory(); })
    .catch(function () { CURRENT_IDENTITY = { user: "Guest", role: "student", roles: [] }; syncUiChrome(); });
  syncUiChrome();
  syncApprovalButton();
  syncEffortButton(EFFORT_LEVELS[effortIndex()].label);
  syncSend();
  if (!msgs.children.length) addWelcome();
  try {
    var reopen = JSON.parse(localStorage.getItem("acp-reopen") || "null");
    if (reopen && reopen.open) {
      if (reopen.session_id) setConversation(reopen.session_id);
      localStorage.removeItem("acp-reopen");
      setOpen(true);
    }
  } catch (e) { /* storage unavailable */ }
  var lastRoute = location.pathname + location.search;
  setInterval(function () {
    var nextRoute = location.pathname + location.search;
    if (nextRoute !== lastRoute) { lastRoute = nextRoute; syncUiChrome(); refreshInsight(); }
  }, 1000);
})();
