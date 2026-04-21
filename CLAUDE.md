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

# Regenerate data/albums.json from the iCloud album API
# (not currently run in CI — see .github/workflows/hugo.yml)
./scripts/fetchalbums.sh
```

There are no tests, linters, or package managers — the entire toolchain is Hugo + bash.

## Deployment

`.github/workflows/hugo.yml` deploys on every push to `main` (and runs on PRs):

1. Checks out with submodules (required for the theme).
2. Installs latest Hugo (non-extended) via `peaceiris/actions-hugo`.
3. Runs `hugo --minify`.
4. Publishes `./public` to the `gh-pages` branch via `peaceiris/actions-gh-pages`, with `cname: travel.igl-web.de`.

Don't edit `gh-pages` directly — it is overwritten on every deploy. The commented-out Cloudflare purge step and `./scripts/fetchalbums.sh` call in the workflow are intentionally disabled.

## Architecture

### Content model

- `content/post/*.md` — blog posts, filename-prefixed with an ordinal (`1-…`, `2-…`, … `83-…`, plus a later Brazil series `b01-…`, `b02-…`, `B30-…`). This ordering is **semantic**: Hugo's `PrevInSection`/`NextInSection` navigation in `layouts/_default/single.html` relies on date ordering, but humans sort/read by these numbers. Keep them monotonic when adding posts.
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

### Photo galleries (runtime fetch)

Posts embed an iCloud shared-album gallery by declaring `album: <id>` in front matter. The mechanism is in `layouts/partials/image-gallery.html`:

- On page load, client-side JS `fetch()`s `{{ .Site.Params.icloud_api }}/album/{{ album }}` and renders each image into a PhotoSwipe gallery.
- `icloud_api` is configured in `config.toml` (default: `https://icloud-api.evolution-web.de`; a localhost value is commented out for local API dev).
- `scripts/fetchalbums.sh` exists to *pre-download* album JSON (into `data/albums.json`) and thumbnails/full images (into `static/img/{thumbs,full}/`, both gitignored). It is currently not invoked by CI, so the site depends on the live API at page-load time.

When touching gallery code, remember both the fetch-at-runtime path (`image-gallery.html`) and the offline-cache path (`fetchalbums.sh`) exist and are meant to be interchangeable.

### Layout overrides

The site uses the `hugo-theme-cleanwhite` theme but **overrides** specific templates at the project root (Hugo's lookup order puts `./layouts/` ahead of `./themes/*/layouts/`):

- `layouts/_default/baseof.html` — adds the "Wir sind gerade hier" location banner, reading `data/location.yaml` (`name` + `url`). Update this file to change the displayed current location.
- `layouts/_default/single.html` — post template; formats dates using German weekday/month lookups in `data/days_german.toml` and `data/months_german.toml`, and injects the image gallery partial before post content.
- `layouts/partials/*.html` — overrides for `head`, `nav`, `footer`, `comments`, `image-gallery`, `post_list`, `header`.

Before editing a partial, check whether the override exists locally; if not, copy from `themes/hugo-theme-cleanwhite/layouts/...` into `./layouts/...` rather than editing inside the submodule.

### Comments

Uses [giscus](https://giscus.app) (GitHub Discussions) — config is under `[params.giscus]` in `config.toml`, wired into posts via the theme's comments partial (overridden in `layouts/partials/comments.html`). `disqus_site` and `twikoo_env_id` are present but empty.

### Site-wide data files

- `data/location.yaml` — current location banner (name + Google Maps URL).
- `data/days_german.toml`, `data/months_german.toml` — German date name lookups used by `single.html`.
- `data/albums.json` — optional, generated by `fetchalbums.sh`; not required for the runtime-fetch gallery path.

## Conventions

- Site language is German (`languageCode = 'de-de'`); post titles, UI strings, and date formatting are German. Preserve this when adding content or UI text.
- Preserve legacy WordPress URLs via the `aliases` front-matter list when migrating or renaming posts.
- Image paths `static/img/thumbs/*.jpg` and `static/img/full/*.jpg` are gitignored — don't commit generated thumbnails.
- `public/` is gitignored; never commit the build output.
