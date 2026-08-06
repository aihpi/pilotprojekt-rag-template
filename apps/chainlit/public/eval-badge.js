/*
 * A badge above the chatbox showing how this conversation is scoring.
 *
 * Same shape as ingest-status.js and for the same reason: scoring runs in a
 * background task with no live session, so it cannot push anything to a browser.
 * This polls /eval-status instead.
 *
 * It replaced a per-answer line appended under each message, which never worked.
 * Chainlit's Message.update() emits over the session websocket, and a judge takes
 * tens of seconds, so by the time the score arrived the handler was gone and the
 * emit went nowhere silently. A badge belongs to no message, so it does not care
 * how long scoring takes and it repopulates after a reload.
 *
 * Placement: immediately before #message-composer, inside that element's flex
 * column. The column already has `gap-2`, so spacing comes for free and nothing
 * floats over the conversation — the lesson ingest-status.js records from three
 * rounds of placement feedback.
 *
 * No colour bands on the numbers, deliberately. The whole point of the docs is that
 * these values mean nothing in absolute terms; painting 62% red would invite exactly
 * the reading we tell people to avoid. The only judgement shown is the trend arrow,
 * which is relative by construction.
 */
(function () {
  "use strict";

  var ENDPOINT = "/eval-status";
  /* A score lands tens of seconds after its answer, so there is nothing to gain
   * from polling as briskly as the document watcher does while idle. */
  var POLL_MS = 5000;
  /* While a judge is running we know a result is imminent, so check more often
   * rather than making the user wait out an idle interval on top of the judge. */
  var PENDING_POLL_MS = 1500;
  var ID = "rag-eval-badge";
  var ANCHOR_ID = "message-composer";

  var lastPayload = null;
  /* Held in a variable rather than looked up by id every time. The composer is
   * React-rendered and is often not present on the first poll, so the badge starts
   * life detached; getElementById would not find it and we would build a new orphan
   * on every render. */
  var badge = null;
  var panel = null;
  var lastStatus = null;
  var lastPath = null;
  var pollTimer = null;

  /* One self-rescheduling timer rather than a fixed setInterval, so the cadence can
   * follow whether a judge is currently running. */
  function schedule(ms) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, ms);
  }


  function panelElement() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = ID + "-panel";
    document.body.appendChild(panel);
    return panel;
  }

  function positionPanel() {
    if (!badge || !panel || !badge.isConnected) return;
    var r = badge.getBoundingClientRect();
    // Measure first, then clamp into the viewport: the badge is centred over a
    // composer whose width changes with the sidebar, so a fixed anchor on either
    // side would overflow.
    panel.style.left = "0px";
    panel.style.top = "0px";
    var w = panel.offsetWidth;
    var h = panel.offsetHeight;
    panel.style.left =
      Math.min(
        Math.max(8, r.left + r.width / 2 - w / 2),
        Math.max(8, window.innerWidth - w - 8)
      ) + "px";
    // Above the badge, because the badge sits near the bottom of the window.
    panel.style.top = Math.max(8, r.top - h - 8) + "px";
  }

  function escapeHtml(text) {
    var d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  /* What the judge actually decided about the last answer. This is the part that
   * makes a number act like evidence instead of a verdict: rather than telling
   * anyone whether 67% is "good", show that it means two of three claims were
   * backed by the sources, and which one was not. */
  function detailHtml(status) {
    var d = status.detail;
    if (!d) return "";
    var out = ["<h4>Letzte bewertete Antwort</h4>"];

    var claims = d.faithfulness_claims || [];
    if (claims.length) {
      var ok = claims.filter(function (c) { return c.ok; }).length;
      out.push(
        '<div class="reb-claims-head">' +
          ok + " von " + claims.length +
          (claims.length === 1 ? " Aussage" : " Aussagen") +
          " durch die Quellen gedeckt</div>"
      );
      claims.forEach(function (c) {
        out.push(
          '<div class="reb-claim" data-ok="' + (c.ok ? "1" : "0") + '">' +
            '<span class="reb-claim-mark">' + (c.ok ? "&#10003;" : "&#10007;") + "</span>" +
            "<div><div>" + escapeHtml(c.text) + "</div>" +
            (c.why ? '<div class="reb-claim-why">' + escapeHtml(c.why) + "</div>" : "") +
            "</div></div>"
        );
      });
    }

    if (d.relevance_declined) {
      out.push(
        '<div class="reb-note">Relevanz 0%: die Antwort hat sich enthalten ' +
          "(etwa &bdquo;steht nicht in den Dokumenten&ldquo;). Das ist keine " +
          "schlechte Antwort, sondern eine verweigerte &mdash; die Kennzahl wird " +
          "in diesem Fall auf 0 gesetzt.</div>"
      );
    }
    return out.join("");
  }

  function panelHtml(status) {
    var n = status.answers;
    return [detailHtml(status)].concat([
      "<h4>Antwortqualität in diesem Gespräch</h4>",
      "<dl>",
      "<dt>Treue</dt><dd>",
      "Wie viele Aussagen der Antwort von den abgerufenen Textstellen gedeckt sind.",
      '<code class="reb-formula">Treue = gedeckte Aussagen / alle Aussagen</code>',
      "</dd>",
      "<dt>Relevanz</dt><dd>",
      "Wie gut die Antwort zur Frage passt. Aus der Antwort werden Fragen erzeugt und ",
      "mit der echten Frage verglichen.",
      '<code class="reb-formula">Relevanz = ⌀ cos( E(erzeugte Frageᵢ) , E(echte Frage) )</code>',
      "</dd>",
      "<dt>Angezeigter Wert</dt><dd>",
      "Laufender Mittelwert über die bewerteten Antworten dieses Gesprächs",
      " (n&nbsp;=&nbsp;" + n + ").",
      '<code class="reb-formula">⌀ = (1/n) · Σ Wertᵢ</code>',
      "</dd>",
      "<dt>Pfeil</dt><dd>",
      "Vergleicht die letzte Antwort mit diesem Mittelwert: ↗ besser, ↘ schlechter. ",
      "Erscheint erst ab zwei Antworten.",
      "</dd>",
      "</dl>",
      '<div class="reb-warn">',
      "Beide Werte stammen von einem Sprachmodell, das ein anderes bewertet, und tragen ",
      "dessen Meinung und Rauschen mit. Einzelwerte sagen wenig, Veränderungen sagen etwas.",
      "</div>",
    ]).join("");
  }

  function element() {
    if (badge) return badge;
    badge = document.createElement("div");
    badge.id = ID;
    badge.setAttribute("role", "button");
    badge.setAttribute("tabindex", "0");
    badge.setAttribute("aria-expanded", "false");
    badge.setAttribute("aria-live", "polite");

    // Click to toggle, not hover. The panel scrolls when an answer has many claims,
    // and a hover panel cannot be scrolled: reaching for it moves the pointer off the
    // badge and closes the thing you were trying to read.
    badge.addEventListener("click", function (event) {
      event.stopPropagation();
      isOpen() ? closePanel() : openPanel();
    });
    badge.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        isOpen() ? closePanel() : openPanel();
      }
    });
    return badge;
  }

  function isOpen() {
    return !!(panel && panel.getAttribute("data-open"));
  }

  function openPanel() {
    if (!lastStatus) return;
    var p = panelElement();
    p.innerHTML = panelHtml(lastStatus);
    p.setAttribute("data-open", "1");
    p.scrollTop = 0;
    if (badge) badge.setAttribute("aria-expanded", "true");
    positionPanel();
  }

  function closePanel() {
    if (panel) panel.removeAttribute("data-open");
    if (badge) badge.setAttribute("aria-expanded", "false");
  }

  /* Called on every tick, not only when the numbers change: the composer may appear
   * after the first poll, and a React re-render can replace its parent and drop the
   * badge with it. Cheap — one getElementById and an identity check. */
  function place() {
    if (!badge) return false;
    var anchor = document.getElementById(ANCHOR_ID);
    if (!anchor || !anchor.parentNode) return false;
    if (anchor.previousElementSibling !== badge) {
      anchor.parentNode.insertBefore(badge, anchor);
    }
    return true;
  }

  /* Chainlit routes client-side, so switching chats never re-runs this script and
   * nothing here noticed the URL had changed. The badge therefore waited for the next
   * POLL_MS tick: up to 5s late, ~2.5s on average, which read as "switching chats is
   * slow". Worse, until that tick it showed the numbers of the conversation you had
   * just left.
   *
   * Called from the mutation observer (a swap fires hundreds of records) and from
   * popstate. Assigning lastPath BEFORE polling is what keeps that to one request. */
  function onNavigation() {
    if (location.pathname === lastPath) return false;
    lastPath = location.pathname;

    // Blank first. A stale score on the wrong conversation is worse than no score,
    // and this is the half that matters more than the latency.
    if (badge) badge.removeAttribute("data-show");
    if (panel) panel.removeAttribute("data-open");
    lastStatus = null;
    // Otherwise the unchanged-payload check suppresses the re-render whenever the
    // new conversation happens to report the same numbers as the old one.
    lastPayload = null;

    poll();
    return true;
  }

  function pct(value) {
    return Math.round(value * 100) + "%";
  }

  /* Both metrics get a trend arrow. They are running means over the same
   * conversation, so showing one on only one of them just looks like a bug. */
  function metric(label, value, trend) {
    if (value === null || value === undefined) return "";
    var arrow = trend > 0 ? "&#8599;" : trend < 0 ? "&#8600;" : "";
    return (
      '<span class="reb-metric">' +
      label +
      ' <span class="reb-value">' +
      pct(value) +
      "</span>" +
      (arrow ? ' <span class="reb-trend">' + arrow + "</span>" : "") +
      "</span>"
    );
  }

  function render(status) {
    var el = element();

    if (!status || !status.enabled) {
      el.removeAttribute("data-show");
      return;
    }

    // A judge takes ~25s, which is gateway round-trip per call rather than anything
    // we can shorten. Saying so beats leaving the badge blank and looking broken —
    // and on the first scored answer of a conversation there is nothing else to show.
    if (!status.answers) {
      if (!status.pending) {
        el.removeAttribute("data-show");
        return;
      }
      el.innerHTML = '<span class="reb-count">Bewertung läuft…</span>';
      el.setAttribute("data-show", "1");
      el.setAttribute("data-pending", "1");
      return;
    }
    if (status.pending) {
      el.setAttribute("data-pending", "1");
    } else {
      el.removeAttribute("data-pending");
    }

    var parts = [];
    var faith = metric("Treue", status.faithfulness, status.trend);
    if (faith) parts.push(faith);
    var rel = metric("Relevanz", status.relevance, status.trend_relevance);
    if (rel) parts.push(rel);

    // Nothing scored yet in a conversation that has scored attempts: say so rather
    // than showing an empty pill, so "on but quiet" is distinguishable from "off".
    if (!parts.length) {
      parts.push('<span class="reb-count">Bewertung ausstehend</span>');
    } else {
      parts.push(
        '<span class="reb-count">' +
          status.answers +
          (status.answers === 1 ? " Antwort" : " Antworten") +
          "</span>"
      );
    }

    el.innerHTML = parts.join('<span class="reb-sep">&middot;</span>');
    el.setAttribute("data-show", "1");
    // Refresh an open panel in place, so the answer count does not go stale while
    // somebody is reading it.
    if (panel && panel.getAttribute("data-open")) {
      panel.innerHTML = panelHtml(status);
      positionPanel();
    }
  }

  function threadQuery() {
    // ponytail: reads the thread id out of the URL, and lets the server fall back to
    // the newest thread when there is none. A brand-new chat has no thread in its URL
    // until the first answer, so for those few seconds the badge can describe the
    // previous conversation. Pass the id from the session instead if that ever
    // actually confuses anyone.
    var m = /\/thread\/([0-9a-fA-F-]{36})/.exec(location.pathname);
    return m ? "?thread_id=" + encodeURIComponent(m[1]) : "";
  }

  function poll() {
    fetch(ENDPOINT + threadQuery(), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        // 401 before login is expected and not worth shouting about.
        return r.ok ? r.json() : null;
      })
      .then(function (status) {
        // Every exit below has to reschedule. This is a self-rescheduling timeout
        // rather than a setInterval, so a single path that returns without one stops
        // the badge updating for the rest of the page's life.
        if (!status) {
          schedule(POLL_MS);
          return;
        }
        lastStatus = status;
        var payload = JSON.stringify(status);
        if (payload !== lastPayload) {
          lastPayload = payload;
          render(status);
        }
        // Faster only while a judge is actually running, so the number appears when
        // it lands instead of up to an idle interval later.
        schedule(status.pending ? PENDING_POLL_MS : POLL_MS);
        // Outside the changed-payload check on purpose. The numbers usually have not
        // changed, but the badge may still need re-attaching, and skipping this is
        // exactly how it stayed invisible: the first poll ran before the composer
        // existed, and no later tick ever tried again.
        place();
      })
      .catch(function () {
        /* offline or restarting: try again on the next tick */
        schedule(POLL_MS);
      });
  }

  function start() {
    lastPath = location.pathname;
    poll();
    // The composer is React-rendered, so a re-render can drop the badge or replace
    // its parent. Put it back whenever that happens. The same burst of mutations is
    // also the earliest signal that a chat switch is under way, which is why
    // onNavigation is checked here rather than on a timer of its own.
    var observer = new MutationObserver(function () {
      onNavigation();
      if (badge && badge.getAttribute("data-show")) place();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    // Back/forward may not produce the same mutation burst.
    window.addEventListener("popstate", onNavigation);
    window.addEventListener("resize", positionPanel);

    // A click-to-open panel needs the usual ways out. Clicks inside it must not
    // close it, or scrolling by dragging the scrollbar would dismiss it.
    document.addEventListener("click", function (event) {
      if (!isOpen()) return;
      if (panel.contains(event.target) || (badge && badge.contains(event.target))) return;
      closePanel();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && isOpen()) closePanel();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
