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
import html
import json
import os
import re
import shutil
import sqlite3
import urllib.parse
import zipfile

try:
    from PIL import Image
except ImportError:  # optimisation is best-effort; convert still works without it
    Image = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "apkg_src")
DATA_DIR = os.path.join(ROOT, "data")
MEDIA_DIR = os.path.join(ROOT, "media")

# (apkg file name, url-safe slug, human friendly display name)
# Source files are looked up in apkg_src/ first, then in the repo root.
COLLECTIONS = [
    # ── original three decks ────────────────────────────────────────────────
    ("Anettermie.apkg", "anettermie", "Anettermie"),
    ("Atlas_photographique_Dissection.apkg", "atlas", "Atlas photographique (Dissection)"),
    ("v4 medicaments_generiques_maroc (1).apkg", "maroc", "Médicaments génériques (Maroc)"),
    # ── decks added from the Anki Decks Store (achma-learning.github.io/anki-decks) ──
    ("plaques_marocaines.apkg", "plaques", "Plaques Marocaines (Codes Régionaux)"),
    ("Fetes_Maroc_Moroccan_Holidays.apkg", "fetes", "Fêtes du Maroc — Moroccan Holidays"),
    ("pharmaco-dci-suffixes-v1.apkg", "dci-suffixes", "Pharmaco — Suffixes & Préfixes DCI"),
    ("Kit_autoformation_soins_intensifs.apkg", "soins-intensifs", "Kit d'autoformation en Soins Intensifs"),
    ("rx_thorax_tuberculose.apkg", "rx-thorax", "Rx Thorax — Tuberculose (FMPM)"),
    ("Radiologie_Thoracique_Chest_Xray_Radiologues.apkg", "chest-xray", "Chest X-Ray — Radiologie Thoracique (Dr HOURI)"),
    ("constantes_biologiques.apkg", "constantes", "Constantes Biologiques Usuelles"),
    ("auscultation_cardiaque.apkg", "auscultation-cardiaque", "Auscultation Cardiaque"),
    ("auscultation_pulmonaire.apkg", "auscultation-pulmonaire", "Auscultation Pulmonaire"),
    ("iconographie_medicale.apkg", "iconographie", "Iconographie Médicale (Anatomopathologie)"),
    ("neurosciences.apkg", "neurosciences", "Neurosciences (Neuroanatomie)"),
    ("physiologie.apkg", "physiologie", "Physiologie"),
    ("ecg_complet.apkg", "ecg", "ECG — Le deck complet"),
    ("cas_cliniques_ecg.apkg", "ecg-cas", "Cas Cliniques d'ECG"),
    ("histologie.apkg", "histologie", "Histologie"),
]

FIELD_SEP = "\x1f"

# Raster images bigger than this (in bytes) are downscaled / re-encoded for the
# web; everything smaller is copied verbatim so already-light decks are untouched.
IMG_SIZE_CAP = 450 * 1024
IMG_DIM_CAP = 1600
RASTER_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".ogv")

# ---------------------------------------------------------------------------
# Anki template rendering (a faithful subset of the mustache-like syntax)
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"\{\{([#^/])([^}]+)\}\}")
FIELD_RE = re.compile(r"\{\{([^#^/][^}]*)\}\}")
STRIP_TAGS_RE = re.compile(r"<[^>]+>")
CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)\}\}", re.S)
CLOZE_NUM_RE = re.compile(r"\{\{c(\d+)::")


def render_cloze(text, ctx):
    """Render Anki cloze deletions for one card.

    ``ctx`` carries the active cloze number (``num``) and which side we are on
    (``q`` = question, ``a`` = answer). The active cloze is hidden as ``[...]``
    (or ``[hint]``) on the question and revealed/highlighted on the answer;
    every other cloze is always shown as plain text.
    """
    active, side = ctx["num"], ctx["side"]

    def repl(m):
        num = int(m.group(1))
        answer, _, hint = m.group(2).partition("::")
        if num == active:
            if side == "q":
                return '<span class="cloze">[%s]</span>' % (hint.strip() or "...")
            return '<span class="cloze">%s</span>' % answer
        return answer

    return CLOZE_RE.sub(repl, text)


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


def render_fields(s, fields, ctx=None):
    """Replace {{Field}} and {{filter:Field}} references."""
    def repl(m):
        spec = m.group(1).strip()
        if ":" in spec:
            parts = spec.split(":")
            fname = parts[-1].strip()
            filt = parts[0].strip()
            val = field_value(fname, fields)
            if filt == "cloze":
                return render_cloze(val, ctx) if ctx else val
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
            # type / furigana / kana / kanji not used by these decks
            return val
        return field_value(spec, fields)

    return FIELD_RE.sub(repl, s)


def render(template, fields, ctx=None):
    return render_fields(render_sections(template, fields), fields, ctx)


