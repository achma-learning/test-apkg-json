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
COLLECTIONS = [
    ("Anettermie.apkg", "anettermie", "Anettermie"),
    ("Atlas_photographique_Dissection.apkg", "atlas", "Atlas photographique (Dissection)"),
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
    with zipfile.ZipFile(os.path.join(SRC_DIR, apkg)) as z:
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
        # special pseudo-fields
        fields["Tags"] = tags.strip()
        fields["Type"] = model["name"]
        fields["Deck"] = deck_name
        fields["Subdeck"] = subdeck_leaf(deck_name)
        fields["Card"] = tmpl["name"]

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
    deck_list = []
    name_by_id = {int(did): d["name"] for did, d in decks.items()}
    for did, d in decks.items():
        did = int(did)
        name = d["name"]
        if name == "Default" and own_count.get(did, 0) == 0:
            continue
        own = own_count.get(did, 0)
        # total = own + all descendants (deck names prefixed with "name::")
        total = own
        prefix = name + "::"
        for other_id, other_name in name_by_id.items():
            if other_name.startswith(prefix):
                total += own_count.get(other_id, 0)
        if total == 0:
            continue
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
