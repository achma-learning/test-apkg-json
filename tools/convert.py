#!/usr/bin/env python3
"""
Convert Anki .apkg decks into JSON/JS data + media for the browser flashcard app.

For each .apkg listed in COLLECTIONS:
  * unzip it (in memory / temp dir)
  * read the SQLite collection (collection.anki21 preferred, else collection.anki2)
  * read the `media` map (zip-entry-number -> real filename) and copy the images
    into media/<slug>/
  * render every card's Front/Back HTML using the note's model templates
    (a faithful subset of Anki's mustache template language)
  * rewrite <img src> + collapse empty answer wrappers
  * write one data file per deck (data/<slug>/<deckid>.js) containing only the
    cards that belong directly to that deck
  * write a global manifest (data/manifest.js) describing every collection/deck

Run from the repository root:  python3 tools/convert.py
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "apkg_src")
DATA_DIR = os.path.join(ROOT, "data")
MEDIA_DIR = os.path.join(ROOT, "media")

# (apkg file name, url-safe slug, human friendly display name)
# Source files are looked up in apkg_src/ first, then in the repo root.
COLLECTIONS = [
    ("Anettermie.apkg", "anettermie", "Anettermie"),
    ("Atlas_photographique_Dissection.apkg", "atlas", "Atlas photographique (Dissection)"),
    ("v4 medicaments_generiques_maroc (1).apkg", "maroc", "Médicaments génériques (Maroc)"),
]

FIELD_SEP = "\x1f"

# ---------------------------------------------------------------------------
# Anki template rendering (a faithful subset of the mustache-like syntax)
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"\{\{([#^/])([^}]+)\}\}")
FIELD_RE = re.compile(r"\{\{([^#^/][^}]*)\}\}")
STRIP_TAGS_RE = re.compile(r"<[^>]+>")


def strip_tags(html):
    if not html:
        return ""
    txt = STRIP_TAGS_RE.sub("", html)
    return txt.replace("&nbsp;", " ").replace("\xa0", " ")


def field_nonempty(val):
    return bool(strip_tags(val).strip())


def field_value(name, fields):
    """Resolve a field name, including Anki's special pseudo-fields."""
    if name in fields:
        return fields[name]
    return ""  # unknown field -> empty (Anki behaviour)


def render_sections(s, fields):
    """Resolve {{#Field}}..{{/Field}} and {{^Field}}..{{/Field}} blocks."""
    out = []
    i, n = 0, len(s)
    while i < n:
        m = TAG_RE.search(s, i)
        if not m:
            out.append(s[i:])
            break
        out.append(s[i:m.start()])
        typ, name = m.group(1), m.group(2).strip()
        if typ in "#^":
            # find the matching close tag for this name (respecting nesting)
            depth, j, inner, end = 1, m.end(), "", n
            while depth > 0:
                m2 = TAG_RE.search(s, j)
                if not m2:
                    inner, end = s[m.end():], n
                    break
                if m2.group(1) in "#^" and m2.group(2).strip() == name:
                    depth += 1
                elif m2.group(1) == "/" and m2.group(2).strip() == name:
                    depth -= 1
                    if depth == 0:
                        inner, end = s[m.end():m2.start()], m2.end()
                        break
                j = m2.end()
            cond = field_nonempty(field_value(name, fields))
            if typ == "^":
                cond = not cond
            if cond:
                out.append(render_sections(inner, fields))
            i = end
        else:
            # stray close tag: drop it
            i = m.end()
    return "".join(out)


