/* study.js — load a deck (+ its subdecks) and run an Anki-like study session */
(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var col = params.get("col");
  var deckId = params.get("deck");
  var M = window.MANIFEST || { collections: [] };

  var $ = function (id) { return document.getElementById(id); };
  var sideEl = $("side");
  var controlsEl = $("controls");
  var breadcrumbEl = $("breadcrumb");

  var collection = M.collections.filter(function (c) { return c.slug === col; })[0];
  if (!collection) { fatal("Collection introuvable."); return; }
  var deckEntry = collection.decks.filter(function (d) { return String(d.id) === String(deckId); })[0];
  if (!deckEntry) { fatal("Deck introuvable."); return; }

  document.title = leafName(deckEntry.name) + " — FlashAnat";
  // tag the card elements with the collection so the right styling applies
  sideEl.className = "card-side card col-" + col;
  $("cardbox").className = "cardbox col-" + col;
  breadcrumbEl.innerHTML = deckEntry.name.split("::")
    .map(function (p, i, a) { return i === a.length - 1 ? "<b>" + esc(p) + "</b>" : esc(p); })
    .join(" <span style='opacity:.5'>›</span> ");

  // Which deck data files to load: this deck + every descendant that has cards.
  var prefix = deckEntry.name + "::";
  var toLoad = collection.decks.filter(function (d) {
    return d.own > 0 && (d.name === deckEntry.name || d.name.indexOf(prefix) === 0);
  }).sort(function (a, b) { return a.name.localeCompare(b.name, "fr"); });

  // --- load the per-deck data files dynamically (works over file:// too) ---
  window.DECKDATA = {};
  var pending = toLoad.length;
  if (pending === 0) { fatal("Ce deck ne contient aucune carte."); return; }
  toLoad.forEach(function (d) {
    var s = document.createElement("script");
    s.src = "data/" + col + "/" + d.id + ".js";
    s.onload = done;
    s.onerror = function () { console.warn("Échec chargement deck", d.id); done(); };
    document.head.appendChild(s);
  });
  function done() { if (--pending === 0) build(); }

  // --- session state ------------------------------------------------------
  var allCards = [];
  var queue = [];
  var pos = 0;
  var revealed = false;
  var known = loadKnown();
  var opts = { shuffle: false, hideKnown: false };

  // auto-advance ("lecture auto") + fullscreen state
  var auto = loadAuto();        // { on:bool, q:seconds, a:seconds }
  var autoT = null;             // pending auto-advance timeout
  var paused = false;           // paused while a lightbox is open / tab hidden
  var autobarEl = null, autofillEl = null;
  var fsSupported = !!(document.documentElement.requestFullscreen ||
    document.documentElement.webkitRequestFullscreen);

  function build() {
    toLoad.forEach(function (d) {
      var dd = window.DECKDATA[d.id];
      if (!dd) return;
      dd.c.forEach(function (pair, i) {
        allCards.push({ uid: d.id + "#" + i, front: pair[0], back: pair[1], deck: dd.n });
      });
    });
    if (!allCards.length) { fatal("Aucune carte chargée."); return; }
    wireTools();
    wireAuto();
    wireFullscreen();
    wireKeys();
    rebuildQueue();
  }

  function rebuildQueue() {
    queue = allCards.filter(function (c) { return opts.hideKnown ? !known[c.uid] : true; });
    if (opts.shuffle) shuffle(queue);
    pos = 0;
    revealed = false;
    if (!queue.length) { renderAllKnown(); return; }
    render();
  }

  // --- rendering ----------------------------------------------------------
  function render() {
    if (pos >= queue.length) { renderDone(); return; }
    var card = queue[pos];
    sideEl.innerHTML = revealed ? card.back : card.front;
    attachImages();
    renderControls();
    updateStats();
    $("cardbox").scrollTop = 0;
    scheduleAuto();
  }

  function renderControls() {
    var card = queue[pos];
    var isKnown = !!known[card.uid];
    if (!revealed) {
      controlsEl.innerHTML =
        "<div class='row'>" +
          navBtn("prev", "◀") +
          "<button class='btn primary' data-act='reveal' style='min-width:260px'>Afficher la réponse</button>" +
          navBtn("next", "▶") +
        "</div>" +
        keysHint("<kbd>Espace</kbd> afficher · <kbd>←</kbd><kbd>→</kbd> naviguer · <kbd>F</kbd> plein écran · <kbd>A</kbd> lecture auto");
    } else {
      controlsEl.innerHTML =
        "<div class='row'>" +
          navBtn("prev", "◀") +
          "<button class='btn again' data-act='again'>À revoir</button>" +
          "<button class='btn good' data-act='good'>" + (isKnown ? "Acquise ✓" : "Acquis") + "</button>" +
          navBtn("next", "▶") +
        "</div>" +
        keysHint("<kbd>1</kbd> à revoir · <kbd>2</kbd>/<kbd>Espace</kbd> acquis · <kbd>F</kbd> plein écran · <kbd>A</kbd> lecture auto");
    }
    Array.prototype.forEach.call(controlsEl.querySelectorAll("[data-act]"), function (b) {
      b.addEventListener("click", function () { act(b.dataset.act); });
    });
  }

  function navBtn(act, label) {
    return "<button class='btn ghost' data-act='" + act + "'>" + label + "</button>";
  }
  function keysHint(html) { return "<div class='hint-keys'>" + html + "</div>"; }

  function act(a) {
    switch (a) {
      case "reveal": revealed = true; render(); break;
      case "good":
        if (!revealed) { revealed = true; render(); break; }
        known[queue[pos].uid] = 1; saveKnown(); pos++; revealed = false; render(); break;
      case "again":
        // re-queue this card to see it again later this session
        delete known[queue[pos].uid]; saveKnown();
        queue.push(queue[pos]); pos++; revealed = false; render(); break;
      case "next": pos = Math.min(pos + 1, queue.length); revealed = false; render(); break;
      case "prev": pos = Math.max(pos - 1, 0); revealed = false; render(); break;
    }
  }

  function updateStats() {
    var total = queue.length;
    $("pos").textContent = Math.min(pos + 1, total) + " / " + total;
    var knownInScope = allCards.filter(function (c) { return known[c.uid]; }).length;
    $("known").textContent = knownInScope + " acquise" + (knownInScope > 1 ? "s" : "");
    var remaining = Math.max(0, queue.length - pos - 1);
    $("review").textContent = remaining + " restante" + (remaining > 1 ? "s" : "");
    $("progressbar").style.width = (total ? (pos / total) * 100 : 0) + "%";
  }

  function renderDone() {
    clearAuto(); hideAutobar();
    var knownInScope = allCards.filter(function (c) { return known[c.uid]; }).length;
    $("progressbar").style.width = "100%";
    controlsEl.innerHTML = "";
    sideEl.innerHTML =
      "<div class='done'>" +
        "<div class='check'>✓</div>" +
        "<h2>Session terminée&nbsp;!</h2>" +
        "<p>" + allCards.length + " carte(s) dans ce chapitre · " +
        knownInScope + " marquée(s) acquise(s).</p>" +
        "<div class='row' style='display:flex;gap:12px;justify-content:center'>" +
          "<button class='btn primary' id='again-all'>Recommencer</button>" +
          "<a class='btn' href='index.html' style='display:grid;place-items:center'>Autres decks</a>" +
        "</div>" +
      "</div>";
    $("again-all").addEventListener("click", function () { rebuildQueue(); });
    updateStats();
  }

  function renderAllKnown() {
    clearAuto(); hideAutobar();
    controlsEl.innerHTML = "";
    sideEl.innerHTML =
      "<div class='done'>" +
        "<div class='check'>🎉</div>" +
        "<h2>Tout est acquis&nbsp;!</h2>" +
        "<p>Toutes les cartes de ce chapitre sont marquées comme acquises.</p>" +
        "<div class='row' style='display:flex;gap:12px;justify-content:center'>" +
          "<button class='btn primary' id='show-all'>Tout revoir quand même</button>" +
          "<a class='btn' href='index.html' style='display:grid;place-items:center'>Autres decks</a>" +
        "</div>" +
      "</div>";
    $("show-all").addEventListener("click", function () {
      opts.hideKnown = false; $("t-skip").classList.remove("active"); rebuildQueue();
    });
  }

  // --- tools (shuffle / hide-known / restart) -----------------------------
  function wireTools() {
    $("t-shuffle").addEventListener("click", function () {
      opts.shuffle = !opts.shuffle; this.classList.toggle("active", opts.shuffle); rebuildQueue();
    });
    $("t-skip").addEventListener("click", function () {
      opts.hideKnown = !opts.hideKnown; this.classList.toggle("active", opts.hideKnown); rebuildQueue();
    });
    $("t-restart").addEventListener("click", function () { rebuildQueue(); });
  }

  // --- keyboard -----------------------------------------------------------
  function wireKeys() {
    document.addEventListener("keydown", function (e) {
      // never hijack typing in the timer inputs
      var t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
                t.tagName === "SELECT" || t.isContentEditable)) return;
      if ($("lightbox").classList.contains("open")) { closeLightbox(); return; }
      switch (e.key) {
        case " ": case "Enter": e.preventDefault(); act(revealed ? "good" : "reveal"); break;
        case "1": if (revealed) act("again"); break;
        case "2": if (revealed) act("good"); break;
        case "f": case "F": e.preventDefault(); toggleFullscreen(); break;
        case "a": case "A": setAuto(!auto.on); break;
        case "ArrowRight": act("next"); break;
        case "ArrowLeft": act("prev"); break;
      }
    });
  }

  // --- fullscreen ---------------------------------------------------------
  function inFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
  }
  function toggleFullscreen() {
    if (fsSupported) {
      if (inFullscreen()) {
        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      } else {
        var el = document.documentElement;
        (el.requestFullscreen || el.webkitRequestFullscreen).call(el);
      }
      // the body class is synced by the fullscreenchange handler below
    } else {
      // browsers without the Fullscreen API: fall back to a CSS-only immersive view
      document.body.classList.toggle("immersive");
      syncFullBtn();
    }
  }
  function onFsChange() {
    document.body.classList.toggle("immersive", inFullscreen());
    syncFullBtn();
  }
  function syncFullBtn() {
    var b = $("t-full");
    if (!b) return;
    var on = document.body.classList.contains("immersive");
    b.classList.toggle("active", on);
    b.title = on ? "Quitter le plein écran (F)" : "Plein écran (F)";
  }
  function wireFullscreen() {
    $("t-full").addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", onFsChange);
    document.addEventListener("webkitfullscreenchange", onFsChange);
    syncFullBtn();
  }

  // --- auto-advance ("lecture auto", comme Anki) --------------------------
  function wireAuto() {
    autobarEl = $("autobar"); autofillEl = $("autobar-fill");
    var qIn = $("auto-q"), aIn = $("auto-a"), pop = $("auto-pop"), cfg = $("t-auto-cfg");
    qIn.value = auto.q; aIn.value = auto.a;
    reflectAuto();

    $("t-auto").addEventListener("click", function () { setAuto(!auto.on); });

    cfg.addEventListener("click", function (e) { e.stopPropagation(); pop.hidden = !pop.hidden; });
    document.addEventListener("click", function (e) {
      if (!pop.hidden && !pop.contains(e.target) && e.target !== cfg) pop.hidden = true;
    });

    function commit() {
      auto.q = clampInt(qIn.value, 1, 600, 10);
      auto.a = clampInt(aIn.value, 1, 600, 5);
      qIn.value = auto.q; aIn.value = auto.a;
      saveAuto();
      if (auto.on) scheduleAuto();   // apply the new timing immediately
    }
    qIn.addEventListener("change", commit);
    aIn.addEventListener("change", commit);
  }
  function reflectAuto() {
    var b = $("t-auto");
    b.classList.toggle("active", auto.on);
    b.innerHTML = (auto.on ? "⏸" : "▶") + " Lecture auto";
  }
  function setAuto(on) {
    auto.on = on; saveAuto(); reflectAuto();
    if (on) scheduleAuto(); else { clearAuto(); hideAutobar(); }
  }
  function scheduleAuto() {
    clearAuto();
    if (!auto.on || paused || lightboxOpen()) return;
    if (pos >= queue.length) return;                 // done / nothing to show
    var secs = revealed ? auto.a : auto.q;
    if (!(secs > 0)) return;
    startCountdown(secs, revealed);
    autoT = setTimeout(function () {
      autoT = null;
      act(revealed ? "next" : "reveal");
    }, secs * 1000);
  }
  function clearAuto() {
    if (autoT) { clearTimeout(autoT); autoT = null; }
    stopCountdown();
  }
  function startCountdown(secs, isAnswer) {
    if (!autofillEl) return;
    autobarEl.classList.add("on");
    autobarEl.classList.toggle("answer", !!isAnswer);
    autofillEl.style.transition = "none";
    autofillEl.style.transform = "scaleX(1)";
    void autofillEl.offsetWidth;                      // force reflow so the transition runs
    autofillEl.style.transition = "transform " + secs + "s linear";
    autofillEl.style.transform = "scaleX(0)";
  }
  function stopCountdown() {
    if (!autofillEl) return;
    autofillEl.style.transition = "none";
    autofillEl.style.transform = "scaleX(1)";
  }
  function hideAutobar() { if (autobarEl) autobarEl.classList.remove("on"); }
  function pauseAuto() { paused = true; clearAuto(); }
  function resumeAuto() { paused = false; scheduleAuto(); }
  function lightboxOpen() { return $("lightbox").classList.contains("open"); }

  // pause the countdown when the tab is hidden, resume when it returns
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) pauseAuto(); else resumeAuto();
  });

  function loadAuto() {
    var d = { on: false, q: 10, a: 5 };
    try {
      var s = JSON.parse(localStorage.getItem("flashanat:autoplay"));
      if (s) {
        if (typeof s.q === "number" && s.q > 0) d.q = s.q;
        if (typeof s.a === "number" && s.a > 0) d.a = s.a;
        d.on = !!s.on;
      }
    } catch (e) {}
    return d;
  }
  function saveAuto() {
    try { localStorage.setItem("flashanat:autoplay", JSON.stringify(auto)); } catch (e) {}
  }
  function clampInt(v, min, max, dflt) {
    v = parseInt(v, 10);
    if (isNaN(v)) return dflt;
    return Math.max(min, Math.min(max, v));
  }

  // --- image lightbox -----------------------------------------------------
  function attachImages() {
    Array.prototype.forEach.call(sideEl.querySelectorAll("img"), function (img) {
      img.addEventListener("click", function () { openLightbox(img.src); });
    });
  }
  function openLightbox(src) {
    $("lightbox-img").src = src;
    $("lightbox").classList.add("open");
    pauseAuto();
  }
  function closeLightbox() {
    $("lightbox").classList.remove("open");
    $("lightbox-img").src = "";
    resumeAuto();
  }
  $("lightbox").addEventListener("click", closeLightbox);

  // --- persistence --------------------------------------------------------
  function knownKey() { return "flashanat:known:" + col; }
  function loadKnown() {
    try { return JSON.parse(localStorage.getItem(knownKey())) || {}; } catch (e) { return {}; }
  }
  function saveKnown() {
    try { localStorage.setItem(knownKey(), JSON.stringify(known)); } catch (e) {}
  }

  // --- helpers ------------------------------------------------------------
  function shuffle(a) {
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
  }
  function leafName(n) { return n.split("::").pop(); }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fatal(msg) {
    if (controlsEl) controlsEl.innerHTML = "";
    if (sideEl) sideEl.innerHTML = "<div class='done'><h2>Oups…</h2><p>" + esc(msg) +
      "</p><a class='btn primary' href='index.html' style='display:inline-grid;place-items:center'>Retour</a></div>";
  }
})();