# remove answer wrappers that ended up empty (e.g. missing Clinique/Commentaire)
EMPTY_WRAP_RE = re.compile(
    r'<span id=["\']?(clinic|comment)["\']?>(.*?)</span>', re.S | re.I
)
SCRIPT_RE = re.compile(r"(?is)<script\b.*?</script>")
COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")


def media_url(slug, name, renames):
    """media/<folder>/<url-encoded name>, honouring optimisation renames and
    routing Anki's shared (``_``-prefixed) assets to media/_shared/."""
    # field references HTML-escape filenames (e.g. "a &amp; b.mp3"); the real
    # file on disk uses the literal character, so unescape before resolving.
    name = html.unescape(name)
    out = renames.get(name, name)
    folder = "_shared" if out.startswith("_") else slug
    return "media/%s/%s" % (folder, urllib.parse.quote(out))


def media_element(slug, name, renames):
    """Turn a [sound:…] reference into a playable <audio>/<video> element."""
    url = media_url(slug, name, renames)
    ext = os.path.splitext(name)[1].lower()
    if ext in VIDEO_EXTS:
        return ('<video controls preload="metadata" src="%s" '
                'style="max-width:100%%"></video>') % url
    return '<audio controls preload="none" src="%s"></audio>' % url


def cleanup(html, slug="", renames=None):
    renames = renames or {}

    def repl(m):
        return "" if not strip_tags(m.group(2)).strip() else m.group(0)

    # drop template scripting / comments that don't belong in a static viewer
    html = SCRIPT_RE.sub("", html)
    html = COMMENT_RE.sub("", html)
    # play audio / video that Anki stores as [sound:file]
    html = SOUND_RE.sub(
        lambda m: media_element(slug, os.path.basename(m.group(1).strip()), renames),
        html,
    )
    html = EMPTY_WRAP_RE.sub(repl, html)
    # collapse runs of empty separators / whitespace left behind
    html = re.sub(r"(?:\s*<hr\s*/?>\s*){2,}", "<hr>", html)
    html = re.sub(r"(?:\s*<br\s*/?>\s*){3,}", "<br><br>", html)
    return html.strip()


# rewrite <img src="paste-xxx.jpg"> -> media/<slug>/paste-xxx.jpg
# (handles "double", 'single' and bare src values, incl. filenames with spaces)
IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*?\bsrc\s*=\s*)("[^"]*"|\'[^\']*\'|[^\s>]+)', re.I
)


def rewrite_media(html, slug, renames=None):
    renames = renames or {}

    def repl(m):
        prefix, token = m.group(1), m.group(2)
        if token[:1] in ("\"", "'"):
            quote, src = token[0], token[1:-1]
        else:
            quote, src = "", token
        if src.startswith(("http://", "https://", "data:", "media/")):
            return m.group(0)
        base = os.path.basename(src)
        return "%s%s%s%s" % (prefix, quote, media_url(slug, base, renames), quote)

    return IMG_SRC_RE.sub(repl, html)


# raster image optimisation -------------------------------------------------

