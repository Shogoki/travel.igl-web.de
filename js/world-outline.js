/*
 * The world outline (static/data/world-50m.json), shared by the route map
 * (route-map.js) and the country map (country-map.js). Load it before them.
 */
window.worldOutline = (function () {
  'use strict';

  // The longitude equal to `lon` (give or take whole turns) that is closest
  // to `ref`, so a line from ref to it never jumps across the whole map.
  function unwrap(lon, ref) {
    while (lon - ref > 180) lon -= 360;
    while (lon - ref < -180) lon += 360;
    return lon;
  }

  // Natural Earth cuts Russia, Wrangel Island and Fiji at the antimeridian,
  // so their rings jump from 180° to -180°, and Leaflet fills that jump as a
  // band across the whole map. Unwrapped, the cut-off piece just reaches
  // past 180°. Antarctica's ring circles the pole and would no longer close
  // unwrapped, so a ring that doesn't end where it started is kept as is.
  function unwrapCoords(c) {
    if (typeof c[0][0] !== 'number') return c.map(unwrapCoords);
    var prev = c[0][0];
    var out = c.map(function (p) {
      prev = unwrap(p[0], prev);
      return [prev, p[1]];
    });
    return Math.abs(out[out.length - 1][0] - out[0][0]) > 180 ? c : out;
  }

  function unwrapGeometry(g) {
    if (g.type === 'FeatureCollection') g.features.forEach(function (f) { unwrapGeometry(f.geometry); });
    else if (g.type === 'Feature') unwrapGeometry(g.geometry);
    else if (g.type === 'GeometryCollection') g.geometries.forEach(unwrapGeometry);
    else if (g.type !== 'Point' && g.type !== 'MultiPoint') g.coordinates = unwrapCoords(g.coordinates);
    return g;
  }

  function load(url) {
    return fetch(url).then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); });
  }

  return { unwrap: unwrap, unwrapGeometry: unwrapGeometry, load: load };
})();
