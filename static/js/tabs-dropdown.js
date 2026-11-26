/* Turns a row of filter tabs into one dropdown menu (Drafts, Submitted, My Tasks, Tasks I Assigned,
   Approvals, Notifications and the Timeline categories).

   The original tab buttons stay in the page (hidden) and keep doing the real work: choosing an option in the
   dropdown simply clicks the matching tab, so every page's own filtering code is untouched. The dropdown
   re-reads the tabs whenever they change (selected tab, counts), so the label and numbers are always current. */
(function () {
    var CHEVRON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';

    function init(tabsEl, isOn) {
        var tabs = [].slice.call(tabsEl.children).filter(function (t) { return t.tagName === 'BUTTON'; });
        if (tabs.length < 2) return;

        var wrap = document.createElement('div'); wrap.className = 'td';
        var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'td-btn';
        btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
        var cur = document.createElement('span'); cur.className = 'td-cur';
        var chev = document.createElement('span'); chev.className = 'td-chev'; chev.innerHTML = CHEVRON;
        btn.appendChild(cur); btn.appendChild(chev);
        var menu = document.createElement('div'); menu.className = 'td-menu'; menu.setAttribute('role', 'listbox');
        wrap.appendChild(btn); wrap.appendChild(menu);

        function label(t) {                       // the tab's text and count (a copy, so page scripts keep updating the originals)
            var c = t.cloneNode(true);
            [].forEach.call(c.querySelectorAll('[data-count]'), function (e) { e.removeAttribute('data-count'); });
            return c.innerHTML;
        }
        function render() {
            var on = tabs.filter(isOn)[0] || tabs[0];
            cur.innerHTML = label(on);
            menu.innerHTML = '';
            tabs.forEach(function (t, i) {
                if (i === 1) { var s = document.createElement('div'); s.className = 'td-sep'; s.setAttribute('role', 'separator'); menu.appendChild(s); }
                var o = document.createElement('button'); o.type = 'button';
                o.className = 'td-opt' + (t === on ? ' on' : '');
                o.setAttribute('role', 'option'); o.setAttribute('aria-selected', t === on ? 'true' : 'false');
                o.innerHTML = label(t);
                o.addEventListener('click', function (e) { e.stopPropagation(); t.click(); close(); btn.focus(); });
                menu.appendChild(o);
            });
        }
        function isOpen() { return wrap.classList.contains('open'); }
        function open() { wrap.classList.add('open'); btn.setAttribute('aria-expanded', 'true'); }
        function close() { wrap.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); }

        btn.addEventListener('click', function (e) { e.stopPropagation(); isOpen() ? close() : open(); });
        document.addEventListener('click', function (e) { if (!wrap.contains(e.target)) close(); });
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && isOpen()) { close(); btn.focus(); } });
        menu.addEventListener('keydown', function (e) {       // arrow keys move through the options
            var opts = [].slice.call(menu.querySelectorAll('.td-opt')), i = opts.indexOf(document.activeElement);
            if (e.key === 'ArrowDown') { e.preventDefault(); (opts[i + 1] || opts[0]).focus(); }
            if (e.key === 'ArrowUp') { e.preventDefault(); (opts[i - 1] || opts[opts.length - 1]).focus(); }
        });
        btn.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') { e.preventDefault(); open(); var o = menu.querySelector('.on') || menu.querySelector('.td-opt'); if (o) o.focus(); }
        });

        tabsEl.parentNode.insertBefore(wrap, tabsEl);
        tabsEl.classList.add('td-src');
        render();
        new MutationObserver(render).observe(tabsEl, { subtree: true, childList: true, characterData: true,
            attributes: true, attributeFilter: ['class', 'aria-selected'] });
    }

    function run() {
        [].forEach.call(document.querySelectorAll('.el-tabs'), function (el) {
            init(el, function (t) { return t.classList.contains('active'); });
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
})();