def process_media(src, out_dir, fname):
    """Copy `fname` into out_dir, downscaling / re-encoding heavy raster images
    so the static site stays light. Returns the actual output filename (a big
    opaque PNG may come out as .jpg). Small files are copied byte-for-byte."""
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(fname)[1].lower()
    try:
        size = os.path.getsize(src)
    except OSError:
        return fname

    if Image is not None and ext in RASTER_EXTS and size > IMG_SIZE_CAP:
        try:
            im = Image.open(src)
            im.load()
            w, h = im.size
            has_alpha = im.mode in ("RGBA", "LA") or (
                im.mode == "P" and "transparency" in im.info)
            if has_alpha:
                has_alpha = im.convert("RGBA").getchannel("A").getextrema()[0] < 255
            scale = min(1.0, IMG_DIM_CAP / float(max(w, h)))
            if scale < 1.0:
                im = im.resize(
                    (max(1, round(w * scale)), max(1, round(h * scale))),
                    Image.LANCZOS,
                )
            if has_alpha:                       # keep transparency -> optimised PNG
                out_name = fname
                dst = os.path.join(out_dir, out_name)
                im.convert("RGBA").save(dst, "PNG", optimize=True)
            else:                               # flatten -> much lighter JPEG
                out_name = os.path.splitext(fname)[0] + ".jpg"
                dst = os.path.join(out_dir, out_name)
                im.convert("RGB").save(
                    dst, "JPEG", quality=85, optimize=True, progressive=True)
            if os.path.getsize(dst) < size:
                return out_name
            if out_name != fname:               # optimisation didn't help -> revert
                os.remove(dst)
        except Exception as exc:                # corrupt / exotic image -> just copy
            print("    [warn] could not optimise %s: %s" % (fname, exc))

    shutil.copyfile(src, os.path.join(out_dir, fname))
    return fname


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
    renames = {}  # original filename -> optimised filename (when re-encoded)
    copied = 0
    for num, fname in media_map.items():
        src = os.path.join(work, num)
        if not os.path.exists(src):
            continue
        # shared assets (background texture, shared icons…) live in media/_shared
        dest_dir = shared_out if fname.startswith("_") else media_out
        out_name = process_media(src, dest_dir, fname)
        if out_name != fname:
            renames[fname] = out_name
        copied += 1
    print("  media files copied: %d (re-encoded: %d)" % (copied, len(renames)))

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
        is_cloze = model.get("type") == 1
        # A cloze note emits one card per deletion (ord_ = 0,1,2…) but they all
        # share the single cloze template; ordinary notes map ord_ -> template.
        if is_cloze:
            tmpl = tmpls[0]
        elif ord_ < len(tmpls):
            tmpl = tmpls[ord_]
        else:
            continue
        deck_name = decks.get(str(did), {}).get("name", "Default")

        fields = {f["name"]: (fvals[i] if i < len(fvals) else "")
                  for i, f in enumerate(model["flds"])}
        # special pseudo-fields — only when a real field doesn't shadow them
        # (Anki gives precedence to a note's own field, e.g. a field named "Type")
        for key, val in (("Tags", tags.strip()), ("Type", model["name"]),
                         ("Deck", deck_name), ("Subdeck", subdeck_leaf(deck_name)),
                         ("Card", tmpl["name"])):
            fields.setdefault(key, val)

        # cloze note types: work out which deletion this card (ord_) reveals
        ctx_q = ctx_a = None
        if is_cloze:
            cloze_text = ""
            for fdef in model["flds"]:
                val = fields.get(fdef["name"], "")
                if CLOZE_NUM_RE.search(val or ""):
                    cloze_text = val
                    break
            nums = sorted({int(x) for x in CLOZE_NUM_RE.findall(cloze_text)})
            active = nums[ord_] if ord_ < len(nums) else (nums[0] if nums else 1)
            ctx_q = {"num": active, "side": "q"}
            ctx_a = {"num": active, "side": "a"}

        front = render(tmpl["qfmt"], fields, ctx_q)
        fields["FrontSide"] = front
        back = render(tmpl["afmt"], fields, ctx_a)

        front = rewrite_media(cleanup(front, slug, renames), slug, renames)
        back = rewrite_media(cleanup(back, slug, renames), slug, renames)
        deck_cards.setdefault(did, []).append([front, back])
        rendered += 1
    print("  cards rendered: %d" % rendered)

    # --- drop media that no rendered card actually references ----------------
    # (some packages bundle hundreds of unused images). Only this collection's
    # private folder is pruned; the cross-collection media/_shared is left alone.
    used = set()
    ref_re = re.compile(r'(?:src|href)="media/%s/([^"]+)"' % re.escape(slug))
    for cards in deck_cards.values():
        for front, back in cards:
            for name in ref_re.findall(front) + ref_re.findall(back):
                used.add(urllib.parse.unquote(name))
    pruned = 0
    if os.path.isdir(media_out):
        for fname in os.listdir(media_out):
            if fname not in used:
                os.remove(os.path.join(media_out, fname))
                pruned += 1
    if pruned:
        print("  unused media pruned: %d" % pruned)

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


def _load_manifest():
    """Read the existing manifest so decks whose .apkg isn't available locally
    (the git-ignored Anettermie/Atlas packages) keep their generated output."""
    path = os.path.join(DATA_DIR, "manifest.js")
    if not os.path.exists(path):
        return {"collections": []}
    try:
        txt = open(path, encoding="utf-8").read()
        return json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except Exception as exc:
        print("  [warn] could not parse existing manifest (%s); rebuilding" % exc)
        return {"collections": []}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    manifest = _load_manifest()
    index = {c["slug"]: i for i, c in enumerate(manifest["collections"])}

    for apkg, slug, display in COLLECTIONS:
        src = os.path.join(SRC_DIR, apkg)
        if not os.path.exists(src):
            src = os.path.join(ROOT, apkg)
        if not os.path.exists(src):
            print("\n=== %s (%s) ===\n  SKIPPED: %s not found (keeping existing data)"
                  % (display, slug, apkg))
            continue
        entry = convert_collection(apkg, slug, display)
        if slug in index:
            manifest["collections"][index[slug]] = entry
        else:
            index[slug] = len(manifest["collections"])
            manifest["collections"].append(entry)

    with open(os.path.join(DATA_DIR, "manifest.js"), "w", encoding="utf-8") as fh:
        fh.write("window.MANIFEST=" + json.dumps(manifest, ensure_ascii=False) + ";")
    total_cards = sum(c["cards"] for c in manifest["collections"])
    total_decks = sum(len([d for d in c["decks"] if d["own"] > 0])
                      for c in manifest["collections"])
    print("\nDONE: %d collections, %d decks, %d cards"
          % (len(manifest["collections"]), total_decks, total_cards))


if __name__ == "__main__":
    main()
