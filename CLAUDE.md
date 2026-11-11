# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A German-language Hugo static site (`travel.igl-web.de`) documenting a travel blog ("Crocs around the world"). Content is plain Markdown posts; the site is built with Hugo and deployed to GitHub Pages via GitHub Actions.

The theme `hugo-theme-cleanwhite` is pulled in as a **git submodule** under `themes/`. Clones that omit submodules will fail to build — use `git clone --recurse-submodules` or run `git submodule update --init` after cloning.

## Common commands

```bash
# Local dev server (shows drafts; archetype sets draft: true by default)
hugo server -D

# Production build (matches the CI build)
hugo --minify                 # outputs to ./public

# Create a new blog post from archetypes/default.md
hugo new post/<slug>.md

# Pull/refresh the theme submodule
git submodule update --init --recursive

# Build against a local album API instead of the deployed one
hugo server --config config.toml,config.local.toml
```

There are no tests, linters, or package managers — the entire toolchain is Hugo + bash, plus one stdlib-only Python script for the route map (below).

## Deployment

`.github/workflows/hugo.yml` deploys on every push to `main`. It also runs on PRs, but there it only builds:

1. Checks out with submodules (required for the theme).
2. Installs a **pinned** Hugo version (non-extended) via `peaceiris/actions-hugo`. Bump it deliberately and build locally with the new version first — an unpinned "latest" is how Hugo v0.146 silently broke every post layout.
3. Runs `hugo --minify`.
4. On `main` only: publishes `./public` to the `gh-pages` branch via `peaceiris/actions-gh-pages`, with `cname: travel.igl-web.de`. The `if:` guard on this step is what keeps PRs from going live before they are merged — don't remove it.

Deploys are serialized by a `concurrency` group and never cancelled, so an older build can't overwrite a newer one.

Don't edit `gh-pages` directly — it is overwritten on every deploy. The commented-out Cloudflare purge step is intentionally disabled.

