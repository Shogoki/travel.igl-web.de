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

`.github/workflows/hugo.yml` deploys on every push to `main` (and runs on PRs):

1. Checks out with submodules (required for the theme).
2. Installs latest Hugo (non-extended) via `peaceiris/actions-hugo`.
3. Runs `hugo --minify`.
4. Publishes `./public` to the `gh-pages` branch via `peaceiris/actions-gh-pages`, with `cname: travel.igl-web.de`.

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

The way around that is the API's image proxy. `{{ icloud_api }}/img/{album}/{photoGuid}/{thumb|full}` is stable and unsigned — it resolves the current signed URL server-side — so Hugo can emit plain `<img src>` once and it keeps working.

- `layouts/partials/image-gallery.html` fetches `{{ icloud_api }}/album/{album}` with `resources.GetRemote` **during the build** and emits static `<figure>` markup: a `thumb` URL for the tile, a `full` URL on the `<a>` for PhotoSwipe, real `width`/`height`, and `loading="lazy"` past the first three tiles. The full-size image is fetched only when the lightbox opens. Nothing about the gallery is fetched by the browser.
- A failed album fetch calls `errorf`, which **fails the build**. This is on purpose: a warning would ship posts with no photos and nobody would notice. Error handling uses the `try` keyword — `.Err` on a resource was removed in Hugo v0.141.
- Hugo caches remote fetches on disk, so repeat local builds are instant; the first build after clearing the cache takes ~90s for all 94 albums.
- `icloud_api` is configured in `config.toml` (default: `https://icloud-api.evolution-web.de`). To develop against a local API, put the override in an untracked `config.local.toml` and run `hugo server --config config.toml,config.local.toml`. That file also needs a `[security.http]` block: Hugo's default policy permits ordinary https hosts but blocks `localhost` and raw IPs.
- `static/css/gallery.css` makes the `<img>` the visible tile. hugo-easy-gallery ships `.gallery img { display: none }` and paints tiles as a `background-image`, which is what used to force the full-size original into every tile. Keep the override if you touch that CSS, and keep `margin: 0` on it — the theme's `.post-container img` rule otherwise pushes the tile out of its square.

### Layout overrides

The site uses the `hugo-theme-cleanwhite` theme but **overrides** specific templates at the project root (Hugo's lookup order puts `./layouts/` ahead of `./themes/*/layouts/`):

- `layouts/_default/baseof.html` — adds the "Wir sind gerade hier" location banner, reading `data/location.yaml` (`name` + `url`). Update this file to change the displayed current location.
- `layouts/_default/page.html` — post template; formats dates using German weekday/month lookups in `data/days_german.toml` and `data/months_german.toml`, and injects the image gallery partial before post content. **It must be named `page.html`, not `single.html`.** Hugo v0.146 reworked template lookup so `page.html` outranks `single.html`, and the theme ships its own `_default/page.html`. While this file was called `single.html`, the theme's minimal page template silently won on every build: no galleries, no German dates, no post header, no prev/next — with no build error, because CI installs the latest Hugo.
- `layouts/partials/*.html` — overrides for `head`, `nav`, `footer`, `comments`, `image-gallery`, `post_list`, `header`.

Before editing a partial, check whether the override exists locally; if not, copy from `themes/hugo-theme-cleanwhite/layouts/...` into `./layouts/...` rather than editing inside the submodule.

### Comments

Uses [giscus](https://giscus.app) (GitHub Discussions) — config is under `[params.giscus]` in `config.toml`, wired into posts via the theme's comments partial (overridden in `layouts/partials/comments.html`). `disqus_site` and `twikoo_env_id` are present but empty.

### Site-wide data files

- `data/location.yaml` — current location banner (name + Google Maps URL).
- `data/days_german.toml`, `data/months_german.toml` — German date name lookups used by `page.html`.

## Conventions

- Site language is German (`languageCode = 'de-de'`); post titles, UI strings, and date formatting are German. Preserve this when adding content or UI text.
- Preserve legacy WordPress URLs via the `aliases` front-matter list when migrating or renaming posts.
- Photos are never committed — they are served through the API's image proxy, which caches them at the edge. `static/img/thumbs/*.jpg` and `static/img/full/*.jpg` remain gitignored.
- Adding a post with a new album, or photos to an existing one, needs no extra step: the next deploy picks them up.
- `public/` is gitignored; never commit the build output.
