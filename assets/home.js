/* home.js — render the collection / deck tree on the home page */
(function () {
  "use strict";
  var M = window.MANIFEST || { collections: [] };
  var container = document.getElementById("collections");

  function fmt(n) { return n.toLocaleString("fr-FR"); }

  // Build a nested tree from the flat deck list (names use "::" separators).
  function buildTree(decks) {
    var byName = {};
    decks.forEach(function (d) {
      byName[d.name] = { id: d.id, name: d.name, own: d.own, total: d.total, children: [] };
    });
    var roots = [];
    decks.forEach(function (d) {
      var parts = d.name.split("::");
      if (parts.length === 1) { roots.push(byName[d.name]); return; }
      var parentName = parts.slice(0, -1).join("::");
      if (byName[parentName]) byName[parentName].children.push(byName[d.name]);
      else roots.push(byName[d.name]);
    });
    var sortRec = function (nodes) {
      nodes.sort(function (a, b) { return a.name.localeCompare(b.name, "fr"); });
      nodes.forEach(function (n) { sortRec(n.children); });
    };
    sortRec(roots);
    return roots;
  }

  function leaf(name) { return name.split("::").pop(); }

  function deckURL(slug, id) {
    return "deck.html?col=" + encodeURIComponent(slug) + "&deck=" + id;
  }

  function renderNode(slug, node, depth) {
    var li = document.createElement("li");
    var row = document.createElement("div");
    row.className = "node-row";
    row.dataset.search = node.name.toLowerCase();

    var hasChildren = node.children.length > 0;
    var twisty = document.createElement("span");
    twisty.className = "twisty" + (hasChildren ? "" : " leaf");
    twisty.textContent = "▾";
    row.appendChild(twisty);

    var name = document.createElement("span");
    name.className = "name";
    name.innerHTML = "<span>" + escapeHtml(leaf(node.name)) + "</span>";
    row.appendChild(name);

    var go = document.createElement("span");
    go.className = "go";
    go.textContent = "Réviser →";
    row.appendChild(go);

    var badge = document.createElement("span");
    badge.className = "badge" + (node.own > 0 ? " has-own" : "");
    badge.textContent = fmt(node.total);
    badge.title = node.total + " carte(s)" + (node.own && node.own !== node.total ? " (dont " + node.own + " ici)" : "");
    row.appendChild(badge);

    li.appendChild(row);

    var childUl = null;
    if (hasChildren) {
      childUl = document.createElement("ul");
      childUl.className = "children";
      node.children.forEach(function (c) { childUl.appendChild(renderNode(slug, c, depth + 1)); });
      li.appendChild(childUl);
    }

    // Clicking the row: go study this deck (includes subdecks).
    row.addEventListener("click", function (e) {
      if (e.target === twisty && hasChildren) { toggle(); return; }
      window.location.href = deckURL(slug, node.id);
    });
    twisty.addEventListener("click", function (e) {
      if (!hasChildren) return;
      e.stopPropagation();
      toggle();
    });
    function toggle() {
      row.classList.toggle("collapsed");
      if (childUl) childUl.classList.toggle("hidden");
    }

    return li;
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  M.collections.forEach(function (col) {
    var section = document.createElement("section");
    section.className = "collection";

    var head = document.createElement("div");
    head.className = "collection-head";
    var total = col.decks.reduce(function (s, d) { return s + d.own; }, 0);
    head.innerHTML =
      "<h2>" + escapeHtml(col.name) + "</h2>" +
      "<span class='meta'>" + fmt(total) + " cartes · " +
      col.decks.filter(function (d) { return d.own > 0; }).length + " chapitres</span>";

    // "study everything" -> the collection root deck
    var root = col.decks.find(function (d) { return d.name.indexOf("::") === -1; });
    if (root) {
      var a = document.createElement("a");
      a.className = "studyall";
      a.href = deckURL(col.slug, root.id);
      a.textContent = "Tout réviser";
      head.appendChild(a);
    }
    section.appendChild(head);

    var tree = document.createElement("ul");
    tree.className = "tree";
    buildTree(col.decks).forEach(function (n) { tree.appendChild(renderNode(col.slug, n, 0)); });
    section.appendChild(tree);
    container.appendChild(section);
  });

  // --- search filter ----------------------------------------------------
  var search = document.getElementById("search");
  search.addEventListener("input", function () {
    var q = search.value.trim().toLowerCase();
    var rows = container.querySelectorAll(".node-row");
    if (!q) {
      rows.forEach(function (r) { r.parentElement.style.display = ""; });
      container.querySelectorAll(".children").forEach(function (c) { c.style.display = ""; });
      return;
    }
    // show a deck row if it (or any descendant) matches
    rows.forEach(function (r) {
      var li = r.parentElement;
      var matchSelf = r.dataset.search.indexOf(q) !== -1;
      var matchDesc = li.querySelector(".node-row") &&
        Array.prototype.some.call(li.querySelectorAll(".node-row"), function (x) {
          return x.dataset.search.indexOf(q) !== -1;
        });
      li.style.display = (matchSelf || matchDesc) ? "" : "none";
    });
    // keep all groups expanded while searching
    container.querySelectorAll(".children").forEach(function (c) { c.style.display = ""; });
  });

  // --- expand / collapse every group at once ----------------------------
  var toggleAllBtn = document.getElementById("toggle-all");
  if (toggleAllBtn) {
    toggleAllBtn.addEventListener("click", function () {
      // collapse when currently expanded, otherwise expand everything
      var collapse = toggleAllBtn.getAttribute("aria-expanded") !== "false";
      container.querySelectorAll(".children").forEach(function (ul) {
        ul.classList.toggle("hidden", collapse);
        var row = ul.previousElementSibling; // the .node-row that owns this group
        if (row && row.classList.contains("node-row")) row.classList.toggle("collapsed", collapse);
      });
      toggleAllBtn.setAttribute("aria-expanded", collapse ? "false" : "true");
      toggleAllBtn.textContent = collapse ? "Tout déplier" : "Tout replier";
    });
  }
})();
