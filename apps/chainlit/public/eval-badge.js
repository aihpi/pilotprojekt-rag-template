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

  /* German and English, chosen the way Chainlit chooses its own interface strings:
   * the server sends `[UI] language` when an instance forces one, otherwise the
   * browser decides. Anything that is not German falls back to English, which is
   * Chainlit's own default — so the badge always agrees with the chrome around it.
   *
   * Strings are raw HTML (the panel is built by concatenation, not escaped), which
   * is why entities and the ⌀/ᵢ/↗ characters can appear inline. Values that come
   * from outside — claim text, judge reasons — go through escapeHtml instead. */
  var T = {
    de: {
      scoring: "Bewertung läuft…",
      pendingScore: "Bewertung ausstehend",
      faithfulness: "Treue",
      relevance: "Relevanz",
      answers: ["Antwort", "Antworten"],
      lastAnswer: "Letzte bewertete Antwort",
      claims: ["Aussage", "Aussagen"],
      claimsHead: "{ok} von {n} {claimWord} durch die Quellen gedeckt",
      declined:
        "Relevanz 0%: die Antwort hat sich enthalten (etwa „steht nicht in den " +
        "Dokumenten“). Das ist keine schlechte Antwort, sondern eine verweigerte — " +
        "die Kennzahl wird in diesem Fall auf 0 gesetzt.",
      quality: "Antwortqualität in diesem Gespräch",
      faithDesc: "Wie viele Aussagen der Antwort von den abgerufenen Textstellen gedeckt sind.",
      faithFormula: "Treue = gedeckte Aussagen / alle Aussagen",
      relDesc:
        "Wie gut die Antwort zur Frage passt. Aus der Antwort werden Fragen erzeugt " +
        "und mit der echten Frage verglichen.",
      relFormula: "Relevanz = ⌀ cos( E(erzeugte Frageᵢ) , E(echte Frage) )",
      shown: "Angezeigter Wert",
      shownDesc:
        "Laufender Mittelwert über die bewerteten Antworten dieses Gesprächs " +
        "(n&nbsp;=&nbsp;{n}).",
      shownFormula: "⌀ = (1/n) · Σ Wertᵢ",
      arrow: "Pfeil",
      arrowDesc:
        "Vergleicht die letzte Antwort mit diesem Mittelwert: ↗ besser, ↘ schlechter. " +
        "Erscheint erst ab zwei Antworten.",
      caveat:
        "Beide Werte stammen von einem Sprachmodell, das ein anderes bewertet, und " +
        "tragen dessen Meinung und Rauschen mit. Einzelwerte sagen wenig, " +
        "Veränderungen sagen etwas.",
      tabChat: "Dieses Gespräch",
      tabCompare: "Vergleich",
      compareHint: "Als Veränderung lesen, nicht als Note. Sortiert nach bewerteten Antworten.",
      compareEmpty: "Noch keine bewerteten Antworten.",
      compareError: "Vergleich nicht verfügbar — Eval-Dienst nicht erreichbar.",
      colConfig: "Konfiguration",
      colAnswers: "n",
      loading: "Laden…",
      suggestText: "Starke Antwort — als Gold-Referenz speichern?",
      suggestSave: "Speichern",
      suggestDismiss: "Ignorieren",
      suggestSaved: "Als Gold-Referenz gespeichert ({n} {turnWord}).",
      turns: ["Runde", "Runden"],
      suggestFailed: "Speichern fehlgeschlagen — Dienst nicht erreichbar.",
      markerTitle: "Starke Antwort erkannt — Details im Panel",
    },
    en: {
      scoring: "Scoring…",
      pendingScore: "Score pending",
      faithfulness: "Faithfulness",
      relevance: "Relevance",
      answers: ["answer", "answers"],
      lastAnswer: "Last scored answer",
      claims: ["claim", "claims"],
      claimsHead: "{ok} of {n} {claimWord} backed by the sources",
      declined:
        "Relevance 0%: the answer declined (along the lines of “that is not in the " +
        "documents”). That is not a bad answer but a withheld one — the metric is " +
        "set to 0 in that case.",
      quality: "Answer quality in this conversation",
      faithDesc: "How many of the answer's claims are backed by the retrieved passages.",
      faithFormula: "Faithfulness = backed claims / all claims",
      relDesc:
        "How well the answer fits the question. Questions are generated from the " +
        "answer and compared with the real one.",
      relFormula: "Relevance = ⌀ cos( E(generated questionᵢ) , E(real question) )",
      shown: "Displayed value",
      shownDesc:
        "Running mean over the scored answers in this conversation (n&nbsp;=&nbsp;{n}).",
      shownFormula: "⌀ = (1/n) · Σ valueᵢ",
      arrow: "Arrow",
      arrowDesc:
        "Compares the last answer with that mean: ↗ better, ↘ worse. Appears from " +
        "two answers on.",
      caveat:
        "Both values come from one language model judging another, and carry its " +
        "opinion and its noise. Single values say little, changes say something.",
      tabChat: "This conversation",
      tabCompare: "Compare",
      compareHint: "Read as deltas, not as grades. Sorted by scored answers.",
      compareEmpty: "No scored answers yet.",
      compareError: "Comparison unavailable — eval service unreachable.",
      colConfig: "Configuration",
      colAnswers: "n",
      loading: "Loading…",
      suggestText: "Strong answer — save as a gold reference?",
      suggestSave: "Save",
      suggestDismiss: "Dismiss",
      suggestSaved: "Saved as a gold reference ({n} {turnWord}).",
      turns: ["turn", "turns"],
      suggestFailed: "Saving failed — service unreachable.",
      markerTitle: "Strong answer detected — details in the panel",
    },
  };

  var strings = T.en;
  var langCode = "en";

  function setLang(forced) {
    var tag = String(forced || navigator.language || "en").toLowerCase();
    langCode = tag.indexOf("de") === 0 ? "de" : "en";
    strings = T[langCode];
  }

  function fill(template, values) {
    return template.replace(/\{(\w+)\}/g, function (_, key) {
      return values[key];
    });
  }

  function plural(pair, n) {
    return pair[n === 1 ? 0 : 1];
  }

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
  /* Which panel tab is showing. The conversation is the default; the comparison
   * is fetched lazily when its tab is first opened. */
  var activeTab = "chat";
  var compareRows = null; /* null = not loaded, "error" = fetch failed */
  /* Suggestions the user waved away, keyed by answer id. Page-lifetime on
   * purpose: a dismissal is "not now", not "never" — a reload may ask again.
   * ponytail: move to localStorage if that ever annoys anyone. */
  var dismissedGold = {};
  var goldSavedText = null; /* confirmation shown in place of the suggestion row */

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
    // One delegated listener instead of re-binding after every innerHTML swap.
    panel.addEventListener("click", function (event) {
      var target = event.target.closest ? event.target.closest("[data-tab],[data-gold-save],[data-gold-dismiss]") : null;
      if (!target) return;
      event.stopPropagation();
      if (target.hasAttribute("data-tab")) {
        var tab = target.getAttribute("data-tab");
        if (tab === activeTab) return;
        activeTab = tab;
        if (tab === "compare") loadCompare();
        refreshPanel(true);
      } else if (target.hasAttribute("data-gold-save")) {
        saveGold();
      } else {
        dismissGold();
      }
    });
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
    var out = ["<h4>" + strings.lastAnswer + "</h4>"];

    var claims = d.faithfulness_claims || [];
    if (claims.length) {
      var ok = claims.filter(function (c) { return c.ok; }).length;
      out.push(
        '<div class="reb-claims-head">' +
          fill(strings.claimsHead, {
            ok: ok,
            n: claims.length,
            claimWord: plural(strings.claims, claims.length),
          }) +
          "</div>"
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
      out.push('<div class="reb-note">' + strings.declined + "</div>");
    }
    return out.join("");
  }

  /* Does the current status carry a live (not dismissed, not saved) suggestion? */
  function goldActive(status) {
    return !!(
      status &&
      status.gold_suggest &&
      status.last_message_id &&
      !dismissedGold[status.last_message_id]
    );
  }

  function suggestionHtml(status) {
    if (goldSavedText) {
      return '<div class="reb-suggest" data-saved="1">&#10003; ' + goldSavedText + "</div>";
    }
    if (!goldActive(status)) return "";
    return (
      '<div class="reb-suggest">' +
      '<span class="reb-gold-mark">!</span>' +
      "<span>" + strings.suggestText + "</span>" +
      '<button type="button" class="reb-save" data-gold-save="1">' +
      strings.suggestSave + "</button>" +
      '<button type="button" class="reb-dismiss" data-gold-dismiss="1" title="' +
      strings.suggestDismiss + '" aria-label="' + strings.suggestDismiss + '">&#10005;</button>' +
      "</div>"
    );
  }

  function tabsHtml() {
    function tab(id, label) {
      return (
        '<button type="button" class="reb-tab" data-tab="' + id + '"' +
        (activeTab === id ? ' data-active="1"' : "") + ">" + label + "</button>"
      );
    }
    return '<div class="reb-tabs">' + tab("chat", strings.tabChat) + tab("compare", strings.tabCompare) + "</div>";
  }

  function compareHtml() {
    if (compareRows === null) return '<p class="reb-hint">' + strings.loading + "</p>";
    if (compareRows === "error") return '<p class="reb-hint">' + strings.compareError + "</p>";
    if (!compareRows.length) return '<p class="reb-hint">' + strings.compareEmpty + "</p>";

    function cell(value, series) {
      if (value === null || value === undefined) return "<td></td>";
      return (
        '<td><div class="reb-cbar"><span class="reb-track"><span class="reb-fill ' +
        series + '" style="width:' + pct(value) + '"></span></span><b>' +
        pct(value) + "</b></div></td>"
      );
    }

    var rows = compareRows
      .slice()
      .sort(function (a, b) { return b.answers - a.answers; })
      .map(function (r) {
        var parts = String(r.config_signature || "").split("|");
        var name = escapeHtml(parts[0] || "?");
        var sub = parts.length >= 5
          ? escapeHtml(parts[2] + " @ " + parts[3] + " · " + parts[4])
          : "";
        return (
          "<tr><td><b>" + name + "</b>" +
          (sub ? '<small>' + sub + "</small>" : "") + "</td>" +
          '<td class="reb-n">' + r.answers + "</td>" +
          cell(r.faithfulness, "f") + cell(r.relevance, "r") + "</tr>"
        );
      })
      .join("");

    return (
      '<p class="reb-hint">' + strings.compareHint + "</p>" +
      '<table class="reb-table"><thead><tr><th>' + strings.colConfig +
      '</th><th class="reb-n">' + strings.colAnswers + "</th><th>" +
      strings.faithfulness + "</th><th>" + strings.relevance + "</th></tr></thead>" +
      "<tbody>" + rows + "</tbody></table>"
    );
  }

  function loadCompare() {
    compareRows = null;
    fetch("/eval-stats", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (payload) { compareRows = payload.configs || []; })
      .catch(function () { compareRows = "error"; })
      .then(function () {
        if (isOpen() && activeTab === "compare") refreshPanel();
      });
  }

  function conversationHtml(status) {
    return [
      detailHtml(status),
      "<h4>" + strings.quality + "</h4>",
      "<dl>",
      "<dt>" + strings.faithfulness + "</dt><dd>",
      strings.faithDesc,
      '<code class="reb-formula">' + strings.faithFormula + "</code>",
      "</dd>",
      "<dt>" + strings.relevance + "</dt><dd>",
      strings.relDesc,
      '<code class="reb-formula">' + strings.relFormula + "</code>",
      "</dd>",
      "<dt>" + strings.shown + "</dt><dd>",
      fill(strings.shownDesc, { n: status.answers }),
      '<code class="reb-formula">' + strings.shownFormula + "</code>",
      "</dd>",
      "<dt>" + strings.arrow + "</dt><dd>",
      strings.arrowDesc,
      "</dd>",
      "</dl>",
      '<div class="reb-warn">' + strings.caveat + "</div>",
    ].join("");
  }

  function panelHtml(status) {
    return (
      suggestionHtml(status) +
      tabsHtml() +
      (activeTab === "compare" ? compareHtml() : conversationHtml(status))
    );
  }

  /* Redraw the open panel in place, keeping scroll position semantics simple:
   * a tab switch starts at the top, a data refresh keeps the reader's place. */
  function refreshPanel(resetScroll) {
    if (!panel || !isOpen() || !lastStatus) return;
    panel.innerHTML = panelHtml(lastStatus);
    if (resetScroll) panel.scrollTop = 0;
    positionPanel();
  }

  function saveGold() {
    var status = lastStatus;
    if (!goldActive(status)) return;
    var messageId = status.last_message_id;
    var m = /\/thread\/([0-9a-fA-F-]{36})/.exec(location.pathname);
    fetch("/eval-gold", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ thread_id: m ? m[1] : status.thread_id, message_id: messageId }),
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (payload) {
        var n = payload.turns || 1;
        goldSavedText = fill(strings.suggestSaved, { n: n, turnWord: plural(strings.turns, n) });
      })
      .catch(function () {
        goldSavedText = strings.suggestFailed;
      })
      .then(function () {
        dismissedGold[messageId] = true; // the marker's job is done either way
        refreshPanel();
        render(lastStatus);
        setTimeout(function () {
          goldSavedText = null;
          refreshPanel();
          poll(); // the server now reports the answer as gold
        }, 4000);
      });
  }

  function dismissGold() {
    if (lastStatus && lastStatus.last_message_id) {
      dismissedGold[lastStatus.last_message_id] = true;
    }
    refreshPanel();
    render(lastStatus);
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
    if (activeTab === "compare") loadCompare(); // reopenings get fresh numbers
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

    // A judge takes ~16s, which is gateway round-trip per call rather than anything
    // we can shorten. Saying so beats leaving the badge blank and looking broken —
    // and on the first scored answer of a conversation there is nothing else to show.
    if (!status.answers) {
      if (!status.pending) {
        el.removeAttribute("data-show");
        return;
      }
      el.innerHTML = '<span class="reb-count">' + strings.scoring + "</span>";
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
    var faith = metric(strings.faithfulness, status.faithfulness, status.trend);
    if (faith) parts.push(faith);
    var rel = metric(strings.relevance, status.relevance, status.trend_relevance);
    if (rel) parts.push(rel);

    // Nothing scored yet in a conversation that has scored attempts: say so rather
    // than showing an empty pill, so "on but quiet" is distinguishable from "off".
    if (!parts.length) {
      parts.push('<span class="reb-count">' + strings.pendingScore + "</span>");
    } else {
      parts.push(
        '<span class="reb-count">' +
          status.answers +
          " " +
          plural(strings.answers, status.answers) +
          "</span>"
      );
    }

    // The quest marker: a strong answer is waiting to be saved as gold. The CSS
    // animation plays once when the element is (re)created, i.e. when the marker
    // first appears — later identical payloads never re-render, so it sits still.
    if (goldActive(status)) {
      parts.push(
        '<span class="reb-gold-mark" title="' + strings.markerTitle + '">!</span>'
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
    // No id until Chainlit routes to /thread/<uuid> on the first answer; the server
    // reports nothing rather than guessing a conversation.
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
        // Before any rendering: `status.lang` carries `[UI] language` when an
        // instance forces one, and null means "let the browser decide".
        setLang(status.lang);
        // The language is part of the key, not just the numbers: what is on screen
        // depends on both, and re-rendering only when a score moves would leave the
        // badge in the previous language until one did.
        var payload = langCode + JSON.stringify(status);
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
