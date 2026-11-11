# Vendored third-party files

Served from this site instead of a CDN, so pages make no third-party requests.
Each file is byte-identical to the published package; check the hashes when
upgrading, and don't edit them in place.

| File | Package | Licence |
| --- | --- | --- |
| `jquery-1.12.4.min.js` | jQuery 1.12.4 | MIT |
| `photoswipe/` | PhotoSwipe 4.1.1 | MIT |
| `leaflet/leaflet.js`, `leaflet/leaflet.css` | [leaflet](https://www.npmjs.com/package/leaflet) 1.9.4, `dist/` | BSD-2-Clause |
| `topojson-client.min.js` | [topojson-client](https://www.npmjs.com/package/topojson-client) 3.1.0, `dist/topojson-client.min.js` | ISC |
| `../data/world-50m.json` | [world-atlas](https://www.npmjs.com/package/world-atlas) 2.0.2, `countries-50m.json` — [Natural Earth](https://www.naturalearthdata.com/) 1:50m countries as TopoJSON | Public domain (Natural Earth), ISC (world-atlas) |

`data/iso3166.yaml` (outside `static/`, read by Hugo) is derived, not
copied: the alpha-2 and numeric columns of `codes.json` from
[i18n-iso-countries](https://www.npmjs.com/package/i18n-iso-countries)
7.14.0 (MIT), rewritten as YAML. It only changes if ISO adds a country.

Leaflet's marker PNGs (`dist/images/`) are left out: the route map draws
circle markers and a CSS marker, so it never requests them.

SHA-256:

```
db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a  leaflet/leaflet.js
a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6  leaflet/leaflet.css
25cd02ae486cc5063e0215a4e4cfb15de83700c87ac48bac4d57dc6aaf3ebb89  topojson-client.min.js
04342cdc1e3016bcd7db1630de95684d67b79fe3c8c460321e87aef469502394  ../data/world-50m.json
```
