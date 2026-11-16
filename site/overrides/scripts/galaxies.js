// galaxies.js: fills the Galaxies panel with the open galaxies' titles and turns.
// The referee publishes the list after each tick (server/public_listing.py).
// The coming-soon note stays when the list cannot be read or names no galaxy.
// A row without a turn shows its title alone.
(function () {
  var panel = document.getElementById('galaxy-list');
  if (!panel || !window.fetch) return;
  fetch(panel.getAttribute('data-src'))
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (doc) {
      var rows = ((doc && doc.galaxies) || []).filter(function (g) {
        return g && typeof g.title === 'string' && g.title;
      });
      if (!rows.length) return;
      panel.textContent = '';
      rows.forEach(function (g) {
        var p = document.createElement('p');
        var b = document.createElement('b');
        b.textContent = g.title;
        p.appendChild(b);
        if (typeof g.turn === 'number' && isFinite(g.turn)) {
          var s = document.createElement('span');
          s.className = 'small';
          s.textContent = 'Turn ' + g.turn;
          p.appendChild(document.createElement('br'));
          p.appendChild(s);
        }
        panel.appendChild(p);
      });
    })
    .catch(function () {});
})();