def render_fields(s, fields):
    """Replace {{Field}} and {{filter:Field}} references."""
    def repl(m):
        spec = m.group(1).strip()
        if ":" in spec:
            parts = spec.split(":")
            fname = parts[-1].strip()
            filt = parts[0].strip()
            val = field_value(fname, fields)
            if filt == "hint":
                if not field_nonempty(val):
                    return ""
                return (
                    '<a class="hint-link" href="#" '
                    'onclick="var c=this.nextElementSibling;'
                    "c.style.display=c.style.display==='none'?'inline':'none';"
                    'return false;">{label}</a>'
                    '<span class="hint-content" style="display:none">{val}</span>'
                ).format(label="Afficher %s" % fname, val=val)
            if filt == "text":
                return strip_tags(val)
            # cloze / type / furigana / kana / kanji not used by these decks
            return val
        return field_value(spec, fields)

    return FIELD_RE.sub(repl, s)


def render(template, fields):
    return render_fields(render_sections(template, fields), fields)


# remove answer wrappers that ended up empty (e.g. missing Clinique/Commentaire)
EMPTY_WRAP_RE = re.compile(
    r'<span id=["\']?(clinic|comment)["\']?>(.*?)</span>', re.S | re.I
)


def cleanup(html):
    def repl(m):
        return "" if not strip_tags(m.group(2)).strip() else m.group(0)

    html = EMPTY_WRAP_RE.sub(repl, html)
    # collapse runs of empty separators / whitespace left behind
    html = re.sub(r"(?:\s*<hr\s*/?>\s*){2,}", "<hr>", html)
    html = re.sub(r"(?:\s*<br\s*/?>\s*){3,}", "<br><br>", html)
    return html.strip()


# rewrite <img src="paste-xxx.jpg"> -> media/<slug>/paste-xxx.jpg
IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\']?)([^"\'>\s]+)\2', re.I)


def rewrite_media(html, slug):
    def repl(m):
        src = m.group(3)
        if src.startswith(("http://", "https://", "data:", "media/")):
            return m.group(0)
        base = os.path.basename(src)
        return "%s%smedia/%s/%s%s" % (m.group(1), m.group(2), slug, base, m.group(2))

    return IMG_SRC_RE.sub(repl, html)


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def subdeck_leaf(name):
    return name.split("::")[-1]


