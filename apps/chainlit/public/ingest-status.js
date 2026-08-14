/*
 * A badge showing what the document watcher is doing, sitting next to the Readme
 * button in the header.
 *
 * Chainlit has no toast API, and the watcher runs as a background task with no user
 * session, so it cannot push anything to a browser. This polls /ingest-status
 * instead and renders the badge itself.
 *
 * Placement went through three rounds of feedback: a loud top-right toast covered
 * the header logo and welcome text; a small faint dot in the bottom right could not
 * be found at all; a fixed badge below the header was still in the way. It now lives
 * *in* the header, immediately after `#readme-button`, so it belongs to the
 * furniture instead of floating over the conversation.
 *
 * Chainlit renders that button as `id="readme-button"` and only when a readme
 * exists, so there is a fixed-position fallback if it never appears. The header is
 * React-rendered, so a MutationObserver puts the badge back whenever a re-render
 * drops it.
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
  var ANCHOR_ID = "readme-button";

  /* A finished run keeps its colour long enough to be read, then returns to the
   * neutral look. It never fades or leaves: dimming it made it invisible in dark
   * mode, so it simply stays there. */
  var HOLD_DONE_MS = 8000;
  /* If the header button never turns up, fall back to a fixed corner. */
  var FALLBACK_AFTER_MS = 8000;

  var lastRevision = -1;
  var lastPayload = null;
  var calmTimer = null;
  var startedAt = Date.now();

  /* German and English, chosen the way Chainlit chooses its own interface strings:
   * the server sends `[UI] language` when an instance forces one, otherwise the
   * browser decides. Anything not German falls back to English, Chainlit's default.
   *
   * The status sentence is composed here rather than server-side, from the counts
   * /ingest-status already carries. One watcher serves every open tab, so there is
   * no single language it could be written in back there. `status.message` remains
   * the English fallback if a payload arrives without counts. */
  var T = {
    de: {
      titleWorking: "Wird gerade indexiert",
      titleDone: "Letzte Änderung",
      titleError: "Etwas ist schiefgelaufen",
      titleIdle: "Ihre Dokumente werden beobachtet",
      nothingYet: "Noch keine Änderungen.",
      hint: "Dokumente werden automatisch aus dem Ordner übernommen.",
      upToDate: "Dokumente sind aktuell",
      watching: "Ihre Dokumente werden beobachtet",
      errorMsg: "Indexierung fehlgeschlagen. Details stehen im App-Log.",
      actions: { new: "neu", changed: "geändert", removed: "entfernt" },
      andMore: "und {n} weitere",
      working: "Änderung erkannt: {parts}…",
      workingPlain: "Änderung erkannt. Wird indexiert…",
      docNew: ["{n} neues Dokument", "{n} neue Dokumente"],
      docChanged: ["{n} geändertes Dokument", "{n} geänderte Dokumente"],
      docRemoving: ["{n} Dokument wird entfernt", "{n} Dokumente werden entfernt"],
      done: "Fertig: {parts}",
      donePlain: "Fertig. Nichts zu ändern.",
      passIndexed: ["{n} Textstelle indexiert", "{n} Textstellen indexiert"],
      passRemoved: ["{n} Textstelle entfernt", "{n} Textstellen entfernt"],
    },
    en: {
      titleWorking: "Indexing right now",
      titleDone: "Last change",
      titleError: "Something went wrong",
      titleIdle: "Watching your documents",
      nothingYet: "No changes yet.",
      hint: "Documents are picked up from the folder automatically.",
      upToDate: "Documents up to date",
      watching: "Watching your documents",
      errorMsg: "Indexing failed. See the app log for details.",
      actions: { new: "new", changed: "changed", removed: "removed" },
      andMore: "and {n} more",
      working: "Change detected: {parts}...",
      workingPlain: "Change detected. Indexing...",
      docNew: ["{n} new document", "{n} new documents"],
      docChanged: ["{n} changed document", "{n} changed documents"],
      docRemoving: ["removing {n} document", "removing {n} documents"],
      done: "Done: {parts}",
      donePlain: "Done. Nothing to change.",
      passIndexed: ["{n} passage indexed", "{n} passages indexed"],
      passRemoved: ["{n} passage removed", "{n} passages removed"],
    },
  };

  var strings = T.en;

  function setLang(forced) {
    var tag = String(forced || navigator.language || "en").toLowerCase();
    strings = tag.indexOf("de") === 0 ? T.de : T.en;
  }

  function fill(template, values) {
    return template.replace(/\{(\w+)\}/g, function (_, key) {
      return values[key];
    });
  }

  function count(pair, n) {
    return fill(pair[n === 1 ? 0 : 1], { n: n });
  }

  /* The sentence under the badge. Built from the counts when they are there, so it
   * follows the reader's language; falls back to whatever the server wrote.
   *
   * Keyed on which counts a payload carries, not on `state`: the two sets are
   * disjoint (a working status has added/edited/removed, a finished one has
   * indexed/removed_passages), and the hold timer re-renders a finished status with
   * `state` rewritten to "idle" while keeping its numbers. */
  function messageFor(status) {
    if (status.state === "error") return strings.errorMsg;
    var parts = [];
    if (typeof status.added === "number") {
      if (status.added) parts.push(count(strings.docNew, status.added));
      if (status.edited) parts.push(count(strings.docChanged, status.edited));
      if (status.removed) parts.push(count(strings.docRemoving, status.removed));
      return parts.length
        ? fill(strings.working, { parts: parts.join(", ") })
        : strings.workingPlain;
    }
    if (typeof status.indexed === "number") {
      if (status.indexed) parts.push(count(strings.passIndexed, status.indexed));
      if (status.removed_passages) {
        parts.push(count(strings.passRemoved, status.removed_passages));
      }
      return parts.length ? fill(strings.done, { parts: parts.join(", ") }) : strings.donePlain;
    }
    return status.message || "";
  }

  function styles() {
    if (document.getElementById(ID + "-styles")) return;
    var css = document.createElement("style");
    css.id = ID + "-styles";
    css.textContent = [
      /* Inline in the header, sized to sit beside the existing buttons. */
      "#" + ID + "{display:inline-flex;align-items:center;gap:7px;",
      "box-sizing:border-box;vertical-align:middle;margin-left:6px;",
      "height:30px;padding:0 11px;border-radius:15px;",
      "font-size:12.5px;font-weight:500;line-height:1;font-family:inherit;",
      "max-width:min(300px,38vw);",
      "border:1px solid transparent;opacity:1;pointer-events:auto;",
      "transition:background .2s ease,color .2s ease,border-color .2s ease,",
      "opacity .18s ease;cursor:default;user-select:none}",

      /* Used only when the header button cannot be found. */
      "#" + ID + "[data-floating='1']{position:fixed;top:62px;right:14px;",
      "z-index:2147483000;margin-left:0;backdrop-filter:blur(6px);",
      "-webkit-backdrop-filter:blur(6px)}",

      /* No fading. An earlier version dimmed the badge once a run had been read,
       * which made it invisible in dark mode. It now stays fully legible at all
       * times and only its colour changes with the state. */

      "#" + ID + "[data-state='idle']{background:rgba(148,163,184,.18);",
      "color:#475569;border-color:rgba(148,163,184,.34)}",
      /* Dark mode needs more contrast than a light-mode alpha gives: a grey tint on
       * a near-black header disappears. Brighter text, stronger fill and border. */
      "html.dark #" + ID + "[data-state='idle'],.dark #" + ID + "[data-state='idle']{",
      "background:rgba(148,163,184,.26);color:#e2e8f0;",
      "border-color:rgba(148,163,184,.42)}",

      "#" + ID + "[data-state='working']{background:rgba(47,109,246,.16);",
      "color:#1d4ed8;border-color:rgba(47,109,246,.38)}",
      "html.dark #" + ID + "[data-state='working'],.dark #" + ID + "[data-state='working']{",
      "background:rgba(96,165,250,.26);color:#bfdbfe;border-color:rgba(96,165,250,.50)}",

      "#" + ID + "[data-state='done']{background:rgba(22,163,74,.16);",
      "color:#15803d;border-color:rgba(22,163,74,.38)}",
      "html.dark #" + ID + "[data-state='done'],.dark #" + ID + "[data-state='done']{",
      "background:rgba(74,222,128,.24);color:#a7f3c0;border-color:rgba(74,222,128,.48)}",

      "#" + ID + "[data-state='error']{background:rgba(220,38,38,.16);",
      "color:#b91c1c;border-color:rgba(220,38,38,.40)}",
      "html.dark #" + ID + "[data-state='error'],.dark #" + ID + "[data-state='error']{",
      "background:rgba(248,113,113,.26);color:#fecaca;border-color:rgba(248,113,113,.52)}",

      "#" + ID + "[data-hidden='1']{display:none}",

      "#" + ID + " .ris-icon{flex:0 0 auto;width:12px;height:12px;display:flex;",
      "align-items:center;justify-content:center}",
      "#" + ID + " .ris-label{white-space:nowrap;overflow:hidden;",
      "text-overflow:ellipsis}",
      "#" + ID + " .ris-dot{width:7px;height:7px;border-radius:50%;",
      "background:currentColor;opacity:.85}",
      "#" + ID + " .ris-spinner{width:11px;height:11px;border-radius:50%;",
      "border:2px solid currentColor;border-top-color:transparent;opacity:.9;",
      "animation:ris-spin .8s linear infinite}",
      "@keyframes ris-spin{to{transform:rotate(360deg)}}",
      "#" + ID + " .ris-check{font-size:12px;font-weight:700;line-height:1}",

      /* The panel is position:fixed and placed by JS, because the badge sits in the
       * header and a CSS-anchored panel would run off the edge of the window. */
      "#" + ID + "-panel{position:fixed;z-index:2147483001;",
      "min-width:230px;max-width:min(340px,calc(100vw - 24px));",
      "padding:10px 12px;border-radius:12px;font-size:12px;font-weight:400;",
      "line-height:1.45;text-align:left;color:#0f172a;background:#fff;",
      "border:1px solid #e2e8f0;box-shadow:0 10px 28px rgba(15,23,42,.16);",
      "opacity:0;visibility:hidden;transform:translateY(-4px);pointer-events:none;",
      "transition:opacity .15s ease,transform .15s ease,visibility .15s}",
      "html.dark #" + ID + "-panel,.dark #" + ID + "-panel{color:#e8eef8;",
      "background:#111826;border-color:#2b3648;",
      "box-shadow:0 10px 28px rgba(0,0,0,.5)}",
      "#" + ID + "-panel[data-open='1']{opacity:1;visibility:visible;",
      "transform:translateY(0)}",
      "#" + ID + "-panel .ris-panel-title{font-weight:600;margin-bottom:6px}",
      "#" + ID + "-panel .ris-file{display:flex;gap:6px;align-items:baseline;",
      "margin-top:3px;word-break:break-word}",
      "#" + ID + "-panel .ris-file-action{flex:0 0 auto;font-size:10px;",
      "font-weight:700;text-transform:uppercase;letter-spacing:.03em;opacity:.75;",
      "min-width:52px}",
      "#" + ID + "-panel .ris-hint{margin-top:8px;opacity:.7}",

      "@media (prefers-reduced-motion:reduce){#" + ID + ",#" + ID + "-panel{",
      "transition:none}#" + ID + " .ris-spinner{animation-duration:2s}}",
      "@media (max-width:560px){#" + ID + "{max-width:44vw}",
      "#" + ID + " .ris-label{display:none}}",
    ].join("");
    document.head.appendChild(css);
  }

  function panelElement() {
    var panel = document.getElementById(ID + "-panel");
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = ID + "-panel";
    document.body.appendChild(panel);
    return panel;
  }

  function positionPanel() {
    var el = document.getElementById(ID);
    var panel = document.getElementById(ID + "-panel");
    if (!el || !panel) return;
    var r = el.getBoundingClientRect();
    // Measure, then clamp into the viewport: the badge may sit anywhere along the
    // header, so a fixed left or right anchor would overflow on one side.
    panel.style.left = "0px";
    panel.style.top = r.bottom + 8 + "px";
    var w = panel.offsetWidth;
    var left = Math.min(
      Math.max(8, r.left + r.width / 2 - w / 2),
      Math.max(8, window.innerWidth - w - 8)
    );
    panel.style.left = left + "px";
  }

  function element() {
    var el = document.getElementById(ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = ID;
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = '<span class="ris-icon"></span><span class="ris-label"></span>';
    el.addEventListener("mouseenter", function () {
      var panel = panelElement();
      panel.setAttribute("data-open", "1");
      positionPanel();
    });
    el.addEventListener("mouseleave", function () {
      panelElement().removeAttribute("data-open");
    });
    place(el);
    return el;
  }

  function place(el) {
    var anchor = document.getElementById(ANCHOR_ID);
    if (anchor && anchor.parentNode) {
      if (el.previousElementSibling !== anchor) {
        anchor.parentNode.insertBefore(el, anchor.nextSibling);
      }
      el.removeAttribute("data-floating");
      return true;
    }
    if (!el.parentNode) document.body.appendChild(el);
    // Only give up on the header once it has had time to render.
    if (Date.now() - startedAt > FALLBACK_AFTER_MS) {
      el.setAttribute("data-floating", "1");
    }
    return false;
  }

  function escapeHtml(text) {
    var d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function panelHtml(status) {
    var title;
    if (status.state === "working") title = strings.titleWorking;
    else if (status.state === "done") title = strings.titleDone;
    else if (status.state === "error") title = strings.titleError;
    else title = lastPayload ? strings.titleDone : strings.titleIdle;

    var html = '<div class="ris-panel-title">' + escapeHtml(title) + "</div>";
    var message = messageFor(status) || (lastPayload && messageFor(lastPayload)) || "";
    html += "<div>" + escapeHtml(message || strings.nothingYet) + "</div>";

    var files = status.files || (lastPayload && lastPayload.files) || [];
    for (var i = 0; i < files.length; i++) {
      // The overflow row carries only a count; the sentence around it is written
      // here, where the language is known.
      var more = files[i].action === "more";
      html +=
        '<div class="ris-file"><span class="ris-file-action">' +
        escapeHtml(more ? "" : strings.actions[files[i].action] || files[i].action) +
        '</span><span>' +
        escapeHtml(more ? fill(strings.andMore, { n: files[i].name }) : files[i].name) +
        "</span></div>";
    }
    html += '<div class="ris-hint">' + escapeHtml(strings.hint) + "</div>";
    return html;
  }

  function render(status) {
    styles();
    var el = element();
    place(el);
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

    var label = messageFor(status);
    if (status.state === "idle") {
      label = lastPayload ? strings.upToDate : strings.watching;
    }
    el.querySelector(".ris-label").textContent = label;
    el.setAttribute("title", label);
    panelElement().innerHTML = panelHtml(status);
    if (panelElement().getAttribute("data-open") === "1") positionPanel();
  }

  function clearTimers() {
    if (calmTimer) clearTimeout(calmTimer);
    calmTimer = null;
  }

  function apply(status) {
    if (!status || typeof status.revision !== "number") return;
    // Before any rendering: `status.lang` carries `[UI] language` when an instance
    // forces one, and null means "let the browser decide".
    setLang(status.lang);

    if (status.state === "off") {
      var existing = document.getElementById(ID);
      if (existing) existing.setAttribute("data-hidden", "1");
      return;
    }

    if (status.state === "idle") {
      render(status);
      return;
    }

    if (status.state === "working") {
      lastRevision = status.revision;
      lastPayload = status;
      clearTimers();
      render(status);
      return;
    }

    // A finished or failed run. Announce a new one, then let it drift away; an
    // already-announced one must not restart its fade on every poll.
    if (status.revision === lastRevision) return;

    lastRevision = status.revision;
    lastPayload = status;
    clearTimers();
    render(status);

    if (status.state === "error") return; // stays put until something changes

    calmTimer = setTimeout(function () {
      // Once the colour has gone, drop back to the neutral look. The panel keeps
      // the detail, so hovering still explains what happened.
      var calm = {};
      for (var k in status) calm[k] = status[k];
      calm.state = "idle";
      render(calm);
    }, HOLD_DONE_MS);
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
    // The header is React-rendered, so a re-render can drop the badge. Put it back.
    var observer = new MutationObserver(function () {
      var el = document.getElementById(ID);
      if (el) place(el);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener("resize", positionPanel);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

/*
 * A second chip: the active configuration, next to the watcher badge.
 *
 * Answers "which config is this container actually running?" from the browser —
 * instance name in the chip, the details (models, collection, chunking,
 * retrieval switches) in a hover panel. Lives in this file because Chainlit
 * loads exactly one custom_js (see the header comment above).
 *
 * Unlike the watcher badge this is static for the process lifetime, so there is
 * no polling: fetch until the first success (401 before login is normal), then
 * render once and stop.
 */
(function () {
  "use strict";

  var ENDPOINT = "/config-info";
  var RETRY_MS = 5000;
  var ID = "rag-config-info";
  var ANCHOR_IDS = ["rag-ingest-status", "readme-button"];

  var info = null;

  function styles() {
    if (document.getElementById(ID + "-styles")) return;
    var css = document.createElement("style");
    css.id = ID + "-styles";
    css.textContent = [
      /* Same furniture as the watcher badge, permanently in its neutral look. */
      "#" + ID + "{display:inline-flex;align-items:center;gap:7px;",
      "box-sizing:border-box;vertical-align:middle;margin-left:6px;",
      "height:30px;padding:0 11px;border-radius:15px;",
      "font-size:12.5px;font-weight:500;line-height:1;font-family:inherit;",
      "max-width:min(260px,32vw);border:1px solid rgba(148,163,184,.34);",
      "background:rgba(148,163,184,.18);color:#475569;cursor:pointer;",
      "user-select:none;-webkit-appearance:none;appearance:none}",
      /* It is a real <button>, so it must not lose its focus ring. */
      "#" + ID + ":focus-visible{outline:2px solid currentColor;outline-offset:2px}",
      /* Used only when no header anchor exists — same treatment as the badge,
       * stacked below it, since the badge may be floating at 62px itself. */
      "#" + ID + "[data-floating='1']{position:fixed;top:100px;right:14px;",
      "z-index:2147483000;margin-left:0;backdrop-filter:blur(6px);",
      "-webkit-backdrop-filter:blur(6px)}",
      "html.dark #" + ID + ",.dark #" + ID + "{background:rgba(148,163,184,.26);",
      "color:#e2e8f0;border-color:rgba(148,163,184,.42)}",
      "#" + ID + " .rci-label{white-space:nowrap;overflow:hidden;",
      "text-overflow:ellipsis}",
      "#" + ID + " .rci-gear{font-size:12px;line-height:1;opacity:.85}",

      "#" + ID + "-panel{position:fixed;z-index:2147483001;",
      "min-width:250px;max-width:min(380px,calc(100vw - 24px));",
      "padding:10px 12px;border-radius:12px;font-size:12px;font-weight:400;",
      "line-height:1.5;text-align:left;color:#0f172a;background:#fff;",
      "border:1px solid #e2e8f0;box-shadow:0 10px 28px rgba(15,23,42,.16);",
      "opacity:0;visibility:hidden;transform:translateY(-4px);pointer-events:none;",
      "transition:opacity .15s ease,transform .15s ease,visibility .15s}",
      "html.dark #" + ID + "-panel,.dark #" + ID + "-panel{color:#e8eef8;",
      "background:#111826;border-color:#2b3648;box-shadow:0 10px 28px rgba(0,0,0,.5)}",
      "#" + ID + "-panel[data-open='1']{opacity:1;visibility:visible;transform:translateY(0)}",
      "#" + ID + "-panel .rci-title{font-weight:600;margin-bottom:6px}",
      "#" + ID + "-panel .rci-row{display:flex;gap:8px;margin-top:2px}",
      "#" + ID + "-panel .rci-key{flex:0 0 92px;opacity:.7}",
      "#" + ID + "-panel .rci-val{word-break:break-word}",
      "#" + ID + "-panel .rci-hint{margin-top:8px;opacity:.7}",
      "@media (max-width:560px){#" + ID + "{max-width:38vw}",
      "#" + ID + " .rci-label{display:none}}",
    ].join("");
    document.head.appendChild(css);
  }

  function esc(text) {
    var d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function row(key, value) {
    return (
      '<div class="rci-row"><span class="rci-key">' + esc(key) +
      '</span><span class="rci-val">' + esc(value) + "</span></div>"
    );
  }

  function panelHtml() {
    var r = info.retrieval || {};
    var retrieval = r.hybrid
      ? "hybrid (" + r.fusion + ", " + r.prefetch_limit + "→" + r.top_k + ")"
      : "dense, top_k " + r.top_k;
    var chunking = (info.sources || [])
      .map(function (s) { return s.name + ": " + s.chunking; })
      .join(" · ");
    var html = '<div class="rci-title">Active configuration</div>';
    html += row("config", info.name + " (" + info.config_path + ")");
    html += row("collection", info.collection);
    html += row("chat model", info.chat_model + " (default)");
    html += row("embedding", info.embed_model);
    if (chunking) html += row("chunking", chunking);
    html += row("retrieval", retrieval);
    html += row("images", info.images_mode);
    html += row("tools", (info.tools || []).join(", "));
    html += '<div class="rci-hint">Set via RAG_CONFIG — restart to change.</div>';
    return html;
  }

  function panelElement() {
    var panel = document.getElementById(ID + "-panel");
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = ID + "-panel";
    document.body.appendChild(panel);
    return panel;
  }

  function positionPanel() {
    var el = document.getElementById(ID);
    var panel = document.getElementById(ID + "-panel");
    if (!el || !panel) return;
    var r = el.getBoundingClientRect();
    panel.style.left = "0px";
    panel.style.top = r.bottom + 8 + "px";
    var w = panel.offsetWidth;
    panel.style.left =
      Math.min(
        Math.max(8, r.left + r.width / 2 - w / 2),
        Math.max(8, window.innerWidth - w - 8)
      ) + "px";
  }

  function place(el) {
    for (var i = 0; i < ANCHOR_IDS.length; i++) {
      var anchor = document.getElementById(ANCHOR_IDS[i]);
      // Skip an anchor that is itself floating: inserting after an out-of-flow
      // element would drop the chip into body flow, below the whole app.
      if (anchor && anchor.parentNode && anchor.getAttribute("data-floating") !== "1") {
        if (el.previousElementSibling !== anchor) {
          anchor.parentNode.insertBefore(el, anchor.nextSibling);
        }
        el.removeAttribute("data-floating");
        return;
      }
    }
    // No header anchor (no readme button, or the badge is floating too). Pin to
    // a fixed corner like the badge does, rather than appending a static button
    // to <body> where it renders below the app and is never seen.
    if (!el.parentNode) document.body.appendChild(el);
    el.setAttribute("data-floating", "1");
  }

  function setOpen(el, open) {
    var panel = panelElement();
    if (open) {
      panel.setAttribute("data-open", "1");
      positionPanel();
    } else {
      panel.removeAttribute("data-open");
    }
    el.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function render() {
    styles();
    var el = document.getElementById(ID);
    if (!el) {
      // A real button, not a div: hover alone leaves keyboard and touch users
      // with no way to read the panel, and this is the only place the active
      // configuration is shown.
      el = document.createElement("button");
      el.id = ID;
      el.type = "button";
      el.setAttribute("aria-haspopup", "true");
      el.setAttribute("aria-expanded", "false");
      el.innerHTML =
        '<span class="rci-gear" aria-hidden="true">⚙</span>' +
        '<span class="rci-label"></span>';
      // `pinned` is what click toggles. Reading aria-expanded instead would
      // always find "true", because mouseenter and focus both fire before click
      // — so a click (and every tap on a browser that focuses buttons) closed
      // the panel it had just opened.
      var pinned = false;
      el.addEventListener("mouseenter", function () { setOpen(el, true); });
      el.addEventListener("mouseleave", function () { if (!pinned) setOpen(el, false); });
      el.addEventListener("focus", function () { setOpen(el, true); });
      el.addEventListener("blur", function () { pinned = false; setOpen(el, false); });
      // Tap/click pins it open, so touch — which never hovers — can read it.
      el.addEventListener("click", function () {
        pinned = !pinned;
        setOpen(el, pinned);
      });
      el.addEventListener("keydown", function (event) {
        if (event.key === "Escape") setOpen(el, false);
      });
    }
    el.querySelector(".rci-label").textContent = info.name;
    el.setAttribute("aria-label", "Active configuration: " + info.name);
    el.setAttribute("title", info.name);
    panelElement().innerHTML = panelHtml();
    place(el);
    return el;
  }

  function fetchOnce() {
    fetch(ENDPOINT, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        // 401 is expected before login, so keep trying. A 404 means the route is
        // not registered in this deployment: permanent, so stop rather than poll
        // a missing endpoint for the lifetime of the tab.
        if (r.status === 404) return "gone";
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (data === "gone") return;
        if (!data) return setTimeout(fetchOnce, RETRY_MS);
        info = data;
        render();
        // The header is React-rendered and can drop the chip entirely. Unlike the
        // watcher badge above, this fetches once, so nothing else would ever put
        // it back — rebuild it, do not just re-place it.
        new MutationObserver(function () {
          if (document.getElementById(ID)) {
            place(document.getElementById(ID));
          } else {
            render();
          }
        }).observe(document.documentElement, { childList: true, subtree: true });
        window.addEventListener("resize", positionPanel);
      })
      .catch(function () { setTimeout(fetchOnce, RETRY_MS); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fetchOnce);
  } else {
    fetchOnce();
  }
})();
