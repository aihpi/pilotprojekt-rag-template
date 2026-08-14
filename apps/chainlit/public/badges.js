/*
 * Loader for the header/composer badges.
 *
 * Chainlit accepts exactly one `custom_js`, and there are now two independent
 * badges: the document-watcher status in the header, and the evaluation score above
 * the chatbox. Concatenating them into one file would tangle two unrelated concerns;
 * this loads both and keeps each in its own file.
 *
 * Deliberately not custom.js: that file holds older code that has never been
 * loaded, and enabling it as a side effect of adding a badge would be a surprise.
 */
(function () {
  "use strict";

  ["/public/ingest-status.js", "/public/eval-badge.js"].forEach(function (src) {
    var s = document.createElement("script");
    s.src = src;
    s.defer = true;
    document.head.appendChild(s);
  });
})();