The build fetches every album from the API, so a deploy takes about 90s longer than a local build and **fails if the API is unreachable**. That is deliberate — the alternative is silently publishing posts with no photos. Re-run the workflow once the API is back. (Adding an `actions/cache` step on Hugo's cache directory would make repeat deploys fast again, at the cost of serving stale albums until the cache expires.)

## Architecture

### Content model

- `content/post/*.md` — blog posts, filename-prefixed with an ordinal (`1-…`, `2-…`, … `83-…`, plus a later Brazil series `b01-…`, `b02-…`, `B30-…`). This ordering is **semantic**: Hugo's `PrevInSection`/`NextInSection` navigation in `layouts/_default/page.html` relies on date ordering, but humans sort/read by these numbers. Keep them monotonic when adding posts.
- `content/unsere-route.md` — standalone page linked from the nav menu (see `params.addtional_menus` in `config.toml`): an interactive map (`{{< route-map >}}`) above the hand-written route list. The list is the source of truth and is shown as written; see "Route map" below for how the map follows it.
- `content/karte.md` — standalone page (`/karte/`), linked as "Karte" at the top of the Länder dropdown: a world map of every country we've been to (`{{< country-map >}}`); see "Country map" below.
- `archetypes/default.md` — front-matter template for `hugo new`; sets `draft: true`.
- `static/img/home-bg-jeep.jpg` (the header photo) is 1920px wide, JPEG q60 — the 4032px original was 700KB on every page. Keep replacements around that size.

Typical post front matter (see `content/post/1-aufbruch-in-eine-unbekannte-welt.md`):

```yaml
title: "#1 - …"
date: 2022-09-15
author: Kerstin
categories: ["Deutschland", "Peru", "Lima"]
featured: 1
album: B0OGrq0zwH4gVC      # iCloud shared album ID (or its share link) — drives the gallery
aliases:                    # legacy WP URL compatibility — preserve when migrating posts
   - "/2022/09/15/…/"
```

### Photo galleries (built at deploy time)

Posts embed an iCloud shared-album gallery by declaring `album: <id>` in front matter. The share link (`https://www.icloud.com/sharedalbum/#<id>`) works too: `layouts/partials/album-id.html` reduces it to the ID, and fails the build on anything that is neither. Galleries are **built at deploy time**, not fetched by the browser.

**The constraint that shapes all of this:** iCloud's own asset URLs are signed and expire about three hours after they are issued. They can never be committed or written into a page.

The way around that is the API's image proxy. `{{ icloud_api }}/img/{album}/{photoGuid}/{thumb|small|medium|full}.jpg` is stable and unsigned — it resolves the current signed URL server-side — so Hugo can emit plain `<img src>` once and it keeps working. Sizes: `thumb` is iCloud's 342px preview (poorly compressed — prefer `small`), `full` the ~2048px original, and `small`/`medium` the original scaled by the API to ≤640px/≤1024px (the album JSON reports `smallWidth`/`smallHeight`/`mediumWidth`/`mediumHeight`). Gallery tiles use `srcset` small/medium, with `sizes` capping 3x phones at 2x; link previews (`og:image`) use medium. `album-photos.html` fails the build if the API doesn't report `smallWidth`/`mediumWidth` — **deploy API changes before site changes that depend on them**.

- `layouts/partials/album-photos.html` fetches `{{ icloud_api }}/album/{album}` with `resources.GetRemote` **during the build** and returns the photos. Both the post galleries and the homepage title images go through it, so they validate the response identically. Hugo caches remote fetches, so an album used by a post and a homepage card costs one request.
- `layouts/partials/post_list.html` renders the post cards on the homepage and country pages: a 3:2 crop of the featured photo (`.Params.featured`, an index into the album, oldest photo first — resolved by `featured-photo.html`), the post's opening "dates – places" line (`post-lead.html`; every post starts with it as a `##` heading — keep doing that), title, author/date/countries and a three-line excerpt (`plain-summary.html`). Styles: `static/css/post-list.css`. `layouts/partials/posts.html` overrides the theme's homepage wrapper to drop its empty sidebar column. It used to show a `placehold.jp` placeholder and have JS fetch the whole album per card — ten uncached API round trips before the first title image could start loading.
- `layouts/partials/image-gallery.html` builds the post gallery and emits static `<figure>` markup: a `thumb` URL for the tile, a `full` URL on the `<a>` for PhotoSwipe, real `width`/`height`, and `loading="lazy"` past the first three tiles. The full-size image is fetched only when the lightbox opens. Nothing about the gallery is fetched by the browser.
- A failed album fetch calls `errorf`, which **fails the build**. This is on purpose: a warning would ship posts with no photos and nobody would notice. Error handling uses the `try` keyword — `.Err` on a resource was removed in Hugo v0.141.
- Hugo caches remote fetches on disk, so repeat local builds are instant; the first build after clearing the cache takes ~90s for all 94 albums. `[caches.getresource] maxAge = "1h"` in `config.toml` makes local builds refetch albums after an hour — Hugo's default is to keep them forever, which hid newly added photos. CI always starts with an empty cache.
- `icloud_api` is configured in `config.toml` (default: `https://icloud-api.evolution-web.de`). To develop against a local API, put the override in an untracked `config.local.toml` and run `hugo server --config config.toml,config.local.toml`. That file also needs a `[security.http]` block: Hugo's default policy permits ordinary https hosts but blocks `localhost` and raw IPs.
- `static/css/gallery.css` is the whole gallery stylesheet: a CSS grid (2 columns, 3 from 480px) of square tiles with the `<img>` as the visible tile (`object-fit: cover`). It replaced hugo-easy-gallery.css, which floated boxes with the padding-bottom hack and painted tiles as a `background-image` — what used to force the full-size original into every tile. Keep `margin: 0; max-width: none` on the tile image: the theme's `.post-container img` rule otherwise pushes it out of its square.

### Route map

The map on `/unsere-route/` is drawn by `static/js/route-map.js` (Leaflet) from `data/route.yaml`, which nobody edits by hand: `scripts/route-to-yaml.py` derives it from the Markdown list in `content/unsere-route.md`.

- **To change the route, edit the list.** One `##` heading per trip, newest first; lines like `- 12.09. – 14.09. Lima` (stays) and `- 14.09. Nachtbus nach Huaraz` (transport). The format is loose on purpose — a language model reads it, not a parser. An unknown end (`- 07.11.26 - xx.11.26 Ometepe`, or `seit 07.11.26`) marks the current stop; a planned end in the trip heading is fine.
- The script sends each trip whose text changed to an **OpenAI-compatible** `chat/completions` endpoint (structured output via `response_format: json_schema`), validates the answer (ISO dates, chronological order, transport type, only the newest trip may be open-ended) and gives the model one retry with the errors. A second request per trip links blog posts to stops, chosen from posts whose opening "dates – places" line overlaps the trip — so a post's lead line is also what puts it on the map. The model echoes each stop's name with its index (small models slip by one in a 136-stop list), and a post whose lead line names a stop outright is linked to it even if the model missed it. Overview posts ("Peru", "Einmal um die Welt") stay unlinked on purpose. New places are geocoded through OpenStreetMap Nominatim and cached in `data/places.yaml`. The model also returns rough coordinates for every stop; they only pick between Nominatim's candidates (namesakes, restaurants called "Koh Lanta") and stand in when Nominatim finds nothing — a small model's guesses can be 100 km off or worse, so those cases are logged as `warning:` lines, as are implausibly long legs. **A misplaced point is fixed in `places.yaml`**, by correcting its `lat`/`lon`: the next run copies it into `route.yaml` without calling the model, and cached entries are never refetched (delete one to refetch it). On the first run about one point in twenty needed this.
- Each trip in `route.yaml` carries a hash of its list text and of its candidate posts (each including the prompt it was made with), so a run only calls the model for what changed — usually one request when a post is added; editing a prompt redoes every trip once.
- CI runs it in the "Update route map data" step of `hugo.yml` before the build, **on `main` only**, and commits the result back as github-actions[bot]. The step never fails the deploy: without credentials (needed only when a trip's text or posts changed, not for coordinate fixes), or when the model's answer stays invalid, the trip keeps its previous version and the site builds with the last committed data. On PRs it only notes that the data will be regenerated after merge. It needs repository settings → Secrets and variables → Actions: secret `LLM_API_KEY`, variables `LLM_BASE_URL` (e.g. `https://api.openai.com/v1`) and `LLM_MODEL`. `LLM_JSON_MODE=none` drops `response_format` for endpoints that reject it.
- Run it locally with the same three variables set: `python3 scripts/route-to-yaml.py` (`--check` reports stale trips without calling anything, `--redo Japan` regenerates the trips whose heading contains "Japan", `--force` all of them). Commit `data/route.yaml` and `data/places.yaml` together with the list change.
- `layouts/shortcodes/route-map.html` serialises the trips to JSON in the page, resolving each post slug to its title, URL and featured photo (`small.jpg` via the image proxy, loaded only when a popup opens). A slug that doesn't resolve — a draft, in a production build — is skipped.
- The basemap is **self-hosted on purpose**: Natural Earth country outlines as TopoJSON (`static/data/world-50m.json`), no tile server. Tiles from OpenStreetMap, Mapbox & co. would send every visitor's IP to a third party, which needs consent on a German site. Don't add a tile layer.
- The world trip goes once around the globe, so the script keeps raw longitudes and the JS unwraps each trip (no leg jumps more than 180°) and draws the basemap three times side by side. Natural Earth cuts Russia and Fiji at the antimeridian, which Leaflet draws as a band across the whole map unless their rings are unwrapped too; `static/js/world-outline.js` does that for both maps. Flights are dashed arcs, boats dotted, everything else solid; colours are assigned oldest trip first, so a new trip doesn't recolour the old ones.
- The "Wir sind gerade hier" marker reads `lat`/`lon` from `data/location.yaml`.

### Country map

`/karte/` (`content/karte.md`, `layouts/shortcodes/country-map.html`, `static/js/country-map.js`, `static/css/country-map.css`) fills in every country we've been to on the same self-hosted outline; clicking one opens its page in the `countries` taxonomy (`/countries/peru/`). Below the map, the same countries as a list — small countries are hard to tap on a phone, and the list works without JavaScript.

- **It needs no upkeep.** The countries are those of the stops in `data/route.yaml`, so a trip added to the route list shows up here once the route data is regenerated. Each is matched to its taxonomy term by name (the route data is German, like the front matter), or failing that through the countries of the posts linked to its stops. A country whose posts are all drafts is still coloured, with "noch keine Posts" and no link.
- The outline is found through the stop's `country_code`: `data/iso3166.yaml` maps it to the numeric ISO code that `world-50m.json` uses as each country's `id`.
- The build warns (it doesn't fail) about a country code missing from that table, and about a country in the front matter that has no stop on the route list, i.e. is missing from the map.
- **States**: where Natural Earth has them at 1:50m (USA, Kanada, Brasilien, Australien, Indonesien; also China, India, Russia, South Africa), the states our stops lie in get the country's colour and the rest of the country a light tint. The JS finds them from the stops' coordinates (`static/data/states-50m.json`, built as described in `static/vendor/README.md`); a stop on an island too small for the outline goes to the nearest state, which can be the wrong one (Pulau Pramuka, part of Jakarta, lands in Banten). Mexico has no states at this scale.
- Colours: each country's hue steps on by the golden angle, in the order we first got there, so all of them differ and a new country doesn't recolour the others; a hue within 45° of a bordering country's is skipped.
- The map is wider than the text column (the window, less 16px a side) and the page has `comments: false` in its front matter, which `page.html` honours.

### Layout overrides

The site uses the `hugo-theme-cleanwhite` theme but **overrides** specific templates at the project root (Hugo's lookup order puts `./layouts/` ahead of `./themes/*/layouts/`):

- `layouts/_default/baseof.html` — adds the "Wir sind gerade hier" location badge (a pill above the site title, styled in `static/css/header.css`), reading `data/location.yaml` (`name` + `url`). Update that data file to change the displayed current location.
- `layouts/_default/page.html` — post template; formats dates with Hugo's own localization (`.Date | time.Format ":date_full"` → "Mittwoch, 3. Juli 2024", driven by `locale = 'de-DE'` in `config.toml`), and injects the image gallery partial before post content. The post header's background is the post's featured photo (`medium` below 1100px, the original above, under a dark gradient for the white title); `image:` in front matter overrides it, and pages without an album fall back to `header_image`. The prev/next links are for posts only: the standalone pages share the root section and would otherwise link to each other. **It must be named `page.html`, not `single.html`.** Hugo v0.146 reworked template lookup so `page.html` outranks `single.html`, and the theme ships its own `_default/page.html`. While this file was called `single.html`, the theme's minimal page template silently won on every build: no galleries, no German dates, no post header, no prev/next — with no build error, because CI installs the latest Hugo.
- `layouts/partials/*.html` — overrides for `head`, `nav`, `footer`, `comments`, `image-gallery`, `post_list`, `pagination` (German "Neuere/Ältere Posts").
- `layouts/partials/footer.html` — social links from `[params.social]` as inline SVGs from `assets/icons/` (sources and licences in `assets/icons/README.md`). A configured network without an icon there triggers a build warning. The theme's footer JavaScript (table of contents, tag cloud, FastClick, Baidu, PlantUML, language switcher) is deliberately gone — nothing here used it.

### Third-party code

jQuery 1.12.4 and PhotoSwipe 4.1.1 are self-hosted under `static/vendor/`, byte-identical to the CDN builds the site used to load (same SRI hashes). So are Leaflet 1.9.4 and topojson-client 3.1.0 for the route and country maps, plus the Natural Earth outline in `static/data/` — sources, licences and hashes in `static/vendor/README.md`. Leaflet and topojson load only on those two pages. jQuery, `bootstrap.js` and the theme's `hux-blog.js` load with `defer`, so **no inline script may call `$` or `jQuery` at parse time** — wrap such code in a `DOMContentLoaded` listener. PhotoSwipe's CSS and JS are only included by `image-gallery.html`, i.e. only on posts with a gallery.

Before editing a partial, check whether the override exists locally; if not, copy from `themes/hugo-theme-cleanwhite/layouts/...` into `./layouts/...` rather than editing inside the submodule.

### Comments

Uses [giscus](https://giscus.app) (GitHub Discussions) — config is under `[params.giscus]` in `config.toml` and rendered by `layouts/partials/comments.html`. Each post's thread is a Discussion in the "Show and tell" category of this repo, matched by pathname, so **renaming a post's URL detaches its comments** (an `aliases` entry does not carry them over). giscus needs Discussions enabled on the repo and the giscus GitHub App installed on it. The site used Cusdis's hosted service until cusdis.com went down; those old comments lived on Cusdis and are not migrated. `disqus_site` and `twikoo_env_id` are present but empty.

### Site-wide data files

- `data/location.yaml` — current location banner (name + Google Maps URL) and the route map's "you are here" marker (`lat`/`lon`). Update all four together.
- `data/route.yaml`, `data/places.yaml` — route map data, generated (see "Route map"); only `places.yaml` coordinates are meant to be hand-corrected.
- `data/iso3166.yaml` — ISO country codes for the country map (see "Country map"); changes only if ISO adds a country.

## Conventions

- Site language is German (`defaultContentLanguage = 'de'`, `locale = 'de-DE'`); post titles, UI strings, and date formatting are German. Preserve this when adding content or UI text.
- Preserve legacy WordPress URLs via the `aliases` front-matter list when migrating or renaming posts.
- Photos are never committed — they are served through the API's image proxy, which caches them at the edge.
- Adding a post with a new album, or photos to an existing one, needs no extra step: the next deploy picks them up.
- `public/` is gitignored; never commit the build output.
- No analytics or tracking. Google Analytics was removed because it set cookies without consent, which is a GDPR problem for a German site; adding any tracker back needs a consent mechanism first.
