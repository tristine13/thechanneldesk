"""Safe rich text for articles (shared by the staff app and the student news site; keep both copies identical).

New articles are saved as HTML that starts with the marker <!--rt-->. Whatever the browser sends is cleaned
here: only a short list of tags and style properties survives, so nobody can slip a script into an article.
Older articles (plain text with **bold** / *italic* markers) still work: is_rich() tells the two apart.
"""
import re
from html import escape, unescape
from html.parser import HTMLParser

RT = "<!--rt-->"


def is_rich(text):
    return (text or "").startswith(RT)


_LEN = re.compile(r"^\d{1,3}(\.\d{1,2})?(px|pt)$")
_LH = re.compile(r"^(\d(\.\d{1,2})?|\d{2,3}%|\d{1,3}(\.\d{1,2})?(px|pt))$")
_COLOR = re.compile(r"^(#[0-9a-f]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*[\d.]+\s*)?\))$")
_SKIP = {"script", "style", "head", "title", "iframe", "object", "embed", "svg", "math"}
# block tags kept: paragraph, two heading levels, pull quote, bullet list (everything else is flattened)
_BLOCK = {"p": "p", "div": "p", "h1": "h2", "h2": "h2", "h3": "h3", "h4": "h3", "h5": "h3", "h6": "h3",
          "blockquote": "blockquote"}
_INLINE = {"b": "b", "strong": "strong", "i": "i", "em": "em", "u": "u", "s": "s", "strike": "s", "del": "s",
           "span": "span", "font": "span"}


def _style(raw):
    keep = []
    for part in (raw or "").split(";"):
        if ":" not in part:
            continue
        k, v = (x.strip().lower() for x in part.split(":", 1))
        ok = False
        if k == "text-align":
            ok = v in ("left", "right", "center", "justify")
        elif k in ("margin-left", "text-indent", "margin-top", "margin-bottom"):
            ok = bool(_LEN.match(v)) or v == "0"
            v = "0" if v == "0" else v
        elif k == "line-height":
            ok = bool(_LH.match(v))
        elif k == "color":
            ok = bool(_COLOR.match(v))
        elif k == "font-weight":
            ok = v in ("bold", "normal") or bool(re.match(r"^[1-9]00$", v))
        elif k == "font-style":
            ok = v in ("italic", "normal")
        elif k in ("text-decoration", "text-decoration-line"):
            ok = v.startswith("underline") or v.startswith("line-through") or v == "none"
            v = "underline" if v.startswith("underline") else ("line-through" if v.startswith("line-through") else "none")
            k = "text-decoration"
        if ok:
            keep.append(f"{k}:{v}")
    return ";".join(keep)


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def _close_all(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")

    def _pop_to(self, tag):
        if tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def _open(self, tag, style=""):
        self.out.append(f"<{tag}" + (f' style="{style}"' if style else "") + ">")
        self.stack.append(tag)

    def _need_block(self):
        if not self.stack:
            self._open("p")
        elif self.stack[-1] == "ul":
            self._open("li")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        a = dict(attrs)
        style = _style(a.get("style"))
        if tag in _BLOCK:
            if "li" in self.stack and tag in ("p", "div"):
                return                                   # a paragraph inside a list item just flows into it
            self._close_all()
            self._open(_BLOCK[tag], style)
        elif tag in ("ul", "ol"):
            self._close_all()
            self._open("ul", style)
        elif tag == "li":
            if "li" in self.stack:
                self._pop_to("li")
            elif self.stack[:1] != ["ul"]:
                self._close_all()
                self._open("ul")
            self._open("li", style)
        elif tag == "br":
            self._need_block()
            self.out.append("<br>")
        elif tag in _INLINE:
            self._need_block()
            if tag == "font" and a.get("color"):
                style = _style(f"color:{a['color']}") or style
            self._open(_INLINE[tag], style)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in _BLOCK:
            if "li" in self.stack and tag in ("p", "div"):
                return
            self._close_all()
        elif tag in ("ul", "ol"):
            self._pop_to("ul")
        elif tag == "li":
            self._pop_to("li")
        elif tag in _INLINE:
            want = _INLINE[tag]
            if want in self.stack[1:]:
                self._pop_to(want)

    def handle_data(self, data):
        if self.skip:
            return
        data = data.replace("\r", "").replace("\n", " ").replace(" ", " ")
        if not data or (not data.strip() and not self.stack):
            return
        self._need_block()
        self.out.append(escape(data, quote=False))


def clean(html):
    """Sanitized HTML body (no marker)."""
    p = _Clean()
    p.feed(html or "")
    p.close()
    p._close_all()
    return "".join(p.out)


def seal(html):
    """What gets stored: the marker plus clean HTML."""
    return RT + clean(html[len(RT):] if html.startswith(RT) else html)


def html_of(text):
    """The HTML to show for a stored rich article (cleaned again, to be safe)."""
    return clean((text or "")[len(RT):])


def to_plain(text):
    """Plain text of a stored article (either format), for excerpts, word counts and empty checks."""
    if is_rich(text):
        body = clean(text[len(RT):])
        body = re.sub(r"</(?:p|h2|h3|blockquote|li)>|<br>", "\n", body)
        return unescape(re.sub(r"<[^>]*>", "", body))
    return re.sub(r"\*+", "", text or "")
