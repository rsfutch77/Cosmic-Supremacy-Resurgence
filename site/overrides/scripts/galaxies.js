// galaxies.js: fills the Galaxies panel with the open galaxies' titles.
// The referee publishes the list after each tick (server/public_listing.py).
// The coming-soon note stays when the list cannot be read or names no galaxy.
(function () {
  var panel = document.getElementById('galaxy-list');
  if (!panel || !window.fetch) return;
  fetch(panel.getAttribute('data-src'))
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (doc) {
      var rows = (doc && doc.galaxies) || [];
      var titles = rows.map(function (g) { return g && g.title; })
                       .filter(function (t) { return typeof t === 'string' && t; });
      if (!titles.length) return;
      panel.textContent = '';
      titles.forEach(function (t) {
        var p = document.createElement('p');
        var b = document.createElement('b');
        b.textContent = t;
        p.appendChild(b);
        panel.appendChild(p);
      });
    })
    .catch(function () {});
})();
