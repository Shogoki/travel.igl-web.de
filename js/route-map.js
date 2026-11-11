/*
 * Interactive route map for /unsere-route/ (layouts/shortcodes/route-map.html).
 *
 * Plain JS on purpose: jQuery loads with `defer`, and this needs nothing
 * from it. The basemap is a TopoJSON country outline served from this site;
 * there is no tile layer, so the map makes no third-party requests until a
 * popup shows a post's thumbnail from the photo API.
 */
(function () {
  'use strict';

  var dataEl = document.getElementById('route-data');
  var mapEl = document.getElementById('route-map');
  if (!dataEl || !mapEl || !window.L || !window.worldOutline) return;

  var data = JSON.parse(dataEl.textContent);
  var trips = data.trips || [];
  if (!trips.length) return;

  // Colours are assigned oldest trip first, so adding a trip at the top of
  // the list doesn't recolour the others.
  var PALETTE = ['#d9534f', '#2a7ab0', '#e08e0b', '#3c9a5f', '#8e5ea2',
                 '#c2185b', '#00897b', '#6d4c41', '#5c6bc0', '#7cb342'];
  trips.forEach(function (t, i) {
    t.color = PALETTE[(trips.length - 1 - i) % PALETTE.length];
  });

  var BY = { flug: 'Flug', bus: 'Bus', zug: 'Zug', auto: 'Auto', boot: 'Boot', fuss: 'zu Fuß' };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmtDate(iso) {
    var p = iso.split('-');
    return p[2] + '.' + p[1] + '.' + p[0];
  }

  function fmtRange(s) {
    if (!s.to) return 'seit ' + fmtDate(s.from);
    if (s.to === s.from) return fmtDate(s.from);
    return fmtDate(s.from) + ' – ' + fmtDate(s.to);
  }

  // --- Longitudes ----------------------------------------------------------
  // The Weltreise goes once around the globe. Unwrap each trip so no leg
  // jumps more than 180° (Vancouver -> Sydney crosses the Pacific instead
  // of the whole map), then shift every other trip by whole turns so that
  // all of them sit on the same copy of the world as the widest one.
  var unwrap = worldOutline.unwrap;

  trips.forEach(function (t) {
    var prev = null;
    t.stops.forEach(function (s) {
      s.x = prev === null ? s.lon : unwrap(s.lon, prev);
      prev = s.x;
    });
    var xs = t.stops.map(function (s) { return s.x; });
    t.min = Math.min.apply(null, xs);
    t.max = Math.max.apply(null, xs);
  });
  var widest = trips.reduce(function (a, t) { return t.max - t.min > a.max - a.min ? t : a; });
  var centre = (widest.min + widest.max) / 2;
  trips.forEach(function (t) {
    if (t === widest) return;
    var mid = (t.min + t.max) / 2;
    var shift = unwrap(mid, centre) - mid;
    t.stops.forEach(function (s) { s.x += shift; });
  });

  // --- Map -----------------------------------------------------------------
  var map = L.map(mapEl, {
    preferCanvas: true,
    zoomSnap: 0.25,
    minZoom: 0,
    maxZoom: 10,
    scrollWheelZoom: false,
    // One finger scrolls the page on phones; two fingers pan and zoom.
    dragging: !L.Browser.mobile,
    tap: false,
    attributionControl: false
  });
  L.control.attribution({ prefix: '<a href="https://leafletjs.com">Leaflet</a>' })
    .addAttribution('Karte: <a href="https://www.naturalearthdata.com/">Natural Earth</a>')
    .addTo(map);

  map.createPane('basemap').style.zIndex = 250;
  var basemapRenderer = L.canvas({ pane: 'basemap', padding: 0.5 });

  worldOutline.load(mapEl.getAttribute('data-world'))
    .then(function (world) {
      var land = worldOutline.unwrapGeometry(topojson.feature(world, world.objects.land));
      var borders = worldOutline.unwrapGeometry(topojson.mesh(world, world.objects.countries, function (a, b) { return a !== b; }));
      // Three copies of the world, so land is under the route wherever the
      // unwrapping put it.
      [-360, 0, 360].forEach(function (off) {
        function toLatLng(c) { return L.latLng(c[1], c[0] + off); }
        L.geoJSON(land, {
          pane: 'basemap', renderer: basemapRenderer, interactive: false, coordsToLatLng: toLatLng,
          style: { stroke: false, fillColor: '#e9e4d8', fillOpacity: 1 }
        }).addTo(map);
        L.geoJSON(borders, {
          pane: 'basemap', renderer: basemapRenderer, interactive: false, coordsToLatLng: toLatLng,
          style: { color: '#bdb5a3', weight: 0.6, opacity: 1, fill: false }
        }).addTo(map);
      });
    })
    .catch(function () { /* The route still reads fine on the plain sea colour. */ });

  // Flights as a gentle arc: a quadratic Bézier through a control point
  // pushed off the straight line, bulging to the left of the direction of travel.
  function arc(a, b) {
    var dx = b[1] - a[1], dy = b[0] - a[0];
    var c = [(a[0] + b[0]) / 2 + dx * 0.18, (a[1] + b[1]) / 2 - dy * 0.18];
    var pts = [];
    for (var i = 0; i <= 32; i++) {
      var t = i / 32, u = 1 - t;
      pts.push([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0],
                u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]);
    }
    return pts;
  }

  function popupHtml(trip, group) {
    var first = group[0];
    var html = '<div class="route-popup"><strong>' + esc(first.n) + ' ' + esc(first.f) + '</strong>' +
      '<div class="route-popup-meta">' + esc(first.c) + ' · ' + esc(trip.title) + '</div><ul class="route-popup-visits">';
    var posts = [];
    group.forEach(function (s) {
      var how = s.l || (s.by ? BY[s.by] : '');
      html += '<li>' + esc(fmtRange(s)) + (how ? '<br><span class="route-popup-how">' + esc(how) + '</span>' : '') + '</li>';
      s.posts.forEach(function (p) {
        if (!posts.some(function (q) { return q.u === p.u; })) posts.push(p);
      });
    });
    html += '</ul>';
    if (posts.length) {
      html += '<ul class="route-popup-posts">';
      posts.forEach(function (p) {
        html += '<li><a href="' + esc(p.u) + '">' +
          (p.img ? '<img src="' + esc(p.img) + '" alt="" loading="lazy" width="64" height="48">' : '') +
          '<span>' + esc(p.t) + '</span></a></li>';
      });
      html += '</ul>';
    }
    return html + '</div>';
  }

  trips.forEach(function (t) {
    var layer = L.featureGroup();
    var pts = t.stops.map(function (s) { return [s.lat, s.x]; });

    for (var i = 1; i < pts.length; i++) {
      var a = pts[i - 1], b = pts[i], by = t.stops[i].by;
      if (a[0] === b[0] && a[1] === b[1]) continue;
      var line = by === 'flug' ? arc(a, b) : [a, b];
      L.polyline(line, {
        color: t.color, weight: 2.5, opacity: 0.85, interactive: false,
        dashArray: by === 'flug' ? '6 6' : by === 'boot' ? '1 6' : null,
        lineCap: 'round'
      }).addTo(layer);
    }

    // Repeat visits (Koh Phi Phi, three times) share one marker.
    var groups = {};
    var order = [];
    t.stops.forEach(function (s) {
      var key = s.n + '|' + s.lat + '|' + s.x;
      if (!groups[key]) { groups[key] = []; order.push(key); }
      groups[key].push(s);
    });
    order.forEach(function (key) {
      var group = groups[key];
      L.circleMarker([group[0].lat, group[0].x], {
        radius: 5, color: '#fff', weight: 1.5, fillColor: t.color, fillOpacity: 1
      })
        .bindTooltip(group[0].n, { direction: 'top', offset: [0, -4] })
        // Built when it opens, so no thumbnail loads before someone asks.
        .bindPopup(function () { return popupHtml(t, group); }, { maxWidth: 260 })
        .addTo(layer);
    });

    t.layer = layer;
  });

  var here = null;
  if (data.here && data.here.lat != null) {
    here = L.marker([data.here.lat, unwrap(data.here.lon, centre)], {
      icon: L.divIcon({ className: 'route-here', iconSize: [18, 18] }),
      keyboard: false, zIndexOffset: 1000, title: 'Wir sind gerade hier'
    }).bindPopup('<div class="route-popup"><strong>Wir sind gerade hier:</strong><br>' + esc(data.here.name) + '</div>');
  }

  // --- Trip filter ---------------------------------------------------------
  var filter = document.querySelector('.route-filter');
  var buttons = [];

  function show(trip) {
    var bounds = L.latLngBounds([]);
    trips.forEach(function (t) {
      var on = !trip || t === trip;
      if (on) {
        t.layer.addTo(map);
        bounds.extend(t.layer.getBounds());
      } else {
        map.removeLayer(t.layer);
      }
    });
    if (here) {
      here.addTo(map);
      if (!trip) bounds.extend(here.getLatLng());
    }
    buttons.forEach(function (b) { b.el.setAttribute('aria-pressed', String(b.trip === trip)); });
    map.closePopup();
    map.fitBounds(bounds, { padding: [24, 24], maxZoom: 8 });
  }

  function button(label, trip) {
    var el = document.createElement('button');
    el.type = 'button';
    el.className = 'route-filter-btn';
    if (trip) el.style.setProperty('--trip-color', trip.color);
    el.textContent = label;
    el.addEventListener('click', function () { show(trip); });
    filter.appendChild(el);
    buttons.push({ el: el, trip: trip });
  }

  button('Alle', null);
  trips.forEach(function (t) {
    var y1 = t.stops[0].from.slice(0, 4);
    var last = t.stops[t.stops.length - 1];
    var y2 = (last.to || last.from).slice(0, 4);
    var years = y1 === y2 ? y1 : y1 + '/' + y2.slice(2);
    // The title often already names the year range's place; keep it short.
    button(t.title.replace(/\s*[\u{1F1E6}-\u{1F1FF}]+/gu, '') + ' ' + years, t);
  });

  show(null);
})();
