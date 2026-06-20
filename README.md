# FlashAnat — Anki decks as a browser flashcard site

A small **Anki-style flashcard web app** built from Anki `.apkg` packages.
Open `index.html`, pick a chapter, and review the cards (with images,
questions and answers) right in the browser — no Anki needed.

The source decks (mostly in French) come from the
[Anki Decks Store](https://achma-learning.github.io/anki-decks/):

| Deck | Cards | Type |
|------|-------|------|
| **Anettermie** | 3 436 | Image-occlusion anatomy plates (Netter) |
| **Atlas photographique (Dissection)** | 1 585 | Image occlusion + clinical Q/A |
| **Médicaments génériques (Maroc)** | 2 067 | Brand↔DCI recognition / substitution + ANSM drug interactions |
| **Neurosciences (Neuroanatomie)** | 2 604 | Labelled CNS/PNS plates |
| **Histologie** | 1 316 | Tissue/organ histology plates |
| **Constantes Biologiques Usuelles** | 920 | Reference lab values Q/A |
| **Physiologie** | 822 | System physiology (plates + occlusion) |
| **ECG — Le deck complet** | 586 | ECG cloze + image cards |
| **Kit Soins Intensifs** | 433 | AnKing-style cloze (ICU e-learning) |
| **Iconographie Médicale** | 368 | Anatomopathology images |
| **Chest X-Ray (Dr HOURI)** | 119 | Thoracic radiology Q/A |
| **Plaques Marocaines** | 89 | Moroccan plate region codes |
| **Rx Thorax — Tuberculose (FMPM)** | 64 | TB radiology images |
| **Pharmaco — Suffixes & Préfixes DCI** | 62 | DCI stem → drug class |
| **Cas Cliniques d'ECG** | 62 | ECG case images |
| **Auscultation Pulmonaire** | 37 | **Audio** lung-sound cards |
| **Fêtes du Maroc** | 30 | Trilingual holidays |
| **Auscultation Cardiaque** | 23 | **Audio** heart-sound cloze cards |
| **Cathéters Veineux (Osmosis)** | 15 | IV catheter gauge ↔ couleur ↔ débit reference + cheatsheet |

→ **19 collections · ~14 640 cards · 357 chapters**, with images, audio and cloze.

## How it works

Each `.apkg` is just a ZIP holding a SQLite database (`collection.anki21`),
the card templates/CSS, and numbered media files. The converter
(`tools/convert.py`) does the whole pipeline:

1. unzips the package and reads the Anki SQLite collection;
2. copies every image into `media/<deck>/` (using its real filename),
   **downscaling/re-encoding heavy images** for the web (big opaque PNGs become
   JPEGs) and **pruning media no card references**;
3. renders each card's **Front/Back HTML** with a faithful subset of Anki's
   mustache template language (`{{Field}}`, `{{#Field}}…{{/Field}}`,
   `{{^Field}}`, `{{FrontSide}}`, `{{hint:…}}`, `{{cloze:…}}`, special fields
   like `{{Subdeck}}`), turns `[sound:…]` into `<audio>`/`<video>` players,
   strips template `<script>`, rewrites `<img>` paths (URL-encoding filenames
   with spaces) and trims empty answer blocks;
4. writes one data file per deck to `data/<deck>/<deckId>.js`;
5. merges a global `data/manifest.js` describing every collection/deck tree
   (decks whose `.apkg` isn't present locally keep their already-generated data).

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

Put the `.apkg` files where the converter looks for them (each entry in
`COLLECTIONS` is searched first in `apkg_src/`, then in the repo root — the
Anettermie/Atlas packages are git-ignored under `apkg_src/`, while the Morocco
package is committed at the repo root) and run:

```bash
pip install Pillow      # optional, for image optimisation
python3 tools/convert.py
```

The run is **incremental/merge-based**: any deck whose `.apkg` is missing keeps
its committed `data/`+`media/`, so you can regenerate just the decks you have
locally. To pull every source deck from the store, download the `.apkg` files
(GitHub raw + Google Drive links) into `apkg_src/` — see the `COLLECTIONS` list
in `tools/convert.py` for the expected filenames.
