/*
 * A badge showing what the document watcher is doing.
 *
 * Chainlit has no toast API, and the watcher runs as a background task with no user
 * session, so it cannot push anything to a browser. This polls /ingest-status
 * instead and draws the badge itself.
 *
 * Placement and behaviour come from two rounds of feedback. It was first a loud
 * top-right toast that covered the header logo and the welcome text; then a small
 * faint dot in the bottom right, which turned out to be so unobtrusive it could not
 * be found at all, and too small to hover reliably. So:
 *
 *   - top right, pushed BELOW the header so it clears the logo and the menu
 *   - always present, never disappearing, so there is always something to look at
 *   - coloured per state and semi-transparent, but large enough to read at a glance
 *   - hovering opens a panel listing the documents involved
 *
 * Its own file rather than an addition to custom.js: only one custom_js can be
 * configured, and custom.js holds older code that has never been loaded. Enabling
 * that as a side effect of adding a badge would be a surprise.
 */
(function () {
  "use strict";

  var ENDPOINT = "/ingest-status";
  var POLL_MS = 2500;
  var ID = "rag-ingest-status";
  /* Clears Chainlit's header. Raise if a future version makes the header taller. */
  var TOP_OFFSET_PX = 62;

  /* How long a finished run keeps its colour before the badge goes calm again. It
   * stays on screen either way, so it can still be found and hovered; only the
   * green stops shouting. */
  var CALM_AFTER_MS = 25000;

  var lastRevision = -1;
  var lastPayload = null;
  var finishedAt = 0;

  function styles() {
    if (document.getElementById(ID + "-styles")) return;
    var css = document.createElement("style");
    css.id = ID + "-styles";
    css.textContent = [
      "#" + ID + "{position:fixed;top:" + TOP_OFFSET_PX + "px;right:14px;",
      "z-index:2147483000;box-sizing:border-box;pointer-events:auto;",
      "display:flex;align-items:center;gap:9px;",
      "min-height:34px;padding:7px 14px;border-radius:17px;",
      "font-size:13px;font-weight:500;line-height:1.2;font-family:inherit;",
      "max-width:min(340px,calc(100vw - 28px));",
      "border:1px solid transparent;backdrop-filter:blur(6px);",
      "-webkit-backdrop-filter:blur(6px);",
      "box-shadow:0 2px 10px rgba(15,23,42,.10);",
      "transition:background .2s ease,color .2s ease,border-color .2s ease;",
      "cursor:default;user-select:none}",

      /* Idle: calm and quiet, but still legible and easy to hit. */
      "#" + ID + "[data-state='idle']{background:rgba(148,163,184,.16);",
      "color:#475569;border-color:rgba(148,163,184,.30)}",
      "html.dark #" + ID + "[data-state='idle'],.dark #" + ID + "[data-state='idle']{",
      "background:rgba(148,163,184,.16);color:#cbd5e1;",
      "border-color:rgba(148,163,184,.26)}",

      /* Working: blue, clearly active. */
      "#" + ID + "[data-state='working']{background:rgba(47,109,246,.16);",
      "color:#1d4ed8;border-color:rgba(47,109,246,.38)}",
      "html.dark #" + ID + "[data-state='working'],.dark #" + ID + "[data-state='working']{",
      "background:rgba(96,165,250,.18);color:#93c5fd;border-color:rgba(96,165,250,.40)}",

      /* Done: green. */
      "#" + ID + "[data-state='done']{background:rgba(22,163,74,.16);",
      "color:#15803d;border-color:rgba(22,163,74,.38)}",
      "html.dark #" + ID + "[data-state='done'],.dark #" + ID + "[data-state='done']{",
      "background:rgba(74,222,128,.16);color:#86efac;border-color:rgba(74,222,128,.36)}",

      /* Error: red. */
      "#" + ID + "[data-state='error']{background:rgba(220,38,38,.16);",
      "color:#b91c1c;border-color:rgba(220,38,38,.40)}",
      "html.dark #" + ID + "[data-state='error'],.dark #" + ID + "[data-state='error']{",
      "background:rgba(248,113,113,.18);color:#fca5a5;border-color:rgba(248,113,113,.40)}",

      "#" + ID + "[data-hidden='1']{display:none}",

      "#" + ID + " .ris-icon{flex:0 0 auto;width:13px;height:13px;display:flex;",
      "align-items:center;justify-content:center}",
      "#" + ID + " .ris-label{white-space:nowrap;overflow:hidden;",
      "text-overflow:ellipsis}",
      "#" + ID + " .ris-dot{width:8px;height:8px;border-radius:50%;",
      "background:currentColor;opacity:.85}",
      "#" + ID + " .ris-spinner{width:12px;height:12px;border-radius:50%;",
      "border:2px solid currentColor;border-top-color:transparent;opacity:.9;",
      "animation:ris-spin .8s linear infinite}",
      "@keyframes ris-spin{to{transform:rotate(360deg)}}",
      "#" + ID + " .ris-check{font-size:12px;font-weight:700;line-height:1}",

      /* Hover panel: the detail, anchored under the badge. */
      "#" + ID + " .ris-panel{position:absolute;top:calc(100% + 8px);right:0;",
      "min-width:230px;max-width:min(340px,calc(100vw - 28px));",
      "padding:10px 12px;border-radius:12px;font-size:12px;font-weight:400;",
      "line-height:1.45;text-align:left;color:#0f172a;background:#fff;",
      "border:1px solid #e2e8f0;box-shadow:0 10px 28px rgba(15,23,42,.16);",
      "opacity:0;visibility:hidden;transform:translateY(-4px);",
      "transition:opacity .15s ease,transform .15s ease,visibility .15s;",
      "white-space:normal}",
      "html.dark #" + ID + " .ris-panel,.dark #" + ID + " .ris-panel{color:#e8eef8;",
      "background:#111826;border-color:#2b3648;",
      "box-shadow:0 10px 28px rgba(0,0,0,.5)}",
      "#" + ID + ":hover .ris-panel{opacity:1;visibility:visible;",
      "transform:translateY(0)}",
      "#" + ID + " .ris-panel-title{font-weight:600;margin-bottom:6px}",
      "#" + ID + " .ris-file{display:flex;gap:6px;align-items:baseline;",
      "margin-top:3px;word-break:break-word}",
      "#" + ID + " .ris-file-action{flex:0 0 auto;font-size:10px;font-weight:700;",
      "text-transform:uppercase;letter-spacing:.03em;opacity:.75;min-width:52px}",
      "#" + ID + " .ris-hint{margin-top:8px;opacity:.7}",

      "@media (prefers-reduced-motion:reduce){#" + ID + ",#" + ID + " .ris-panel{",
      "transition:none}#" + ID + " .ris-spinner{animation-duration:2s}}",
      /* Narrow screens: keep the badge, drop it to icon width if space is tight. */
      "@media (max-width:560px){#" + ID + "{max-width:calc(100vw - 28px)}}",
    ].join("");
    document.head.appendChild(css);
  }

  function element() {
    var el = document.getElementById(ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = ID;
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML =
      '<span class="ris-icon"></span><span class="ris-label"></span>' +
      '<div class="ris-panel"></div>';
    document.body.appendChild(el);
    return el;
  }

  function escapeHtml(text) {
    var d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function panelHtml(status) {
    var title;
    if (status.state === "working") title = "Indexing right now";
    else if (status.state === "done") title = "Last change";
    else if (status.state === "error") title = "Something went wrong";
    else title = "Watching your documents";

    var html = '<div class="ris-panel-title">' + escapeHtml(title) + "</div>";
    html += "<div>" + escapeHtml(status.message || "No changes yet.") + "</div>";

    var files = status.files || (lastPayload && lastPayload.files) || [];
    if (files.length) {
      for (var i = 0; i < files.length; i++) {
        var f = files[i];
        html +=
          '<div class="ris-file"><span class="ris-file-action">' +
          escapeHtml(f.action === "more" ? "" : f.action) +
          '</span><span>' +
          escapeHtml(f.name) +
          "</span></div>";
      }
    }
    html +=
      '<div class="ris-hint">Documents are picked up from the folder automatically.' +
      "</div>";
    return html;
  }

  function render(status) {
    styles();
    var el = element();
    el.removeAttribute("data-hidden");
    el.setAttribute("data-state", status.state);

    var icon = el.querySelector(".ris-icon");
    if (status.state === "working") {
      icon.innerHTML = '<span class="ris-spinner"></span>';
    } else if (status.state === "done") {
      icon.innerHTML = '<span class="ris-check">✓</span>';
    } else {
      icon.innerHTML = '<span class="ris-dot"></span>';
    }

    // When a finished run has calmed down, the badge reads plainly again but the
    // panel still explains what happened.
    var label = status.message || "";
    if (status.state === "idle") {
      label = lastPayload ? "Documents up to date" : "Watching your documents";
    }
    el.querySelector(".ris-label").textContent = label;
    el.querySelector(".ris-panel").innerHTML = panelHtml(status);
  }

  function apply(status) {
    if (!status || typeof status.revision !== "number") return;

    if (status.state === "off") {
      var existing = document.getElementById(ID);
      if (existing) existing.setAttribute("data-hidden", "1");
      return;
    }

    // Idle before anything has happened: show the calm badge so it can be found.
    if (status.state === "idle") {
      render(status);
      return;
    }

    if (status.state === "working") {
      // Re-render every poll so the badge survives the chat UI re-rendering and
      // any progress text stays current.
      lastRevision = status.revision;
      lastPayload = status;
      finishedAt = 0;
      render(status);
      return;
    }

    // A finished or failed run. New ones get their colour; an old one calms down
    // but stays on screen, with the detail still available on hover.
    if (status.revision !== lastRevision) {
      lastRevision = status.revision;
      lastPayload = status;
      finishedAt = Date.now();
      render(status);
      return;
    }

    var stale = finishedAt && Date.now() - finishedAt > CALM_AFTER_MS;
    // Errors keep their colour: unlike a success, they still need attention.
    if (stale && status.state !== "error") {
      var calm = {};
      for (var k in status) calm[k] = status[k];
      calm.state = "idle";
      render(calm);
    } else {
      render(status);
    }
  }

  function poll() {
    fetch(ENDPOINT, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        // 401 before login is expected, and not something to shout about.
        return r.ok ? r.json() : null;
      })
      .then(apply)
      .catch(function () {
        /* offline or restarting: try again on the next tick */
      });
  }

  function start() {
    poll();
    setInterval(poll, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
