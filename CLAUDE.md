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

There are no tests, linters, or package managers — the entire toolchain is Hugo + bash.

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
- `content/unsere-route.md` — standalone page linked from the nav menu (see `params.addtional_menus` in `config.toml`).
- `archetypes/default.md` — front-matter template for `hugo new`; sets `draft: true`.

Typical post front matter (see `content/post/1-aufbruch-in-eine-unbekannte-welt.md`):

```yaml
title: "#1 - …"
date: 2022-09-15
author: Kerstin
categories: ["Deutschland", "Peru", "Lima"]
featured: 1
album: B0OGrq0zwH4gVC      # iCloud shared album ID — drives the gallery
aliases:                    # legacy WP URL compatibility — preserve when migrating posts
   - "/2022/09/15/…/"
```

### Photo galleries (built at deploy time)

Posts embed an iCloud shared-album gallery by declaring `album: <id>` in front matter. Galleries are **built at deploy time**, not fetched by the browser.

**The constraint that shapes all of this:** iCloud's own asset URLs are signed and expire about three hours after they are issued. They can never be committed or written into a page.

The way around that is the API's image proxy. `{{ icloud_api }}/img/{album}/{photoGuid}/{thumb|small|medium|full}.jpg` is stable and unsigned — it resolves the current signed URL server-side — so Hugo can emit plain `<img src>` once and it keeps working. Sizes: `thumb` is iCloud's 342px preview (poorly compressed — prefer `small`), `full` the ~2048px original, and `small`/`medium` the original scaled by the API to ≤640px/≤1024px (the album JSON reports `smallWidth`/`smallHeight`/`mediumWidth`/`mediumHeight`). Gallery tiles use `srcset` small/medium, with `sizes` capping 3x phones at 2x; link previews (`og:image`) use medium. `album-photos.html` fails the build if the API doesn't report `smallWidth`/`mediumWidth` — **deploy API changes before site changes that depend on them**.

- `layouts/partials/album-photos.html` fetches `{{ icloud_api }}/album/{album}` with `resources.GetRemote` **during the build** and returns the photos. Both the post galleries and the homepage title images go through it, so they validate the response identically. Hugo caches remote fetches, so an album used by a post and a homepage card costs one request.
- `layouts/partials/post_list.html` renders the post cards on the homepage and country pages: a 3:2 crop of the featured photo (`.Params.featured`, an index into the album, oldest photo first — resolved by `featured-photo.html`), the post's opening "dates – places" line (`post-lead.html`; every post starts with it as a `##` heading — keep doing that), title, author/date/countries and a three-line excerpt (`plain-summary.html`). Styles: `static/css/post-list.css`. `layouts/partials/posts.html` overrides the theme's homepage wrapper to drop its empty sidebar column. It used to show a `placehold.jp` placeholder and have JS fetch the whole album per card — ten uncached API round trips before the first title image could start loading.
- `layouts/partials/image-gallery.html` builds the post gallery and emits static `<figure>` markup: a `thumb` URL for the tile, a `full` URL on the `<a>` for PhotoSwipe, real `width`/`height`, and `loading="lazy"` past the first three tiles. The full-size image is fetched only when the lightbox opens. Nothing about the gallery is fetched by the browser.
- A failed album fetch calls `errorf`, which **fails the build**. This is on purpose: a warning would ship posts with no photos and nobody would notice. Error handling uses the `try` keyword — `.Err` on a resource was removed in Hugo v0.141.
- Hugo caches remote fetches on disk, so repeat local builds are instant; the first build after clearing the cache takes ~90s for all 94 albums. `[caches.getresource] maxAge = "1h"` in `config.toml` makes local builds refetch albums after an hour — Hugo's default is to keep them forever, which hid newly added photos. CI always starts with an empty cache.
- `icloud_api` is configured in `config.toml` (default: `https://icloud-api.evolution-web.de`). To develop against a local API, put the override in an untracked `config.local.toml` and run `hugo server --config config.toml,config.local.toml`. That file also needs a `[security.http]` block: Hugo's default policy permits ordinary https hosts but blocks `localhost` and raw IPs.
- `static/css/gallery.css` is the whole gallery stylesheet: a CSS grid (2 columns, 3 from 480px) of square tiles with the `<img>` as the visible tile (`object-fit: cover`). It replaced hugo-easy-gallery.css, which floated boxes with the padding-bottom hack and painted tiles as a `background-image` — what used to force the full-size original into every tile. Keep `margin: 0; max-width: none` on the tile image: the theme's `.post-container img` rule otherwise pushes it out of its square.

### Layout overrides

The site uses the `hugo-theme-cleanwhite` theme but **overrides** specific templates at the project root (Hugo's lookup order puts `./layouts/` ahead of `./themes/*/layouts/`):

- `layouts/_default/baseof.html` — adds the "Wir sind gerade hier" location banner, reading `data/location.yaml` (`name` + `url`). Update this file to change the displayed current location.
- `layouts/_default/page.html` — post template; formats dates with Hugo's own localization (`.Date | time.Format ":date_full"` → "Mittwoch, 3. Juli 2024", driven by `locale = 'de-DE'` in `config.toml`), and injects the image gallery partial before post content. **It must be named `page.html`, not `single.html`.** Hugo v0.146 reworked template lookup so `page.html` outranks `single.html`, and the theme ships its own `_default/page.html`. While this file was called `single.html`, the theme's minimal page template silently won on every build: no galleries, no German dates, no post header, no prev/next — with no build error, because CI installs the latest Hugo.
- `layouts/partials/*.html` — overrides for `head`, `nav`, `footer`, `comments`, `image-gallery`, `post_list`, `pagination` (German "Neuere/Ältere Posts").
- `layouts/partials/footer.html` — social links from `[params.social]` as inline SVGs from `assets/icons/` (sources and licences in `assets/icons/README.md`). A configured network without an icon there triggers a build warning. The theme's footer JavaScript (table of contents, tag cloud, FastClick, Baidu, PlantUML, language switcher) is deliberately gone — nothing here used it.

### Third-party code

jQuery 1.12.4 and PhotoSwipe 4.1.1 are self-hosted under `static/vendor/`, byte-identical to the CDN builds the site used to load (same SRI hashes). jQuery, `bootstrap.js` and the theme's `hux-blog.js` load with `defer`, so **no inline script may call `$` or `jQuery` at parse time** — wrap such code in a `DOMContentLoaded` listener. PhotoSwipe's CSS and JS are only included by `image-gallery.html`, i.e. only on posts with a gallery.

Before editing a partial, check whether the override exists locally; if not, copy from `themes/hugo-theme-cleanwhite/layouts/...` into `./layouts/...` rather than editing inside the submodule.

### Comments

Uses [giscus](https://giscus.app) (GitHub Discussions) — config is under `[params.giscus]` in `config.toml`, wired into posts via the theme's comments partial (overridden in `layouts/partials/comments.html`). `disqus_site` and `twikoo_env_id` are present but empty.

### Site-wide data files

- `data/location.yaml` — current location banner (name + Google Maps URL).

## Conventions

- Site language is German (`defaultContentLanguage = 'de'`, `locale = 'de-DE'`); post titles, UI strings, and date formatting are German. Preserve this when adding content or UI text.
- Preserve legacy WordPress URLs via the `aliases` front-matter list when migrating or renaming posts.
- Photos are never committed — they are served through the API's image proxy, which caches them at the edge.
- Adding a post with a new album, or photos to an existing one, needs no extra step: the next deploy picks them up.
- `public/` is gitignored; never commit the build output.
