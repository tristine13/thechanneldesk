/* Tabs for the inbox-style lists (Approvals, Tasks): .nt-tab[data-nt-tab] filters .nt-item[data-group]. */
(function () {
    var tabs = document.querySelectorAll('[data-nt-tab]'), none = document.getElementById('nt-none');
    if (!tabs.length) return;
    function apply(kind) {
        var shown = 0;
        document.querySelectorAll('.nt-item').forEach(function (el) {
            var ok = kind === 'all' || el.dataset.group === kind;
            el.hidden = !ok; if (ok) shown++;
        });
        document.querySelectorAll('.nt-group').forEach(function (g) { g.hidden = !g.querySelector('.nt-item:not([hidden])'); });
        if (none) none.hidden = shown > 0 || !document.querySelector('.nt-item');
    }
    tabs.forEach(function (t) {
        t.addEventListener('click', function () {
            tabs.forEach(function (o) { o.classList.toggle('active', o === t); });
            apply(t.dataset.ntTab);
        });
    });
})();
