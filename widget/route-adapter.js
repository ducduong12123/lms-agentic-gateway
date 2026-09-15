(function (global) {
  "use strict";
  function parse(value) {
    var path;
    try { path = new URL(value || location.href, location.href).pathname.replace(/\/$/, "") || "/"; }
    catch (e) { path = String(value || "").split("?")[0].replace(/\/$/, "") || "/"; }
    var lesson = path.match(/^\/lms\/courses\/([^/]+)\/learn\/(\d+)-(\d+)$/);
    if (lesson) return { kind: "lesson", course: decodeURIComponent(lesson[1]), chapter: Number(lesson[2]), lesson_number: Number(lesson[3]), path: path };
    var course = path.match(/^\/lms\/courses\/([^/]+)\/?(?:learn)?$/);
    if (course) return { kind: "course", course: decodeURIComponent(course[1]), path: path };
    return { kind: "other", path: path };
  }
  global.LMSAgentRoute = { parse: parse };
})(window);
