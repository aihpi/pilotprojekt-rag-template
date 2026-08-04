/*
 * A small, faint indicator showing what the document watcher is doing.
 *
 * Chainlit has no toast API, and the watcher runs as a background task with no user
 * session, so it cannot push anything to a browser. This polls /ingest-status
 * instead and draws the indicator itself.
 *
 * Kept deliberately quiet: this is background activity, not something the reader
 * needs to act on. It sits in the bottom right so it never covers the header, the
 * logo or the welcome text, stays collapsed to a small dot, and only shows its
 * details when hovered or while actually working.
 *
 * Its own file rather than an addition to custom.js: only one custom_js can be
 * configured, and custom.js holds older code that has never been loaded. Enabling
 * that as a side effect of adding an indicator would be a surprise.
 */
(function () {
  "use strict";

  var ENDPOINT = "/ingest-status";
  var POLL_MS = 3000;
  var SETTLE_AFTER_MS = 5000; // how long "done" stays expanded before collapsing
  var ID = "rag-ingest-status";

  var lastRevision = -1;
  var lastMessage = "";
  var collapseTimer = null;

  function styles() {
    if (document.getElementById(ID + "-styles")) return;
    var css = document.createElement("style");
    css.id = ID + "-styles";
    css.textContent = [
      /* Bottom right, small, translucent. Out of the way of the header and logo. */
      "#" + ID + "{position:fixed;bottom:12px;right:12px;z-index:2147483000;",
      "display:flex;align-items:center;gap:8px;box-sizing:border-box;",
      "height:26px;padding:0 9px;border-radius:13px;",
      "font-size:12px;line-height:1;font-family:inherit;",
      "color:rgba(100,116,139,.9);background:rgba(148,163,184,.10);",
      "border:1px solid rgba(148,163,184,.22);backdrop-filter:blur(4px);",
      "opacity:.45;transition:opacity .18s ease,background .18s ease;",
      "cursor:default;max-width:calc(100vw - 24px);overflow:hidden}",
      /* Hover, or working, reveals it properly. */
      "#" + ID + ":hover{opacity:1;background:rgba(148,163,184,.18)}",
      "#" + ID + "[data-state='working']{opacity:.85}",
      "#" + ID + "[data-hidden='1']{display:none}",

      "html.dark #" + ID + ",.dark #" + ID + "{color:rgba(203,213,225,.9);",
      "background:rgba(148,163,184,.12);border-color:rgba(148,163,184,.20)}",

      /* The label is collapsed by default and revealed on hover or while working. */
      "#" + ID + " .ris-label{max-width:0;opacity:0;white-space:nowrap;",
      "overflow:hidden;transition:max-width .2s ease,opacity .2s ease}",
      "#" + ID + ":hover .ris-label,#" + ID + "[data-expanded='1'] .ris-label{",
      "max-width:46vw;opacity:1}",

      "#" + ID + " .ris-icon{flex:0 0 auto;width:10px;height:10px;display:flex;",
      "align-items:center;justify-content:center}",
      "#" + ID + " .ris-dot{width:7px;height:7px;border-radius:50%;",
      "background:currentColor;opacity:.75}",
      "#" + ID + " .ris-spinner{width:10px;height:10px;border-radius:50%;",
      "border:1.5px solid rgba(120,140,170,.35);border-top-color:#2f6df6;",
      "animation:ris-spin .8s linear infinite}",
      "@keyframes ris-spin{to{transform:rotate(360deg)}}",

      "#" + ID + "[data-state='done'] .ris-check{color:#16a34a;font-size:11px;",
      "font-weight:700;line-height:1}",
      "#" + ID + "[data-state='done']{color:#16a34a;",
      "background:rgba(22,163,74,.10);border-color:rgba(22,163,74,.28)}",
      "html.dark #" + ID + "[data-state='done'],.dark #" + ID + "[data-state='done']{",
      "color:#4ade80;background:rgba(74,222,128,.10);border-color:rgba(74,222,128,.26)}",

      "#" + ID + "[data-state='error']{color:#dc2626;",
      "background:rgba(220,38,38,.10);border-color:rgba(220,38,38,.30);opacity:.9}",
      "html.dark #" + ID + "[data-state='error'],.dark #" + ID + "[data-state='error']{",
      "color:#f87171;background:rgba(248,113,113,.10)}",

      "@media (prefers-reduced-motion:reduce){#" + ID + ",#" + ID + " .ris-label{",
      "transition:none}#" + ID + " .ris-spinner{animation-duration:2s}}",
      /* On a narrow screen the collapsed dot is all that is worth showing. */
      "@media (max-width:520px){#" + ID + ":hover .ris-label{max-width:60vw}}",
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
    el.innerHTML = '<span class="ris-icon"></span><span class="ris-label"></span>';
    document.body.appendChild(el);
    return el;
  }

  function render(state, message, expanded) {
    styles();
    var el = element();
    var icon = el.querySelector(".ris-icon");
    var label = el.querySelector(".ris-label");

    el.removeAttribute("data-hidden");
    el.setAttribute("data-state", state);
    label.textContent = message;
    // The native tooltip is the fallback for touch devices, where there is no hover.
    el.setAttribute("title", message);

    if (state === "working") {
      icon.innerHTML = '<span class="ris-spinner"></span>';
    } else if (state === "done") {
      icon.innerHTML = '<span class="ris-check">✓</span>';
    } else if (state === "error") {
      icon.innerHTML = '<span class="ris-dot"></span>';
    } else {
      icon.innerHTML = '<span class="ris-dot"></span>';
    }

    if (expanded) {
      el.setAttribute("data-expanded", "1");
    } else {
      el.removeAttribute("data-expanded");
    }
  }

  function idleMessage() {
    return lastMessage
      ? "Watching your documents. Last change: " + lastMessage
      : "Watching your documents for changes";
  }

  function settleLater(state) {
    if (collapseTimer) clearTimeout(collapseTimer);
    collapseTimer = setTimeout(function () {
      // Collapse back to the quiet dot, but keep what happened available on hover.
      render(state === "error" ? "error" : "idle", idleMessage(), false);
    }, SETTLE_AFTER_MS);
  }

  function apply(status) {
    if (!status || typeof status.revision !== "number") return;

    if (status.state === "off") {
      var existing = document.getElementById(ID);
      if (existing) existing.setAttribute("data-hidden", "1");
      return;
    }

    if (status.state === "idle") {
      render("idle", idleMessage(), false);
      return;
    }

    if (status.state === "working") {
      // Re-render every poll so the indicator survives the chat UI re-rendering.
      lastRevision = status.revision;
      render("working", status.message || "Indexing...", true);
      if (collapseTimer) clearTimeout(collapseTimer);
      return;
    }

    // done / error: announce once, then settle down.
    if (status.revision === lastRevision) return;
    lastRevision = status.revision;
    lastMessage = status.message || "";
    render(status.state, lastMessage, true);
    settleLater(status.state);
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