def convert_collection(apkg, slug, display):
    print("\n=== %s (%s) ===" % (display, slug))
    work = os.path.join("/tmp", "apkgwork_" + slug)
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    src = os.path.join(SRC_DIR, apkg)
    if not os.path.exists(src):
        src = os.path.join(ROOT, apkg)  # fall back to a package kept in the repo
    with zipfile.ZipFile(src) as z:
        z.extractall(work)

    db_path = os.path.join(work, "collection.anki21")
    if not os.path.exists(db_path):
        db_path = os.path.join(work, "collection.anki2")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    models = json.loads(cur.execute("SELECT models FROM col").fetchone()[0])
    decks = json.loads(cur.execute("SELECT decks FROM col").fetchone()[0])

    # --- copy media ---------------------------------------------------------
    media_out = os.path.join(MEDIA_DIR, slug)
    os.makedirs(media_out, exist_ok=True)
    shared_out = os.path.join(MEDIA_DIR, "_shared")
    os.makedirs(shared_out, exist_ok=True)
    media_map = {}
    mm_path = os.path.join(work, "media")
    if os.path.exists(mm_path):
        media_map = json.load(open(mm_path, encoding="utf-8"))
    copied = 0
    for num, fname in media_map.items():
        src = os.path.join(work, num)
        if not os.path.exists(src):
            continue
        if fname.startswith("_"):
            # shared asset (background texture etc.)
            dst = os.path.join(shared_out, fname)
        else:
            dst = os.path.join(media_out, fname)
        shutil.copyfile(src, dst)
        copied += 1
    print("  media files copied: %d" % copied)

    # --- render every card --------------------------------------------------
    notes = {}
    for nid, mid, flds, tags in cur.execute("SELECT id, mid, flds, tags FROM notes"):
        notes[nid] = (mid, flds.split(FIELD_SEP), tags)

    # cards grouped per deck, ordered by (note id, template ord)
    deck_cards = {}
    rows = cur.execute("SELECT nid, did, ord FROM cards ORDER BY nid, ord").fetchall()
    rendered = 0
    for nid, did, ord_ in rows:
        if nid not in notes:
            continue
        mid, fvals, tags = notes[nid]
        model = models[str(mid)]
        tmpls = model["tmpls"]
        if ord_ >= len(tmpls):
            continue
        tmpl = tmpls[ord_]
        deck_name = decks.get(str(did), {}).get("name", "Default")

        fields = {f["name"]: (fvals[i] if i < len(fvals) else "")
                  for i, f in enumerate(model["flds"])}
        # special pseudo-fields — only when a real field doesn't shadow them
        # (Anki gives precedence to a note's own field, e.g. a field named "Type")
        for key, val in (("Tags", tags.strip()), ("Type", model["name"]),
                         ("Deck", deck_name), ("Subdeck", subdeck_leaf(deck_name)),
                         ("Card", tmpl["name"])):
            fields.setdefault(key, val)

        front = render(tmpl["qfmt"], fields)
        fields["FrontSide"] = front
        back = render(tmpl["afmt"], fields)

        front = rewrite_media(cleanup(front), slug)
        back = rewrite_media(cleanup(back), slug)
        deck_cards.setdefault(did, []).append([front, back])
        rendered += 1
    print("  cards rendered: %d" % rendered)

    # --- write per-deck data files -----------------------------------------
    out_dir = os.path.join(DATA_DIR, slug)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    own_count = {}
    for did, cards in deck_cards.items():
        own_count[did] = len(cards)
        payload = {"n": decks[str(did)]["name"], "c": cards}
        with open(os.path.join(out_dir, "%d.js" % did), "w", encoding="utf-8") as fh:
            fh.write("DECKDATA[%d]=%s;" % (did, json.dumps(payload, ensure_ascii=False)))

    # --- build deck list (with own + total-including-subdecks counts) -------
    # Leaves = decks that actually hold cards. Some packages only define those
    # leaf decks, so we also synthesize every implicit parent so the tree nests.
    id_by_name = {d["name"]: int(did) for did, d in decks.items()}
    leaves = {decks[str(did)]["name"]: cnt for did, cnt in own_count.items()}
    node_names = set()
    for name in leaves:
        if name == "Default":
            continue
        parts = name.split("::")
        for i in range(1, len(parts) + 1):
            node_names.add("::".join(parts[:i]))

    deck_list = []
    for name in node_names:
        own = leaves.get(name, 0)
        prefix = name + "::"
        total = own + sum(c for n, c in leaves.items()
                          if n != name and n.startswith(prefix))
        if total == 0:
            continue
        if name in id_by_name:
            did = id_by_name[name]                      # real deck id
        else:
            did = "g" + hashlib.md5((slug + "::" + name).encode()).hexdigest()[:12]
        deck_list.append({"id": did, "name": name, "own": own, "total": total})
    deck_list.sort(key=lambda x: x["name"])
    print("  decks with cards: %d" % len([d for d in deck_list if d["own"] > 0]))

    con.close()
    return {"slug": slug, "name": display, "decks": deck_list,
            "cards": rendered}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    manifest = {"collections": []}
    for apkg, slug, display in COLLECTIONS:
        manifest["collections"].append(convert_collection(apkg, slug, display))
    with open(os.path.join(DATA_DIR, "manifest.js"), "w", encoding="utf-8") as fh:
        fh.write("window.MANIFEST=" + json.dumps(manifest, ensure_ascii=False) + ";")
    total_cards = sum(c["cards"] for c in manifest["collections"])
    total_decks = sum(len([d for d in c["decks"] if d["own"] > 0])
                      for c in manifest["collections"])
    print("\nDONE: %d collections, %d decks, %d cards"
          % (len(manifest["collections"]), total_decks, total_cards))


if __name__ == "__main__":
    main()
