/*
 * Map of every country we've been to, on /karte/
 * (layouts/shortcodes/country-map.html). Clicking a country opens its
 * country page. Same self-hosted outline as the route map, no tile layer.
 *
 * Where Natural Earth has states for a country (static/data/states-50m.json:
 * USA, Kanada, Brasilien, Australien, Indonesien, ...), the states our stops
 * lie in get the country's colour and the rest of the country a light tint
 * of it.
 */
(function () {
  'use strict';

  var dataEl = document.getElementById('country-data');
  var mapEl = document.getElementById('country-map');
  if (!dataEl || !mapEl || !window.L || !window.topojson || !window.worldOutline) return;

  // Oldest country first, as the route visited them.
  var countries = JSON.parse(dataEl.textContent);
  if (!countries.length) return;
  var byId = {};
  var byCode = {};
  countries.forEach(function (c) { byId[c.id] = c; byCode[c.cc] = c; });

  var FULL = 0.85;   // a visited country, or a visited state
  var TINT = 0.4;    // the rest of a country we only saw part of

  var map = L.map(mapEl, {
    preferCanvas: true,
    zoomSnap: 0.25,
    minZoom: 0,
    maxZoom: 7,
    scrollWheelZoom: false,
    // One finger scrolls the page on phones; two fingers pan and zoom.
    dragging: !L.Browser.mobile,
    tap: false,
    attributionControl: false
  }).setView([20, 10], 1);
  L.control.attribution({ prefix: '<a href="https://leafletjs.com">Leaflet</a>' })
    .addAttribution('Karte: <a href="https://www.naturalearthdata.com/">Natural Earth</a>')
    .addTo(map);

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // Colour n steps the hue on by the golden angle, so each new country lands
  // in the largest gap left and no two countries get the same colour, with
  // three lightnesses taking turns to tell close hues apart. In the order we
  // got there, so a new country doesn't recolour the others; a hue within
  // 45° of a bordering country's is skipped (Costa Rica and Nicaragua, the
  // mainland of Southeast Asia, Malaysia and Indonesia on Borneo).
  var LIGHTNESS = [48, 38, 58];
  function hue(n) { return (n * 137.508 + 5) % 360; }
  function hueGap(a, b) { var d = Math.abs(a - b) % 360; return Math.min(d, 360 - d); }

  function colour(world) {
    var geoms = world.objects.countries.geometries;
    var neighbours = topojson.neighbors(geoms);
    var next = 0;
    countries.forEach(function (c) {
      var near = [];
      geoms.forEach(function (g, i) {
        if (g.id !== c.id) return;
        neighbours[i].forEach(function (j) {
          var other = byId[geoms[j].id];
          if (other && other.hue != null) near.push(other.hue);
        });
      });
      for (var tries = 0; tries < 12 && near.some(function (h) { return hueGap(h, hue(next)) < 45; }); tries++) next++;
      c.hue = hue(next);
      c.color = 'hsl(' + Math.round(c.hue) + ', 62%, ' + LIGHTNESS[next % LIGHTNESS.length] + '%)';
      next++;
    });
  }

  // --- Which states we've been to ------------------------------------------
  function polygons(g) {
    return g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
  }

  // Even-odd ray casting over every ring, so holes count as outside.
  function contains(g, lon, lat) {
    var inside = false;
    polygons(g).forEach(function (poly) {
      poly.forEach(function (ring) {
        for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          var a = ring[i], b = ring[j];
          if ((a[1] > lat) !== (b[1] > lat) && lon < (b[0] - a[0]) * (lat - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
        }
      });
    });
    return inside;
  }

  // Squared distance in degrees, longitude scaled by latitude; only used to
  // compare, and only over a short way.
  function nearness(g, lon, lat) {
    var k = Math.cos(lat * Math.PI / 180), best = Infinity;
    polygons(g).forEach(function (poly) {
      poly[0].forEach(function (p) {
        var dx = (p[0] - lon) * k, dy = p[1] - lat;
        best = Math.min(best, dx * dx + dy * dy);
      });
    });
    return best;
  }

  // Each stop goes to the state it lies in. Small islands (the Gilis,
  // Komodo) aren't in the 1:50m outline, so a stop on one goes to the
  // nearest state within about 150 km.
  function visitStates(c, states) {
    (c.s || []).forEach(function (stop) {
      var name = stop.n, lat = stop.lat, lon = stop.lon;
      var hit = null;
      states.forEach(function (f) {
        if (!hit && (contains(f.geometry, lon, lat) || contains(f.geometry, lon + 360, lat) || contains(f.geometry, lon - 360, lat))) hit = f;
      });
      if (!hit) {
        var best = 1.5 * 1.5;
        states.forEach(function (f) {
          var d = nearness(f.geometry, lon, lat);
          if (d < best) { best = d; hit = f; }
        });
      }
      if (!hit) return;
      var seen = hit.properties.stops = hit.properties.stops || [];
      if (seen.indexOf(name) < 0) seen.push(name);
    });
  }

  // --- Highlighting --------------------------------------------------------
  // A country is one or more layers: its outline (several for Australia and
  // its reefs) and, where it has states, its visited states on top.
  var layersById = {};
  var listItems = {};

  // `raise`: whether highlighting may bring the layer to the front. Not a
  // country with states: it would cover its own state borders for good.
  function track(id, layer, style, raise) {
    layer.base = style;
    layer.raise = raise;
    (layersById[id] = layersById[id] || []).push(layer);
  }

  function highlight(id, on) {
    (layersById[id] || []).forEach(function (l) {
      l.setStyle(on
        ? { color: '#404040', weight: 1.5, fillOpacity: Math.min(1, l.base.fillOpacity + 0.15) }
        : l.base);
      // In order, so the states stay on top of their country.
      if (on && l.raise) l.bringToFront();
    });
    if (listItems[id]) listItems[id].classList.toggle('is-active', on);
  }

  function hover(id, layer, html, url) {
    layer.bindTooltip(html, { sticky: true, direction: 'top', offset: [0, -6] });
    layer.on('mouseover', function () { highlight(id, true); });
    layer.on('mouseout', function () { highlight(id, false); });
    if (url) layer.on('click', function () { window.location.href = url; });
  }

  function posts(c) {
    return c.u ? (c.c === 1 ? '1 Post' : c.c + ' Posts') : 'noch keine Posts';
  }

  // --- Drawing -------------------------------------------------------------
  var statesUrl = mapEl.getAttribute('data-states');
  Promise.all([
    worldOutline.load(mapEl.getAttribute('data-world')),
    // The map works without states, only coarser.
    statesUrl ? worldOutline.load(statesUrl).catch(function () { return null; }) : null
  ])
    .then(function (res) {
      var world = res[0], statesTopo = res[1];
      colour(world);

      var statesOf = {};
      if (statesTopo) {
        worldOutline.unwrapGeometry(topojson.feature(statesTopo, statesTopo.objects.states)).features.forEach(function (f) {
          var c = byCode[f.properties.cc];
          if (c) (statesOf[c.id] = statesOf[c.id] || []).push(f);
        });
        countries.forEach(function (c) {
          if (statesOf[c.id]) visitStates(c, statesOf[c.id]);
        });
      }

      var renderer = L.canvas({ padding: 0.5 });
      var features = worldOutline.unwrapGeometry(topojson.feature(world, world.objects.countries));

      L.geoJSON(features, {
        renderer: renderer,
        interactive: false,
        filter: function (f) { return !byId[f.id]; },
        style: { fillColor: '#e9e4d8', fillOpacity: 1, color: '#fff', weight: 0.6 }
      }).addTo(map);

      function countryStyle(f) {
        return { fillColor: byId[f.id].color, fillOpacity: statesOf[f.id] ? TINT : FULL, color: '#fff', weight: 0.6 };
      }
      var visited = L.geoJSON(features, {
        renderer: renderer,
        filter: function (f) { return !!byId[f.id]; },
        style: countryStyle,
        onEachFeature: function (f, layer) {
          var c = byId[f.id];
          track(f.id, layer, countryStyle(f), !statesOf[f.id]);
          hover(f.id, layer, '<strong>' + esc(c.f) + ' ' + esc(c.n) + '</strong><br>' + posts(c), c.u);
        }
      }).addTo(map);

      // The borders of every state of those countries, so the tint reads as
      // "the rest of the country"; only the visited ones take the mouse.
      Object.keys(statesOf).forEach(function (id) {
        var c = byId[id];
        L.geoJSON({ type: 'FeatureCollection', features: statesOf[id] }, {
          renderer: renderer,
          interactive: false,
          filter: function (f) { return !f.properties.stops; },
          style: { fill: false, color: '#fff', weight: 0.5, opacity: 0.9 }
        }).addTo(map);
        var stateStyle = { fillColor: c.color, fillOpacity: FULL, color: '#fff', weight: 0.6 };
        L.geoJSON({ type: 'FeatureCollection', features: statesOf[id] }, {
          renderer: renderer,
          filter: function (f) { return !!f.properties.stops; },
          style: stateStyle,
          onEachFeature: function (f, layer) {
            track(id, layer, stateStyle, true);
            hover(id, layer,
              '<strong>' + esc(c.f) + ' ' + esc(f.properties.n) + '</strong><br>' +
              esc(c.n) + ' · ' + esc(f.properties.stops.join(', ')), c.u);
          }
        }).addTo(map);
      });

      map.fitBounds(visited.getBounds(), { padding: [8, 8] });

      // The list below the map: same colours, and hovering an entry
      // highlights its country.
      Array.prototype.forEach.call(document.querySelectorAll('.country-list [data-id]'), function (li) {
        var id = li.getAttribute('data-id');
        if (!byId[id]) return;
        listItems[id] = li;
        li.style.setProperty('--country-color', byId[id].color);
        li.addEventListener('mouseenter', function () { highlight(id, true); });
        li.addEventListener('mouseleave', function () { highlight(id, false); });
      });
    })
    .catch(function () { /* The list below still has every country. */ });
})();
