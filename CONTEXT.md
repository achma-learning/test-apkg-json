# FlashAnat — AI Context File
_Last synced: 2026-06-15 @ e049acf_

## 1. What This Is (Plain English)
- **In one sentence:** A website that turns Anki flashcard packs (`.apkg` files) into a study site you open in a browser — pick a chapter, flip cards, grade yourself — no Anki app needed.
- **Why it exists:** To study medical decks (anatomy, radiology, pharmacology, ECG, histology, heart/lung sounds…) on any device straight from a web link, and to have one repeatable converter that ingests new `.apkg` decks from the companion [Anki Decks Store](https://achma-learning.github.io/anki-decks/).
- **Who uses it:** Mostly the owner + fellow medical students; it's public on GitHub Pages, so treat content as shippable but low-stakes.
- **Vibe:** Polished personal tool. Plain static site, no framework, no build step — deliberately simple so it "just works" from a file or a free host.

## 2. How To Run It
- **Setup once:** Nothing to install to *view* the site. To *regenerate* data with image optimisation: `pip install Pillow` (optional — converter still runs without it).
- **Run dev (view the site):**
  ```bash
  python3 -m http.server 8000
  # open http://localhost:8000/
  ```
  It also works by double-clicking `index.html` (data loads via `<script>` tags, not `fetch`, so there are no `file://` CORS issues).
- **Regenerate the card data** (after adding/replacing a `.apkg`):
  ```bash
  python3 tools/convert.py
  ```
- **Build / deploy:** No build. It's a static site — push to the default branch and GitHub Pages serves it as-is.
- **Required env vars:** None. No `.env.example`, no secrets, no runtime services.

## 3. Tech Stack
- **Language + runtime:** Front end is plain HTML/CSS + vanilla ES5-ish JS (no modules, no framework). Converter is **Python 3 standard library** (`zipfile`, `sqlite3`, `json`, `re`, `urllib`, `hashlib`). Version not pinned — no `.python-version`/`pyproject.toml`.
- **Key libraries:** **Pillow** (optional, image optimisation) — guarded import, `Image = None` if missing (tools/convert.py:11). That's the *only* third-party dependency, and it's optional.
- **What kind of project:** Static web app + a one-file data pipeline. Zero npm, zero lockfiles, no bundler.
- **External services:** None at runtime. Source `.apkg` files are downloaded **by hand** (GitHub raw + Google Drive links from the deck store) into `apkg_src/`; nothing fetches them automatically.

## 4. Code Map (The Important Files Only)
- `tools/convert.py` — **the engine.** Unzips each `.apkg`, reads its SQLite collection, copies/optimises media, renders every card's front/back HTML, writes per-deck data + a global manifest. Open this first if anything about the data looks wrong.
- `index.html` — home page. Loads `data/manifest.js` then `assets/home.js`; renders the searchable deck tree.
- `deck.html` — study page. Loads `data/manifest.js` then `assets/study.js`; URL is `deck.html?col=<slug>&deck=<deckId>`. The card HTML is injected into `#side` (deck.html:40).
- `assets/home.js` — builds the collapsible collection/deck tree from the manifest. Fully data-driven (scales to any number of decks).
- `assets/study.js` — the study session: load deck + subdecks, flip, grade (À revoir / Acquis), shuffle, hide-known, progress saved in `localStorage` (key `flashanat:known:<slug>`). Renders card HTML via `innerHTML`, so audio/video/cloze markup all work.
- `assets/cards.css` — hand-written reproduction of the decks' card styling (scoped per collection via `.col-<slug>`). `assets/app.css` — site chrome (dark theme).
- `data/manifest.js` — generated. `window.MANIFEST = {collections:[{slug,name,decks:[{id,name,own,total}],cards}]}` (tools/convert.py:531).
- `data/<slug>/<deckId>.js` — generated, one per deck. `DECKDATA[<id>] = {n:"<deck name>", c:[[frontHTML, backHTML], …]}` (tools/convert.py:459).
- `media/<slug>/…` + `media/_shared/…` — generated images/audio/video. `_`-prefixed (shared) assets go to `_shared`.
- `apkg_src/` — source `.apkg` files. **Git-ignored** (.gitignore:2). The Maroc pack is the exception, committed at repo root as `v4 medicaments_generiques_maroc (1).apkg`.

## 5. Rules For Editing This Code
- **Stay zero-dependency on the front end.** Vanilla JS only — no React/Vue, no bundler, no npm. The site must keep opening straight from `file://`.
- **Converter uses the Python standard library only.** The single allowed extra is Pillow, and it must stay an optional guarded import (don't make it required).
- **Data files are JS, not JSON.** Keep emitting `DECKDATA[id]=…;` and `window.MANIFEST=…;` so they load via `<script>` (this is what avoids CORS on `file://`). Don't switch to `fetch`/JSON without a reason.
- **Don't disturb the original three decks.** `anettermie`, `atlas`, `maroc` — their `.apkg` sources aren't in the repo (except Maroc at root). The converter is merge-based and *skips* decks whose source is missing (tools/convert.py:509 `main`). Conversion changes must stay byte-identical for these decks (verified by `git status` showing 0 changed files under their `data/`+`media/`).
- **Media URLs must be URL-encoded.** Filenames contain spaces, commas, `&`. `media_url` HTML-unescapes then `urllib.parse.quote`s names (tools/convert.py:207). Never assume clean filenames.
- **Add a new deck = add a tuple to `COLLECTIONS`** (`(apkg_filename, slug, display_name)`, tools/convert.py:42), drop the `.apkg` in `apkg_src/`, run `convert.py`. Slugs become folder names and CSS hooks (`.col-<slug>`).
- **Card HTML is trusted-but-cleaned.** `cleanup()` strips `<script>`/comments at convert time (tools/convert.py:228); the front end then injects it raw. Keep stripping scripts — don't render untrusted decks without it.

## 6. Fragile Bits & Landmines
- **Image-optimisation thresholds are load-bearing.** `IMG_SIZE_CAP = 450KB`, `IMG_DIM_CAP = 1600` (tools/convert.py:69-70) are tuned so the original decks' images (max ~351KB / 1543px) fall *below* the cap and are copied byte-for-byte. **Lowering these will rewrite/alter the committed Anettermie/Atlas media** and blow up the diff. Opaque PNGs above the cap are re-encoded to `.jpg` and the `<img src>` is rewritten via a per-deck `renames` map.
- **`rewrite_media` must preserve exact output for existing decks** (tools/convert.py:256). `quote()` is a no-op on already-safe names like `paste-abc.jpg`, so existing output stays identical. If you change this regex, re-verify with `git status` that `data/anettermie` etc. don't change.
- **Cloze card counting.** Cloze note types have one template but emit one card per deletion (ord 0,1,2…). The loop must use `tmpls[0]` for cloze and *not* skip `ord_ >= len(tmpls)` (tools/convert.py inside `convert_collection`). Active deletion = the ord-th distinct cloze number. Getting this wrong silently drops cards (it once produced 321/433 for the Kit deck).
- **`.editcloze { display:none }`** (assets/cards.css) hides the AnKing cloze note's *editable duplicate* of the answer (the template renders `{{cloze:Text}}` twice). Don't delete this rule or the Kit Soins Intensifs answers show twice.
- **`{{edit:Field}}` must pass through as the field value**, not be blanked — the "Project Europa" decks (constantes, iconographie, auscultation…) use it for normal answer display. Only AnKing abuses it for the hidden duplicate.
- **Orphan-media pruning** deletes files in `media/<slug>/` that no rendered card references (tools/convert.py, after the render loop). It deliberately never touches `media/_shared/` (shared across decks). Some packs bundle hundreds of unused images (constantes shed 277).
- **One known dangling link:** `media/rx-thorax/rc57.html` — the source deck mis-pasted an HTML path as `<img src>`. Cosmetic (shows its `alt`), not worth "fixing".
- **Big repo.** Committed `media/` is ~322MB; with git-ignored `apkg_src/` the working tree is ~950MB. Don't `git add` the `.apkg` files (they're ignored on purpose).

## 7. Current State
- **Last shipped:** PR #3 — added 15 new decks from the Anki Decks Store (GitHub + Google Drive sources) → now **18 collections / 14,623 cards / 356 chapters**. Extended the converter with cloze rendering, `[sound:]`→`<audio>/<video>`, web image optimisation, orphan-media pruning, and a merge-based manifest. Verified all decks render in a headless browser with 0 console errors.
- **Working on now:** PR #3 review/merge into `main`.
- **Next up:** _Not yet figured out_ — possible follow-ups: per-deck CSS fidelity for decks with custom styling; trimming the heaviest media (Iconographie ~70MB, Histologie ~46MB) if repo size matters.

## 8. Update Protocol (Verbatim)
> **For the AI Assistant:** When asked to "Update CONTEXT.md":
> 1. Re-run Phase 0 — check for new `GEMINI.md` / `CLAUDE.md` / `.github/` files.
> 2. Re-scan the tree, manifests, and `.github/workflows/` for drift.
> 3. Read our recent conversation for new decisions, fragile bits discovered, or shifted goals.
> 4. Refresh the `_Last synced_` line with today's date and current commit SHA.
> 5. Rewrite — do not append. One clean source of truth. Preserve still-true content, revise the rest.
> 6. Keep §1 and §2 in plain English. Keep the file under ~350 lines.
