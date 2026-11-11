/*
 * Map of every country we've been to, on /karte/
 * (layouts/shortcodes/country-map.html). Clicking a country opens its
 * country page. Same self-hosted outline as the route map, no tile layer.
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
  countries.forEach(function (c) { byId[c.id] = c; });

  var PALETTE = ['#d9534f', '#2a7ab0', '#e08e0b', '#3c9a5f', '#8e5ea2',
                 '#c2185b', '#00897b', '#6d4c41', '#5c6bc0', '#7cb342'];

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

  // Cycle through the palette in the order we got there, skipping a colour
  // a bordering country already has (Costa Rica and Nicaragua, the
  // mainland of Southeast Asia, Malaysia and Indonesia on Borneo).
  function colour(world) {
    var geoms = world.objects.countries.geometries;
    var neighbours = topojson.neighbors(geoms);
    var next = 0;
    countries.forEach(function (c) {
      var taken = {};
      geoms.forEach(function (g, i) {
        if (g.id !== c.id) return;
        neighbours[i].forEach(function (j) {
          var other = byId[geoms[j].id];
          if (other && other.color) taken[other.color] = true;
        });
      });
      for (var tries = 0; tries < PALETTE.length && taken[PALETTE[next % PALETTE.length]]; tries++) next++;
      c.color = PALETTE[next % PALETTE.length];
      next++;
    });
  }

  // A country can be several features (Australia and its reefs), so
  // highlighting goes through all layers with its id.
  var layersById = {};
  var listItems = {};

  function highlight(id, on) {
    (layersById[id] || []).forEach(function (l) {
      l.setStyle(on ? { fillOpacity: 1, color: '#404040', weight: 1.5 } : { fillOpacity: 0.85, color: '#fff', weight: 0.6 });
      if (on) l.bringToFront();
    });
    if (listItems[id]) listItems[id].classList.toggle('is-active', on);
  }

  function tooltip(c) {
    var posts = c.u ? (c.c === 1 ? '1 Post' : c.c + ' Posts') : 'noch keine Posts';
    return '<strong>' + esc(c.f) + ' ' + esc(c.n) + '</strong><br>' + posts;
  }

  worldOutline.load(mapEl.getAttribute('data-world'))
    .then(function (world) {
      colour(world);
      var renderer = L.canvas({ padding: 0.5 });
      var features = worldOutline.unwrapGeometry(topojson.feature(world, world.objects.countries));

      L.geoJSON(features, {
        renderer: renderer,
        interactive: false,
        filter: function (f) { return !byId[f.id]; },
        style: { fillColor: '#e9e4d8', fillOpacity: 1, color: '#fff', weight: 0.6 }
      }).addTo(map);

      var visited = L.geoJSON(features, {
        renderer: renderer,
        filter: function (f) { return !!byId[f.id]; },
        style: function (f) {
          return { fillColor: byId[f.id].color, fillOpacity: 0.85, color: '#fff', weight: 0.6 };
        },
        onEachFeature: function (f, layer) {
          var c = byId[f.id];
          (layersById[f.id] = layersById[f.id] || []).push(layer);
          layer.bindTooltip(tooltip(c), { sticky: true, direction: 'top', offset: [0, -6] });
          layer.on('mouseover', function () { highlight(f.id, true); });
          layer.on('mouseout', function () { highlight(f.id, false); });
          if (c.u) layer.on('click', function () { window.location.href = c.u; });
        }
      }).addTo(map);

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
