# FlashAnat — Anki decks as a browser flashcard site

A small **Anki-style flashcard web app** built from two Anki `.apkg` packages.
Open `index.html`, pick a chapter, and review the cards (with images,
questions and answers) right in the browser — no Anki needed.

The two source decks (anatomy, in French):

| Deck | Cards | Type |
|------|-------|------|
| **Anettermie** | 3 436 | Image-occlusion anatomy plates (Netter) |
| **Atlas photographique (Dissection)** | 1 585 | Image occlusion + clinical Q/A |

→ **5 021 cards** across **49 chapters**, with **579 images**.

## How it works

Each `.apkg` is just a ZIP holding a SQLite database (`collection.anki21`),
the card templates/CSS, and numbered media files. The converter
(`tools/convert.py`) does the whole pipeline:

1. unzips the package and reads the Anki SQLite collection;
2. copies every image into `media/<deck>/` (using its real filename);
3. renders each card's **Front/Back HTML** with a faithful subset of Anki's
   mustache template language (`{{Field}}`, `{{#Field}}…{{/Field}}`,
   `{{^Field}}`, `{{FrontSide}}`, `{{hint:…}}`, special fields like
   `{{Subdeck}}`), rewrites `<img>` paths and trims empty answer blocks;
4. writes one data file per deck to `data/<deck>/<deckId>.js`;
5. writes a global `data/manifest.js` describing every collection/deck tree.

The front-end is plain HTML/CSS/JS (no build step, no framework):

```
index.html          home page — searchable, collapsible deck tree
deck.html           study page — ?col=<slug>&deck=<id>
assets/app.css      site chrome (dark theme)
assets/cards.css    faithful rebuild of the Anki note-type styling
assets/home.js      builds the deck tree from the manifest
assets/study.js     study session: flip, grade, shuffle, progress
data/               generated JSON-in-JS data (manifest + per-deck files)
media/              images (per collection) + shared background
tools/convert.py    the .apkg → data/media converter
```

## Studying

- **A page per deck**: every deck has its own URL,
  e.g. `deck.html?col=anettermie&deck=1614548807557`.
  Opening a parent chapter automatically includes all of its sub-decks.
- Flip with **Espace**, grade **À revoir** (`1`) / **Acquis** (`2`),
  navigate with **← →**.
- **Mélanger** (shuffle), **Cacher acquises** (hide learned), and
  **Recommencer**. Progress (“acquired” cards) is saved in `localStorage`.
- Click any image to open it full-screen.

## Running locally

It's a static site. The simplest way:

```bash
python3 -m http.server 8000
# then open http://localhost:8000/
```

It also works by opening `index.html` directly from disk (the data is loaded
via `<script>` tags rather than `fetch`, so there are no CORS issues), and it
deploys as-is to GitHub Pages.

## Regenerating the data

Place the two `.apkg` files in `apkg_src/` (they are git-ignored to keep the
repo small) and run:

```bash
python3 tools/convert.py
```
