/* Filter tabs (.nt-tabs) shown as ONE dropdown: the button shows the current filter, the menu lists them all.
   The tab buttons are only moved into the menu, never copied, so each page's own filtering keeps working. */
(function () {
    'use strict';
    var CHEV = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';

    function setup(bar) {
        var tabs = [].slice.call(bar.querySelectorAll(':scope > .nt-tab'));
        if (tabs.length < 2) return;

        var dd = document.createElement('div');
        dd.className = 'nt-more';
        dd.innerHTML = '<button type="button" class="nt-more-btn" aria-haspopup="true" aria-expanded="false">' +
            '<span class="nt-more-label"></span>' + CHEV + '</button><div class="nt-menu" role="menu"></div>';
        bar.appendChild(dd);
        var btn = dd.querySelector('.nt-more-btn'), menu = dd.querySelector('.nt-menu'),
            label = dd.querySelector('.nt-more-label');
        tabs.forEach(function (t) { menu.appendChild(t); });

        function sync() {                       // the button shows the selected filter and its count
            var act = menu.querySelector('.nt-tab.active') || tabs[0];
            var count = act.querySelector('.nt-count');
            label.innerHTML = '';
            label.appendChild(document.createTextNode((act.firstChild.textContent || '').trim()));
            if (count) label.appendChild(count.cloneNode(true));
        }
        function close() { dd.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); }

        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var open = !dd.classList.contains('open');
            dd.classList.toggle('open', open);
            btn.setAttribute('aria-expanded', open);
        });
        menu.addEventListener('click', function () {
            close();
            setTimeout(sync, 0);                // after the page's own click handler marked the tab active
        });
        document.addEventListener('click', function (e) { if (!dd.contains(e.target)) close(); });
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
        sync();
    }

    [].forEach.call(document.querySelectorAll('.nt-tabs'), setup);
})();
