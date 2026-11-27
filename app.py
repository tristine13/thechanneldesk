"""The Channel Desk (Flask + MySQL).

Roles: Admin, Adviser, Editor-in-Chief, Editor, Staff.
Run:   flask --app app run --debug
"""
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from html import escape

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.utils import secure_filename

load_dotenv()

from db import close_db, execute, query  # noqa: E402
from mailer import email_html, send_mail  # noqa: E402
import richtext  # noqa: E402
from roles import (ACTION_LABELS, ADMIN, ADVISER, ANNOUNCE, APPROVE_LABELS,  # noqa: E402
                   ASSIGN_TASKS, ASSIGNABLE_ROLES, EDITABLE_STATUSES, EIC,
                   NEWS_EDITOR, NEXT_STATUS, POSITIONS, REVIEWERS, ROLE_LABELS,
                   ROLES, SEE_ALL_POSTS, STAFF, STAGE_REVIEWER, STATUS_LABELS, WORKERS,
                   can_act, login_required, roles_required, visible_stages)

app = Flask(__name__)
# behind Vercel's proxy: trust X-Forwarded-* so Google sign-in builds https:// callback URLs
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
# its own cookie name: the student site also runs on 127.0.0.1 and browsers share cookies across ports,
# so a shared name made each site overwrite the other's session ("Invalid CSRF token")
app.config["SESSION_COOKIE_NAME"] = "channeldesk_staff"
app.config["SESSION_COOKIE_SECURE"] = bool(os.getenv("VERCEL"))
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.teardown_appcontext(close_db)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

# type of work: used for tasks.type and for new posts.type
# (same sections as the student news site; old "Column" posts show under Opinion there)
TASK_TYPES = ["News", "Editorial", "Opinion", "Feature", "Literary", "Sci-Tech", "Sports", "Lokal"]
# one pastel palette (fill, text) for every Desk tag, shared by all screens (same as the Timeline)
CAT_COLORS = {
    "News": ["#FFE2DD", "#992B1B"], "Editorial": ["#EAE4F2", "#5B2C83"], "Opinion": ["#FDECC8", "#9A6700"],
    "Feature": ["#FADEC9", "#8F471A"], "Literary": ["#F5E0E9", "#852952"], "Sci-Tech": ["#D3E5EF", "#1D5B79"],
    "Sports": ["#DBEDDB", "#1E6034"], "Lokal": ["#E3E2E0", "#37352F"], "Other": ["#EFEFED", "#37352F"],
}
app.jinja_env.globals["CAT_COLORS"] = CAT_COLORS
POST_TYPES = TASK_TYPES

# one spelling per desk: "news", "Sci Tech", "Op/Ed", "Column" ... all map onto the 8 official labels
_TYPE_KEYS = {re.sub(r"[^a-z]", "", t.lower()): t for t in TASK_TYPES}
_TYPE_KEYS.update({"column": "Opinion", "oped": "Opinion", "opinions": "Opinion", "editorials": "Editorial",
                   "features": "Feature", "sport": "Sports", "local": "Lokal", "science": "Sci-Tech",
                   "sciencetech": "Sci-Tech", "scienceandtech": "Sci-Tech", "tech": "Sci-Tech"})


def canon_type(t):
    """The official label for a type, or None when it is not one of the 8 desks."""
    return _TYPE_KEYS.get(re.sub(r"[^a-z]", "", (t or "").lower()))

oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ---------------------------------------------------------------- helpers
# notifications.reference_type is an ENUM('task','post','submission','general')
def link_for(ref_type, ref_id, kind=None):
    """Where the button inside an email / notification should go."""
    if ref_type == "post" and ref_id:
        return f"/post/{ref_id}"
    if ref_type == "task":
        return "/tasks"
    return {"registration": "/admin", "announcement": "/announcements"}.get(kind, "/dashboard")


def notify(user_id, message, ref_type="general", ref_id=None, subject=None,
           detail=None, email=True, kind="message", from_user=None):
    """In-app notification (saved in `notifications`) + optional email."""
    if ref_type not in ("task", "post", "submission", "general"):
        ref_type = "general"
    title = subject or "The Channel Desk notification"
    execute(
        "INSERT INTO notifications (user_id, from_user_id, type, title, message, "
        "reference_type, reference_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (user_id, from_user, kind, title[:255], message, ref_type, ref_id),
    )
    if not email:
        return
    u = query("SELECT email FROM users WHERE id = %s", (user_id,), one=True)
    if u and u["email"]:
        body = f"<p>{escape(message)}</p>"
        if detail:
            body += (f'<p style="background:#f6f6f6;border-left:4px solid #7a0000;'
                     f'padding:10px 14px"><b>Feedback:</b><br>{escape(detail)}</p>')
        send_mail(u["email"], title,
                  email_html(title, body, "Open The Channel Desk",
                             link_for(ref_type, ref_id, kind)))


def notify_role(role, message, ref_type="general", ref_id=None, subject=None,
                exclude=None, kind="message", from_user=None):
    for u in query("SELECT id FROM users WHERE role = %s", (role,)):
        if u["id"] != exclude:
            notify(u["id"], message, ref_type, ref_id, subject, kind=kind,
                   from_user=from_user)


def notify_status(post, status, reviewer_id=None, reviewer_label=None, feedback=None):
    """Tell the author their output changed status (in-app + email)."""
    label = STATUS_LABELS[status]
    msg = f"Your output “{post['title']}” is now {label}."
    if reviewer_label:
        msg += f" Reviewed by the {reviewer_label}."
    notify(post["user_id"], msg, "post", post["id"],
           subject=f"Your output is {label}: {post['title']}", detail=feedback,
           kind="submission", from_user=reviewer_id)


# Announcements count only when they come from the Adviser or Editor-in-Chief
ANNOUNCER_ONLY = (" AND (type <> 'announcement' OR from_user_id IN "
                  "(SELECT id FROM users WHERE role IN ('adviser','editor_in_chief')))")


def unread_count(user_id):
    row = query("SELECT COUNT(*) AS total FROM notifications WHERE user_id = %s AND is_read = 0"
                + ANNOUNCER_ONLY, (user_id,), one=True)
    return row["total"]


def approval_count():
    """How many works are waiting for the logged-in reviewer (sidebar badge)."""
    if "user_id" not in session or session.get("role") not in REVIEWERS:
        return 0
    role = session["role"]
    stages = [s for s, r in STAGE_REVIEWER.items() if role == ADMIN or r == role]
    marks = ",".join(["%s"] * len(stages))
    row = query(f"SELECT COUNT(*) AS n FROM posts WHERE status IN ({marks}) AND user_id <> %s",
                (*stages, session["user_id"]), one=True)
    return row["n"]


def save_images(post_id, files):
    files = [f for f in files if f and f.filename][:1]      # one image per article
    if files and "." in files[0].filename and files[0].filename.rsplit(".", 1)[-1].lower() in ALLOWED_EXT:
        for old in query("SELECT image FROM post_images WHERE post_id = %s", (post_id,)):   # a new picture replaces the old one
            try:
                os.remove(os.path.join(app.config["UPLOAD_FOLDER"], old["image"]))
            except OSError:
                pass
        execute("DELETE FROM post_images WHERE post_id = %s", (post_id,))
    for f in files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_EXT:
            flash(f"Skipped {f.filename}: unsupported file type.", "error")
            continue
        name = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
        f.save(os.path.join(app.config["UPLOAD_FOLDER"], name))
        # the DB trigger `after_post_image_insert` keeps posts.image_count in sync
        execute("INSERT INTO post_images (post_id, image) VALUES (%s, %s)", (post_id, name))


def images_for(post):
    imgs = [r["image"] for r in query(
        "SELECT image FROM post_images WHERE post_id = %s ORDER BY id", (post["id"],))]
    if post.get("image"):  # legacy single-image column on posts
        imgs.insert(0, post["image"])
    return imgs


# The post's own status (posts.status) is the single source of truth.
# submissions only stores the review trail (reviewer + feedback).
POST_SELECT = (
    "SELECT p.*, s.feedback, s.reviewed_by, COALESCE(r.name, u.name, u.email) AS author "
    "FROM posts p "
    "LEFT JOIN submissions s ON s.post_id = p.id "
    "LEFT JOIN users u ON u.id = p.user_id "
    "LEFT JOIN registrations r ON r.email = u.email "
)


def can_view(post):
    uid, role = session["user_id"], session["role"]
    if post["user_id"] == uid:
        return True                      # authors always see their own work
    if post["status"] == "draft":
        return False                     # drafts are private to the author
    if post["status"] == "approved":
        return True                      # approved work is public (Timeline)
    if role == ADMIN:
        return True                      # the Admin oversees every submitted work, whatever its stage
    # in-review work: the reviewer whose stage it is sitting at
    if post["status"] in visible_stages(role):
        return True
    # a work you were notified about stays viewable after it moves on, is sent back or rejected,
    # so the "View work" button in Notifications never leads to a dead end
    return bool(query("SELECT id FROM notifications WHERE user_id=%s "
                      "AND reference_type IN ('post','submission') AND reference_id=%s LIMIT 1",
                      (uid, post["id"]), one=True))


def sync_submission(post_id, status, reviewer_id=None, feedback=None):
    """Keep the `submissions` review-trail row in step with the post."""
    try:
        row = query("SELECT id FROM submissions WHERE post_id=%s", (post_id,), one=True)
        if row:
            execute("UPDATE submissions SET status=%s, reviewed_by=%s, feedback=%s WHERE post_id=%s",
                    (status, reviewer_id, feedback, post_id))
        else:
            execute("INSERT INTO submissions (post_id, status, reviewed_by, feedback) "
                    "VALUES (%s,%s,%s,%s)", (post_id, status, reviewer_id, feedback))
    except Exception as exc:       # the trail is only a log: it must never block the submission itself
        print(f"[channeldesk] submissions trail not updated ({status}): {exc}")


def get_my_task(task_id):
    """A not-yet-completed task assigned to the logged-in user (or None)."""
    if not task_id:
        return None
    return query(
        "SELECT t.* FROM tasks t JOIN task_assignments ta ON ta.task_id = t.id "
        "WHERE t.id = %s AND ta.user_id = %s AND t.status <> 'completed'",
        (task_id, session["user_id"]), one=True)


# ------------------------------------------------------------ request hooks
_picture_ready = False


def ensure_user_picture():
    """users.picture holds the member's Google profile picture (a URL); added the first time it is needed."""
    global _picture_ready
    if _picture_ready:
        return
    try:
        has = query("SELECT COUNT(*) AS n FROM information_schema.columns WHERE table_schema = DATABASE() "
                    "AND table_name = 'users' AND column_name = 'picture'", one=True)
        if not has["n"]:
            execute("ALTER TABLE users ADD COLUMN picture VARCHAR(500) NULL")
            print("[channeldesk] added users.picture (Google profile pictures)")
        _picture_ready = True
    except Exception as exc:                     # shown in the terminal so it is never silent
        print(f"[channeldesk] could not add users.picture: {exc}")


_approved_ready = False


def ensure_approved_at():
    """posts.approved_at = when the Adviser gave the final approval (the Timeline shows this, not the creation time).
    Added the first time it is needed; works approved before it existed get the time of their "Approved"
    notification, or keep showing their creation time if that cannot be found."""
    global _approved_ready
    if _approved_ready:
        return
    try:
        has = query("SELECT COUNT(*) AS n FROM information_schema.columns WHERE table_schema = DATABASE() "
                    "AND table_name = 'posts' AND column_name = 'approved_at'", one=True)
        if not has["n"]:
            execute("ALTER TABLE posts ADD COLUMN approved_at DATETIME NULL")
            print("[channeldesk] added posts.approved_at")
        _approved_ready = True                   # the column exists: new approvals are stamped from now on
    except Exception as exc:
        print(f"[channeldesk] could not add posts.approved_at: {exc}")
        return
    try:                                         # older approvals: use the time of their "Approved" notification
        execute("UPDATE posts SET approved_at = (SELECT MAX(n.created_at) FROM notifications n "
                "WHERE n.reference_type = 'post' AND n.reference_id = posts.id AND n.user_id = posts.user_id "
                "AND n.message LIKE %s) WHERE status = 'approved' AND approved_at IS NULL",
                ("%is now Approved%",))
    except Exception as exc:
        print(f"[channeldesk] could not fill approved_at for older works: {exc}")


_post_cols_ready = False


def ensure_post_columns():
    """posts.deck (the short summary under the headline) and posts.image_caption (the photo's caption)."""
    global _post_cols_ready
    if _post_cols_ready:
        return
    try:
        have = {r["cn"].lower() for r in query(
            "SELECT column_name AS cn FROM information_schema.columns WHERE table_schema = DATABASE() "
            "AND table_name = 'posts' AND column_name IN ('deck', 'image_caption', 'archived_at')")}
        if "archived_at" not in have:
            execute("ALTER TABLE posts ADD COLUMN archived_at DATETIME NULL")
            print("[channeldesk] added posts.archived_at")
        if "deck" not in have:
            execute("ALTER TABLE posts ADD COLUMN deck VARCHAR(300) NULL")
            print("[channeldesk] added posts.deck")
        if "image_caption" not in have:
            execute("ALTER TABLE posts ADD COLUMN image_caption VARCHAR(300) NULL")
            print("[channeldesk] added posts.image_caption")
        _post_cols_ready = True
    except Exception as exc:
        print(f"[channeldesk] could not add posts.deck / image_caption: {exc}")


_types_ready = False


def ensure_type_labels():
    """Once per run: rewrite odd spellings of a desk in posts.type / tasks.type to the official label,
    so the Timeline, the lists, the approvals and the student site all show (and filter by) the same tag."""
    global _types_ready
    if _types_ready:
        return
    _types_ready = True
    for table in ("posts", "tasks"):
        try:
            for row in query(f"SELECT DISTINCT type FROM {table}"):
                official = canon_type(row["type"])
                if official and official != row["type"]:
                    execute(f"UPDATE {table} SET type = %s WHERE type = %s", (official, row["type"]))
                    print(f"[channeldesk] {table}.type '{row['type']}' -> '{official}'")
        except Exception as exc:
            print(f"[channeldesk] could not tidy {table}.type: {exc}")


@app.before_request
def csrf_and_session_refresh():
    ensure_user_picture()
    ensure_type_labels()
    ensure_approved_at()
    ensure_post_columns()
    if request.method == "POST":
        token = session.get("csrf")
        if not token or token != request.form.get("csrf_token"):
            abort(400, "Invalid CSRF token")
    if "user_id" in session:  # always trust the DB for the role
        cols = "id, role, picture" if _picture_ready else "id, role"
        u = query(f"SELECT {cols} FROM users WHERE id = %s", (session["user_id"],), one=True)
        if not u:
            session.clear()
        else:
            session["role"] = u["role"]
            session["picture"] = u.get("picture") or ""


@app.context_processor
def inject_globals():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if "user_id" in session:
        try:
            poll_tick()          # sends the "poll opened / closed" notifications once their time has come
        except Exception:
            pass                 # never let a poll problem break a page
    count = unread_count(session["user_id"]) if "user_id" in session else 0
    return dict(csrf_token=session["csrf"], ROLE_LABELS=ROLE_LABELS, notif_count=count,
                approval_count=approval_count(),
                APPROVE_LABELS=APPROVE_LABELS,
                SEE_ALL_POSTS=SEE_ALL_POSTS, ASSIGN_TASKS=ASSIGN_TASKS, ANNOUNCE=ANNOUNCE,
                REVIEWERS=REVIEWERS, WORKERS=WORKERS, ADMIN=ADMIN, ROLES=ROLES,
                ASSIGNABLE_ROLES=ASSIGNABLE_ROLES, EDITABLE_STATUSES=EDITABLE_STATUSES,
                ACTION_LABELS=ACTION_LABELS, STATUS_LABELS=STATUS_LABELS)


# ---- article formatting: **bold** and *italic* markers, shown as real bold / italic ----
_BOTH = re.compile(r"\*\*\*(.+?)\*\*\*", re.S)
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITAL = re.compile(r"(?<!\*)\*(?!\s|\*)(.+?)(?<![\s*])\*(?!\*)", re.S)


@app.template_filter("rich")
def rich_filter(text):
    if richtext.is_rich(text):                    # written in the Word-style editor (already cleaned)
        return Markup(richtext.html_of(text))
    html = _BOTH.sub(r"<strong><em>\1</em></strong>", escape(text or ""))
    return Markup(_ITAL.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", html)))


def editor_html(content):
    """An article as HTML for the Word-style editor (older plain-text articles become one paragraph per line)."""
    if richtext.is_rich(content):
        return richtext.html_of(content)
    out = []
    for line in (content or "").replace("\r\n", "\n").split("\n"):
        html = _BOTH.sub(r"<b><i>\1</i></b>", escape(line))
        html = _ITAL.sub(r"<i>\1</i>", _BOLD.sub(r"<b>\1</b>", html))
        out.append(f"<p>{html or '<br>'}</p>")
    return "".join(out)


@app.template_filter("plain")
def plain_filter(text):
    """The text without the bold / italic markers (for excerpts)."""
    return richtext.to_plain(text)


@app.after_request
def no_page_cache(resp):
    """Never keep pages in the browser's back/forward cache, so going back shows fresh data
    (e.g. Notifications reflects the work you just read, and the unread badge is right)."""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, msg="You don't have access to that page."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, msg="Page not found."), 404


# ------------------------------------------------------------------- public
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        position = request.form["position"].strip()
        if position not in POSITIONS or not name or not email:
            flash("Please fill out every field.", "error")
            return redirect(url_for("register"))

        existing = query("SELECT is_verified FROM registrations WHERE email = %s", (email,), one=True)
        if existing:
            if existing["is_verified"]:
                flash("That email is already registered. Sign in with Google.", "info")
            else:
                flash("That email is already registered and waiting for admin approval.", "info")
            return redirect(url_for("index"))

        execute("INSERT INTO registrations (name, email, position) VALUES (%s,%s,%s)",
                (name, email, position))
        session.pop("google_email", None)
        session.pop("google_name", None)
        send_mail(email, "We received your registration",
                  email_html("Registration received",
                             f"<p>Hi {escape(name)}, thanks for registering as <b>{escape(position)}</b>. "
                             "An admin will review your account. You'll get another email as soon as "
                             "it's approved.</p>"))
        notify_role(ADMIN, f"New registration awaiting approval: {name} ({position})",
                    "general", None, subject="New registration awaiting approval",
                    kind="registration")
        flash("Registered! Check your email. An admin must approve you before you can sign in.", "info")
        return redirect(url_for("index"))

    prefill = {"name": session.get("google_name", ""), "email": session.get("google_email", "")}
    return render_template("register.html", positions=POSITIONS, prefill=prefill)


@app.route("/login")
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if not os.getenv("GOOGLE_CLIENT_ID"):
        flash("Google sign-in isn't configured (set GOOGLE_CLIENT_ID / SECRET in .env).", "error")
        return redirect(url_for("index"))
    return oauth.google.authorize_redirect(url_for("auth_callback", _external=True))


@app.route("/auth/callback")
def auth_callback():
    token = oauth.google.authorize_access_token()
    info = token.get("userinfo") or {}
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified", True):
        flash("Google did not return a verified email.", "error")
        return redirect(url_for("index"))

    reg = query("SELECT * FROM registrations WHERE email = %s", (email,), one=True)
    if not reg:
        session["google_email"] = email
        session["google_name"] = info.get("name", "")
        flash(f"We couldn't find an account for {email}. Please register first.", "error")
        return redirect(url_for("register"))
    if not reg["is_verified"]:
        flash("Your registration is still waiting for admin approval.", "info")
        return redirect(url_for("index"))

    user = query("SELECT * FROM users WHERE email = %s", (email,), one=True)
    if not user:
        uid = execute("INSERT INTO users (email, role, name, google_id) VALUES (%s,%s,%s,%s)",
                      (email, STAFF, reg["name"], info.get("sub")))
        user = {"id": uid, "role": STAFF}
    elif not user.get("google_id") and info.get("sub"):
        execute("UPDATE users SET google_id=%s WHERE id=%s", (info["sub"], user["id"]))

    picture = info.get("picture") or ""          # the member's Google profile picture
    print(f"[channeldesk] sign-in {email}: Google picture {'received' if picture else 'NOT received'}")
    if picture and _picture_ready:
        execute("UPDATE users SET picture=%s WHERE id=%s", (picture, user["id"]))
    session.clear()
    session.update(user_id=user["id"], email=email, name=reg["name"],
                   role=user["role"], position=reg["position"], picture=picture,
                   csrf=secrets.token_hex(16))
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/uploads/<path:filename>")
@login_required
def uploads(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/brand/logo.png")
def brand_logo():
    """The publication's logo (also accepts the name channellogo.png.png that Windows can create)."""
    folder = os.path.join(app.root_path, "static", "images")
    for name in ("channellogo.png", "channellogo.png.png", "channellogo.jpg"):
        if os.path.exists(os.path.join(folder, name)):
            return send_from_directory(folder, name)
    abort(404)


# ---------------------------------------------------------------- dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    """Timeline: every APPROVED output, newest first. Drafts never appear here."""
    order = "COALESCE(p.approved_at, p.created_at)" if _approved_ready else "p.created_at"
    live = " AND p.archived_at IS NULL" if _post_cols_ready else ""      # archived works leave the Timeline
    posts = query(POST_SELECT + f"WHERE p.status = 'approved'{live} ORDER BY {order} DESC")
    pics = {}
    try:                                     # the writers' Google profile pictures (if the column exists yet)
        uids = sorted({p["user_id"] for p in posts if p.get("user_id")})
        if uids and _picture_ready:
            marks = ",".join(["%s"] * len(uids))
            pics = {u["id"]: u["picture"] for u in query(
                f"SELECT id, picture FROM users WHERE id IN ({marks}) AND picture IS NOT NULL AND picture <> ''", uids)}
    except Exception:
        pics = {}
    for p in posts:
        p["images"] = images_for(p)
        p["shown_at"] = p.get("approved_at") or p["created_at"]          # when it was approved, not when it was written
        p["cat"] = canon_type(p["type"]) or "Other"          # same label everywhere; never guessed as News
        plain = richtext.to_plain(p["content"]).replace("\r", "")
        flat = " ".join(plain.split())
        p["excerpt"] = flat if len(flat) <= 220 else flat[:220].rsplit(" ", 1)[0] + "..."
        if p["cat"] == "Literary":            # an opening stanza keeps its line breaks
            lines = [ln.strip() for ln in plain.split("\n") if ln.strip()][:4]
            p["quote"] = "\n".join(lines)[:220]
        else:
            p["quote"] = flat if len(flat) <= 170 else flat[:170].rsplit(" ", 1)[0] + "..."
        p["pic"] = pics.get(p.get("user_id"))
        p["search"] = f"{p['title']} {p.get('author') or ''} {p['cat']} {flat[:500]}".lower()
    parts = session["name"].split(" ", 1)
    cats = list(TASK_TYPES) + (["Other"] if any(p["cat"] == "Other" for p in posts) else [])
    return render_template("dashboard.html", posts=posts, categories=cats, first=parts[0],
                           last=parts[1] if len(parts) > 1 else "")


# -------------------------------------------------------------------- posts
@app.route("/create", methods=["GET", "POST"])
@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@roles_required(*WORKERS)
def create(post_id=None):
    post = None
    if post_id:
        post = query(POST_SELECT + "WHERE p.id = %s", (post_id,), one=True)
        if not post or post["user_id"] != session["user_id"]:
            abort(403)
        if post["status"] not in EDITABLE_STATUSES:
            flash("This output is already in review and can't be edited.", "error")
            return redirect(url_for("view_post", post_id=post_id))

    # optional: opened from the Tasks page via the Edit button
    task = get_my_task(request.values.get("task_id", type=int))

    # older posts may carry a legacy type (e.g. "Op/Ed"); keep it selectable
    types = list(POST_TYPES)
    if post and post["type"] not in types:
        types.append(post["type"])

    if request.method == "POST":
        title = " ".join(request.form["title"].split())
        deck = " ".join(request.form.get("deck", "").split())[:300]
        caption = " ".join(request.form.get("image_caption", "").split())[:300]
        content = request.form["content"].strip()
        if richtext.is_rich(content):                  # keep only safe tags / styles
            content = richtext.seal(content)
            if not richtext.to_plain(content).strip():
                content = ""
        ptype = request.form.get("type", "News")
        if ptype not in types:
            ptype = "News"
        submit = request.form.get("action") == "submit"
        if submit and (not title or not content):      # a draft may be saved half-written; a submission may not
            flash("Title and content are required to submit.", "error")
            return redirect(request.url)
        if not submit and not title:
            title = "Untitled draft"
        if not submit and not content:
            content = ""

        # a submitted work enters the chain one step past its author:
        # Staff -> Editor, Editor -> Editor-in-Chief, Editor-in-Chief -> Adviser
        entry = {NEWS_EDITOR: "with_eic", EIC: "with_adviser"}.get(session["role"], "pending")
        status = entry if submit else ("revise" if post and post["status"] == "revise" else "draft")
        if post:
            execute("UPDATE posts SET title=%s, type=%s, content=%s, status=%s WHERE id=%s",
                    (title, ptype, content, status, post_id))
        else:
            post_id = execute(
                "INSERT INTO posts (user_id, title, type, content, status) VALUES (%s,%s,%s,%s,%s)",
                (session["user_id"], title, ptype, content, status))
        save_images(post_id, request.files.getlist("images"))
        if _post_cols_ready:
            execute("UPDATE posts SET deck=%s, image_caption=%s WHERE id=%s", (deck or None, caption or None, post_id))

        # keep the task in step with the work being done on it
        if task:
            execute("UPDATE tasks SET status=%s WHERE id=%s",
                    ("submitted" if submit else "in_progress", task["id"]))
            if submit:
                notify(task["created_by"], f"{session['name']} submitted: {task['title']}",
                       "task", task["id"], subject=f"Task submitted: {task['title']}",
                       kind="task", from_user=session["user_id"])

        if submit:
            sync_submission(post_id, entry)            # starts the approval chain
            try:                                       # stamp the moment of submission (Approvals lists newest first)
                execute("UPDATE submissions SET submitted_at=NOW() WHERE post_id=%s", (post_id,))
            except Exception as exc:
                print(f"[channeldesk] submitted_at not stamped: {exc}")
            saved = {"id": post_id, "title": title, "user_id": session["user_id"]}
            notify_status(saved, entry)                # confirmation to the author
            # first stop of the chain for this author: Editor, Editor-in-Chief or Adviser
            notify_role(STAGE_REVIEWER[entry],
                        f"{session['name']} submitted “{title}” ({ptype}) and it awaits your approval.",
                        "post", post_id, subject=f"Awaiting your approval: {title}",
                        exclude=session["user_id"], kind="submission",
                        from_user=session["user_id"])
            # email + in-app notification to every admin
            notify_role(ADMIN,
                        f"{session['name']} submitted “{title}” ({ptype}) for review.",
                        "post", post_id, subject=f"New work submitted: {title}",
                        exclude=session["user_id"], kind="submission",
                        from_user=session["user_id"])
            flash(f"Submitted for review. Status: {STATUS_LABELS[entry]}.", "info")
            return redirect(url_for("submitted"))
        flash("Draft saved.", "info")
        return redirect(url_for("drafts"))

    return render_template("create.html", post=post, post_types=types, task=task,
                           images=images_for(post) if post else [],
                           initial_html=editor_html(post["content"]) if post else "")


def attach_submit_time(rows):
    """sort_at = when the work was (last) submitted; falls back to when it was created."""
    try:
        times = {r["post_id"]: r["submitted_at"] for r in query("SELECT post_id, submitted_at FROM submissions")}
    except Exception:
        times = {}
    for r in rows:
        r["sort_at"] = times.get(r["id"]) or r["created_at"]
    return rows


def mark_today(rows):
    """Flag each row made today (the lists group by Today / Earlier)."""
    today = date.today()
    for r in rows:
        r["is_today"] = (r.get("sort_at") or r["created_at"]).date() == today
    return rows


@app.route("/drafts")
@roles_required(*WORKERS)
def drafts():
    """Only the author's own drafts (status = 'draft')."""
    rows = query(POST_SELECT + "WHERE p.user_id = %s AND p.status = 'draft' "
                 "ORDER BY p.created_at DESC", (session["user_id"],))
    return render_template("post_list.html", title="Drafts", posts=mark_today(rows),
                           empty="No drafts yet.")


@app.route("/submitted")
@roles_required(*WORKERS)
def submitted():
    """The author's own work that has left the draft stage."""
    rows = query(POST_SELECT + "WHERE p.user_id = %s AND p.status <> 'draft' "
                 "ORDER BY p.created_at DESC", (session["user_id"],))
    return render_template("post_list.html", title="Submitted", posts=mark_today(rows),
                           empty="Nothing submitted yet.")


# Spelling & grammar check for the article editor. Uses LanguageTool (free, no account). The text is sent
# to that service; to keep it private, run your own LanguageTool server and set LANGUAGETOOL_URL in .env.
LANGUAGETOOL_URL = os.getenv("LANGUAGETOOL_URL", "https://api.languagetool.org/v2/check")
LANGUAGETOOL_LANG = os.getenv("LANGUAGETOOL_LANG", "en-US")


@app.route("/api/grammar", methods=["POST"])
@roles_required(*WORKERS)
def grammar_check():
    text = request.form.get("text", "").replace("\r\n", "\n")[:20000]      # browsers send line breaks as CRLF, which shifted every mark
    if not text.strip():
        return jsonify(matches=[])
    data = urllib.parse.urlencode({"text": text, "language": LANGUAGETOOL_LANG}).encode()
    try:
        req = urllib.request.Request(LANGUAGETOOL_URL, data=data, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return jsonify(error="The checker could not be reached. Check your internet connection and try again."), 502
    matches = []
    for m in payload.get("matches", []):
        matches.append({
            "offset": m["offset"], "length": m["length"], "message": m.get("message", ""),
            "kind": (m.get("rule") or {}).get("issueType", ""),
            "replacements": [r["value"] for r in m.get("replacements", [])[:4]],
        })
    return jsonify(matches=matches)


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@roles_required(*WORKERS)
def delete_draft(post_id):
    """An author can throw away their own draft (never a work that has been submitted)."""
    post = query("SELECT id, user_id, status FROM posts WHERE id = %s", (post_id,), one=True)
    if not post or post["user_id"] != session["user_id"]:
        abort(404)
    if post["status"] != "draft":
        flash("Only drafts can be deleted.", "error")
        return redirect(url_for("submitted"))
    for row in query("SELECT image FROM post_images WHERE post_id = %s", (post_id,)):
        try:
            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], row["image"]))
        except OSError:
            pass
    execute("DELETE FROM post_images WHERE post_id = %s", (post_id,))
    execute("DELETE FROM submissions WHERE post_id = %s", (post_id,))
    execute("DELETE FROM notifications WHERE reference_type IN ('post','submission') AND reference_id = %s",
            (post_id,))
    execute("DELETE FROM posts WHERE id = %s", (post_id,))
    flash("Draft deleted.", "info")
    return redirect(url_for("drafts"))


STAGES = [("pending", "Awaiting Editor"),
          ("with_eic", "Awaiting Editor-in-Chief"),
          ("with_adviser", "Awaiting Adviser")]


@app.route("/review")
@roles_required(*REVIEWERS)
def review_queue():
    """Approvals dashboard. Each reviewer only sees the stages of the approval chain
    that concern them (see roles.VISIBLE_STAGES); the Admin sees everything."""
    role, uid = session["role"], session["user_id"]
    is_admin = role == ADMIN
    stages = [s for s in STAGES if s[0] in visible_stages(role)]
    shown = [k for k, _ in stages]

    ensure_post_columns()                        # makes sure posts.archived_at exists before it is used below
    can_archive = role in (ADVISER, EIC, ADMIN)
    live = " AND p.archived_at IS NULL" if _post_cols_ready else ""
    if is_admin:
        posts = query(POST_SELECT + f"WHERE p.status <> 'draft'{live} ORDER BY p.created_at")
    else:
        marks = ",".join(["%s"] * len(shown))
        posts = query(POST_SELECT + f"WHERE p.status IN ({marks}) ORDER BY p.created_at",
                      tuple(shown))
        if can_archive:                          # the Adviser / Editor-in-Chief also see the approved works
            posts += query(POST_SELECT + f"WHERE p.status = 'approved'{live} ORDER BY p.created_at")
    archived = []
    if can_archive and _post_cols_ready:
        archived = query(POST_SELECT + "WHERE p.archived_at IS NOT NULL ORDER BY p.archived_at DESC")
        for p in archived:
            p["can_act"] = False
        mark_today(archived)
    for p in posts:
        p["can_act"] = can_act(role, p, uid)
    attach_submit_time(posts)                    # newest submission first, even if the draft is older
    mark_today(posts)

    by_status = {}
    for p in posts:
        by_status.setdefault(p["status"], []).append(p)

    mine = [k for k, _ in stages if STAGE_REVIEWER[k] == role]
    order = sorted(stages, key=lambda s: 0 if s[0] in mine else 1)   # your stage first
    sections = [(k, label, by_status.get(k, [])) for k, label in order]

    counts = {k: len(by_status.get(k, [])) for k in
              ("pending", "with_eic", "with_adviser", "approved", "revise", "rejected")}

    def latest(status):
        return sorted(by_status.get(status, []), key=lambda p: p["created_at"], reverse=True)[:15]

    counts["archived"] = len(archived)
    # Revise / Rejected history is for the Admin only; Approved works for whoever can archive them
    outcomes = []
    if is_admin:
        outcomes += [("revise", "Needs Revision (sent back to the author)", latest("revise")),
                     ("rejected", "Rejected", latest("rejected"))]
    if can_archive:
        outcomes.append(("approved", "Approved", sorted(by_status.get("approved", []),
                                                         key=lambda p: p["created_at"], reverse=True)))

    waiting = sum(1 for p in posts if p["can_act"])
    return render_template("approvals.html", sections=sections, outcomes=outcomes, archived=archived,
                           counts=counts, waiting=waiting, stages=stages,
                           show_outcomes=is_admin, can_archive=can_archive and _post_cols_ready)


@app.route("/post/<int:post_id>/archive", methods=["POST"])
@roles_required(ADVISER, EIC, ADMIN)
def archive_work(post_id):
    """Archive an approved work: it leaves the Timeline and the student site but is kept (Approvals > Archive)."""
    ensure_post_columns()
    post = query("SELECT id, title, status FROM posts WHERE id = %s", (post_id,), one=True)
    if not post:
        abort(404)
    if not _post_cols_ready:
        flash("Archiving is not ready yet. Check the terminal for the database message.", "error")
    elif post["status"] != "approved":
        flash("Only approved works can be archived.", "error")
    else:
        execute("UPDATE posts SET archived_at = NOW() WHERE id = %s", (post_id,))
        flash(f"Archived “{post['title']}”. It no longer shows on the Timeline or the student site.", "info")
    return redirect(url_for("review_queue"))


@app.route("/post/<int:post_id>/unarchive", methods=["POST"])
@roles_required(ADVISER, EIC, ADMIN)
def unarchive_work(post_id):
    """Bring an archived work back to the Timeline and the student site."""
    ensure_post_columns()
    post = query("SELECT id, title FROM posts WHERE id = %s", (post_id,), one=True)
    if not post:
        abort(404)
    if _post_cols_ready:
        execute("UPDATE posts SET archived_at = NULL WHERE id = %s", (post_id,))
        flash(f"Restored “{post['title']}”. It is published again.", "info")
    return redirect(url_for("review_queue"))

@app.route("/post/<int:post_id>")
@login_required
def view_post(post_id):
    post = query(POST_SELECT + "WHERE p.id = %s", (post_id,), one=True)
    if not post or not can_view(post):
        abort(404)
    post["images"] = images_for(post)
    # opening a work counts as reading its notifications
    execute("UPDATE notifications SET is_read=1 WHERE user_id=%s "
            "AND reference_type IN ('post','submission') AND reference_id=%s",
            (session["user_id"], post_id))
    can_review = can_act(session["role"], post, session["user_id"])
    can_edit = (post["user_id"] == session["user_id"]
                and post["status"] in EDITABLE_STATUSES)
    ensure_post_columns()
    can_archive = session["role"] in (ADVISER, EIC, ADMIN) and _post_cols_ready
    return render_template("view_post.html", post=post, can_review=can_review, can_edit=can_edit,
                           can_archive=can_archive,
                           approve_label=APPROVE_LABELS.get(post["status"], "Approve"))


@app.route("/post/<int:post_id>/review", methods=["POST"])
@roles_required(*REVIEWERS)
def review_post(post_id):
    post = query(POST_SELECT + "WHERE p.id = %s", (post_id,), one=True)
    if not post:
        abort(404)
    if post["user_id"] == session["user_id"]:
        flash("You can't review your own work.", "error")
        return redirect(url_for("review_queue"))
    if not can_act(session["role"], post, session["user_id"]):
        flash("That output isn't waiting for your approval.", "error")
        return redirect(url_for("review_queue"))

    action = request.form.get("action", "")
    if action == "approve":
        new_status = NEXT_STATUS[post["status"]]          # move one step along the chain
    elif action == "revise":
        new_status = "revise"
    elif action == "reject":
        new_status = "rejected"
    else:
        abort(400)

    feedback = request.form.get("feedback", "").strip() or None
    back = request.form.get("next") or url_for("review_queue")
    if new_status in ("revise", "rejected") and not feedback:
        flash("Please write feedback so the author knows why.", "error")
        return redirect(back if back.startswith("/") else url_for("review_queue"))

    if new_status == "approved":                 # stamp the moment of the Adviser's final approval
        ensure_approved_at()
        try:
            execute("UPDATE posts SET status=%s, approved_at=NOW() WHERE id=%s", (new_status, post_id))
        except Exception as exc:
            print(f"[channeldesk] could not save approved_at: {exc}")
            execute("UPDATE posts SET status=%s WHERE id=%s", (new_status, post_id))
    else:
        execute("UPDATE posts SET status=%s WHERE id=%s", (new_status, post_id))
    sync_submission(post_id, new_status, session["user_id"], feedback)
    reviewer = ROLE_LABELS[session["role"]]
    notify_status(post, new_status, session["user_id"], reviewer, feedback)   # tell the author

    next_role = STAGE_REVIEWER.get(new_status)                                 # tell the next approver
    if next_role:
        notify_role(next_role,
                    f"“{post['title']}” was approved by the {reviewer} and awaits your approval.",
                    "post", post_id, subject=f"Awaiting your approval: {post['title']}",
                    exclude=post["user_id"], kind="submission", from_user=session["user_id"])
    flash(f"“{post['title']}” is now: {STATUS_LABELS[new_status]}.", "info")
    return redirect(url_for("review_queue"))


# -------------------------------------------------------------------- tasks
# tasks(id, title, description, type, created_by, status, created_at)
# task_assignments(task_id, user_id)  <- who the task is assigned to
TASK_STATUSES = ("pending", "in_progress", "submitted", "completed")
TASK_LABELS = {"pending": "To do", "in_progress": "In progress",
               "submitted": "Submitted", "completed": "Completed"}


# The Tasks menu is split into four pages (sidebar dropdown):
#   /tasks           My Tasks                  (everyone)
#   /tasks/assign    Assign a Task             (ASSIGN_TASKS)
#   /tasks/assigned  Tasks I Assigned          (ASSIGN_TASKS)
#   /tasks/all       All Tasks (Admin overview) (admin)
@app.route("/tasks")
@roles_required(*WORKERS)
def my_tasks():
    uid = session["user_id"]
    execute("UPDATE notifications SET is_read=1 WHERE user_id=%s AND reference_type='task'", (uid,))
    mine = query(
        "SELECT t.*, COALESCE(r.name, cu.name, cu.email) AS creator "
        "FROM tasks t "
        "JOIN task_assignments ta ON ta.task_id = t.id "
        "JOIN users cu ON cu.id = t.created_by "
        "LEFT JOIN registrations r ON r.email = cu.email "
        "WHERE ta.user_id = %s ORDER BY t.status = 'completed', t.created_at DESC", (uid,))
    return render_template("tasks.html", mine=mark_today(mine), TASK_STATUSES=TASK_STATUSES,
                           TASK_LABELS=TASK_LABELS)


@app.route("/tasks/assign", methods=["GET", "POST"])
@roles_required(*ASSIGN_TASKS)
def assign_task():
    uid = session["user_id"]
    if request.method == "POST":  # create + assign
        title = request.form["title"].strip()
        desc = request.form.get("description", "").strip()
        ttype = request.form.get("type", "News")
        if ttype not in TASK_TYPES:
            ttype = "News"
        ids = [int(x) for x in request.form.getlist("assigned_to") if x.isdigit()]
        people = [r["id"] for r in query(
            "SELECT id FROM users WHERE id IN (%s)" % ",".join(["%s"] * len(ids)), ids)] if ids else []
        if not title or not people:
            flash("Please enter a title and pick at least one assignee.", "error")
            return redirect(url_for("assign_task"))
        tid = execute("INSERT INTO tasks (title, description, type, created_by) VALUES (%s,%s,%s,%s)",
                      (title, desc, ttype, uid))
        for person in people:
            execute("INSERT INTO task_assignments (task_id, user_id) VALUES (%s,%s)", (tid, person))
            notify(person, f"New {ttype} task from {session['name']}: {title}", "task", tid,
                   subject=f"New task assigned: {title}", kind="task", from_user=uid)
        flash("Task assigned.", "info")
        return redirect(url_for("assigned_tasks"))

    staff = query("SELECT u.id, COALESCE(r.name, u.name, u.email) AS name, r.position "
                  "FROM users u JOIN registrations r ON r.email = u.email "
                  "WHERE r.is_verified = 1 AND u.id <> %s ORDER BY name", (uid,))
    return render_template("assign_task.html", staff=staff, task_types=TASK_TYPES)


@app.route("/tasks/all")
@roles_required(ADMIN)
def admin_tasks():
    # admin oversees every task in the system
    rows = query(
        "SELECT t.*, MAX(COALESCE(cr.name, cu.name, cu.email)) AS creator, "
        "GROUP_CONCAT(COALESCE(r.name, au.name, au.email) SEPARATOR ', ') AS assignee "
        "FROM tasks t JOIN users cu ON cu.id = t.created_by "
        "LEFT JOIN registrations cr ON cr.email = cu.email "
        "LEFT JOIN task_assignments ta ON ta.task_id = t.id "
        "LEFT JOIN users au ON au.id = ta.user_id "
        "LEFT JOIN registrations r ON r.email = au.email "
        "GROUP BY t.id ORDER BY t.status = 'completed', t.created_at DESC")
    return render_template("all_tasks.html", all_tasks=mark_today(rows), TASK_LABELS=TASK_LABELS)


@app.route("/tasks/assigned")
@roles_required(*ASSIGN_TASKS)
def assigned_tasks():
    uid = session["user_id"]
    assigned = query(
        "SELECT t.*, GROUP_CONCAT(COALESCE(r.name, au.name, au.email) SEPARATOR ', ') AS assignee "
        "FROM tasks t "
        "LEFT JOIN task_assignments ta ON ta.task_id = t.id "
        "LEFT JOIN users au ON au.id = ta.user_id "
        "LEFT JOIN registrations r ON r.email = au.email "
        "WHERE t.created_by = %s GROUP BY t.id "
        "ORDER BY t.status = 'completed', t.created_at DESC", (uid,))
    return render_template("assigned_tasks.html", assigned=mark_today(assigned), TASK_LABELS=TASK_LABELS)


@app.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def task_status(task_id):
    status = request.form.get("status")
    if status not in TASK_STATUSES:
        abort(400)
    uid = session["user_id"]
    task = query("SELECT * FROM tasks WHERE id=%s", (task_id,), one=True)
    if not task:
        abort(404)
    is_assignee = query("SELECT id FROM task_assignments WHERE task_id=%s AND user_id=%s",
                        (task_id, uid), one=True)
    is_creator = task["created_by"] == uid          # the Admin is view-only, so no admin override
    # assignees move it forward; only the creator can mark it completed
    if status == "completed" and not is_creator:
        abort(403)
    if not (is_assignee or is_creator):
        abort(403)
    execute("UPDATE tasks SET status=%s WHERE id=%s", (status, task_id))
    if status == "submitted" and not is_creator:
        notify(task["created_by"], f"{session['name']} submitted: {task['title']}", "task",
               task_id, subject=f"Task submitted: {task['title']}", kind="task", from_user=uid)
    back = request.form.get("next", "")          # return to the page the user was on
    if not back.startswith("/") or back.startswith("//"):
        back = url_for("my_tasks")
    return redirect(back)


# ------------------------------------------------------------ announcements
@app.route("/announcements", methods=["GET", "POST"])
@login_required
def announcements():
    if request.method == "POST":
        if session["role"] not in ANNOUNCE:
            abort(403)
        title = request.form["title"].strip()
        body = request.form["body"].strip()
        if not title or not body:
            flash("Title and message are required.", "error")
            return redirect(url_for("announcements"))
        execute("INSERT INTO announcements (title, body, created_by) VALUES (%s,%s,%s)",
                (title, body, session["user_id"]))
        people = query("SELECT u.id, u.email FROM users u JOIN registrations r ON r.email=u.email "
                       "WHERE r.is_verified=1 AND u.id<>%s", (session["user_id"],))
        for p in people:
            notify(p["id"], f"Announcement: {title}", "general", None,
                   subject=f"Announcement: {title}", email=False, kind="announcement",
                   from_user=session["user_id"])
        html = email_html(f"Announcement: {title}",
                          f"<p style='white-space:pre-wrap'>{escape(body)}</p>"
                          f"<p style='color:#888'>Posted by {escape(session['name'])}</p>",
                          "Open The Channel Desk", "/announcements")
        send_mail([p["email"] for p in people], f"Announcement: {title}", html)
        flash(f"Announcement posted and emailed to {len(people)} people.", "info")
        return redirect(url_for("announcements"))

    execute("UPDATE notifications SET is_read=1 WHERE user_id=%s AND type='announcement'",
            (session["user_id"],))                      # viewing the page = reading them
    rows = query("SELECT a.*, COALESCE(r.name, u.name, u.email) AS author FROM announcements a "
                 "LEFT JOIN users u ON u.id=a.created_by "
                 "LEFT JOIN registrations r ON r.email=u.email "
                 "WHERE u.role IN ('adviser','editor_in_chief') "
                 "ORDER BY a.created_at DESC LIMIT 50")
    return render_template("announcements.html", rows=rows)


# ------------------------------------------------------------ notifications
ALERT_KINDS = ("submission", "task", "registration")      # the "Alerts" tab: things that need action


@app.template_filter("ago")
def ago_filter(dt):
    secs = max(0, int((datetime.now() - dt).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = (date.today() - dt.date()).days
    if days == 1:
        return "Yesterday"
    return f"{days} days ago" if days < 7 else dt.strftime("%b %d, %Y")


def notif_target(n):
    """(url, button label) for where a notification leads, or (None, None)."""
    if n["reference_type"] == "post" and n["reference_id"]:
        return url_for("view_post", post_id=n["reference_id"]), "View work"
    if n["reference_type"] == "task":
        # the Adviser has no tasks of their own, only the ones they assigned
        return url_for("my_tasks" if session.get("role") in WORKERS else "assigned_tasks"), "View task"
    if n["type"] == "announcement":
        return url_for("announcements"), "View"
    if n["type"] == "registration":
        return url_for("admin"), "Review"
    if (n["title"] or "").startswith("Poll "):
        return url_for("polls"), "View poll"
    return None, None


@app.route("/notifications")
@login_required
def notifications():
    """Nothing is marked read just by opening the page: use the buttons (or open an item)."""
    rows = query("SELECT * FROM notifications WHERE user_id=%s" + ANNOUNCER_ONLY +
                 " ORDER BY created_at DESC LIMIT 100", (session["user_id"],))
    today = date.today()
    for n in rows:
        n["href"], n["action"] = notif_target(n)
        n["is_alert"] = n["type"] in ALERT_KINDS
        n["today"] = n["created_at"].date() == today
    unread = [n for n in rows if not n["is_read"]]
    attention = [n for n in unread if n["is_alert"]]
    return render_template("notifications.html", rows=rows, unread=len(unread),
                           attention=len(attention))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (session["user_id"],))
    return redirect(url_for("notifications"))


@app.route("/notifications/<int:notif_id>/open")
@login_required
def notification_open(notif_id):
    """Mark one notification read, then go where it points."""
    n = query("SELECT * FROM notifications WHERE id=%s AND user_id=%s",
              (notif_id, session["user_id"]), one=True)
    if not n:
        abort(404)
    execute("UPDATE notifications SET is_read=1 WHERE id=%s", (notif_id,))
    return redirect(notif_target(n)[0] or url_for("notifications"))


# -------------------------------------------------------------------- polls
# Campus Pulse (shown on the student site). Managed by the Adviser and Editor-in-Chief; the Admin can view.
# polls(id, question, options JSON, starts_at, ends_at, created_by, notified_open, notified_close)
# poll_vote_counts(poll_id, option_idx, n) is written by the student site.
POLL_MANAGERS = ANNOUNCE
_polls_ready = False
_last_tick = 0.0


def ensure_poll_tables():
    global _polls_ready
    if _polls_ready:
        return
    execute("CREATE TABLE IF NOT EXISTS polls (id INT AUTO_INCREMENT PRIMARY KEY, question VARCHAR(255) NOT NULL, "
            "options TEXT NOT NULL, starts_at DATETIME NOT NULL, ends_at DATETIME NOT NULL, created_by INT NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, notified_open TINYINT(1) NOT NULL DEFAULT 0, "
            "notified_close TINYINT(1) NOT NULL DEFAULT 0)")
    execute("CREATE TABLE IF NOT EXISTS poll_vote_counts (poll_id INT NOT NULL, option_idx INT NOT NULL, "
            "n INT NOT NULL DEFAULT 0, PRIMARY KEY (poll_id, option_idx))")
    _polls_ready = True


def fmt_dt(d):
    return d.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")


def poll_phase(p, now=None):
    now = now or datetime.now()
    if p["ends_at"] <= now:
        return "closed"
    return "open" if p["starts_at"] <= now else "upcoming"


def poll_results(p):
    """([{label, n, pct}], total votes) for one poll."""
    labels = json.loads(p["options"])
    votes = {r["option_idx"]: r["n"] for r in query(
        "SELECT option_idx, n FROM poll_vote_counts WHERE poll_id=%s", (p["id"],))}
    total = sum(votes.get(i, 0) for i in range(len(labels)))
    rows = [{"label": lbl, "n": votes.get(i, 0),
             "pct": round(100 * votes.get(i, 0) / total) if total else 0}
            for i, lbl in enumerate(labels)]
    return rows, total


def announce_poll_open(p):
    """Everyone with an account (staff included) hears that voting is open."""
    people = query("SELECT u.id, u.email FROM users u JOIN registrations r ON r.email = u.email "
                   "WHERE r.is_verified = 1 AND u.id <> %s", (p["created_by"],))
    subject = f"Poll opened: {p['question']}"[:255]
    msg = f"Campus Pulse is open: “{p['question']}” Voting closes {fmt_dt(p['ends_at'])}."
    for u in people:
        notify(u["id"], msg, "general", None, subject=subject, email=False, kind="message",
               from_user=p["created_by"])
    emails = [u["email"] for u in people if u["email"]]
    if emails:
        send_mail(emails, subject, email_html("Campus Pulse is open",
                  f"<p><b>{escape(p['question'])}</b></p><p>Voting closes {escape(fmt_dt(p['ends_at']))}.</p>",
                  "Open The Channel Desk", "/polls"))


def announce_poll_close(p):
    """The Adviser, Editor-in-Chief and Admin get the final result."""
    rows, total = poll_results(p)
    msg = f"Campus Pulse closed: “{p['question']}” {total} vote{'' if total == 1 else 's'}."
    if total:
        top = max(rows, key=lambda r: r["n"])
        msg += f" Leading: {top['label']} ({top['pct']}%)."
    for u in query("SELECT id FROM users WHERE role IN ('adviser','editor_in_chief','admin')"):
        notify(u["id"], msg, "general", None, subject=f"Poll closed: {p['question']}"[:255],
               kind="message", from_user=p["created_by"])


def poll_tick(force=False):
    """No scheduler runs in the background, so due notifications are sent the next time anyone uses the
    staff app (checked at most every 30 seconds)."""
    global _last_tick
    if not force and time.time() - _last_tick < 30:
        return
    _last_tick = time.time()
    ensure_poll_tables()
    now = datetime.now()
    for p in query("SELECT * FROM polls WHERE notified_open = 0 AND starts_at <= %s", (now,)):
        execute("UPDATE polls SET notified_open = 1 WHERE id = %s", (p["id"],))
        if p["ends_at"] > now:                      # no "opened" message for a poll that is already over
            announce_poll_open(p)
    for p in query("SELECT * FROM polls WHERE notified_close = 0 AND ends_at <= %s", (now,)):
        execute("UPDATE polls SET notified_close = 1, notified_open = 1 WHERE id = %s", (p["id"],))
        announce_poll_close(p)


def parse_local(value):
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime((value or "").strip(), fmt)
        except ValueError:
            pass
    return None


@app.route("/polls")
@roles_required(ADVISER, EIC, ADMIN)
def polls():
    ensure_poll_tables()
    now = datetime.now()
    rows = query("SELECT p.*, COALESCE(r.name, u.name, u.email) AS creator FROM polls p "
                 "LEFT JOIN users u ON u.id = p.created_by LEFT JOIN registrations r ON r.email = u.email "
                 "ORDER BY p.starts_at DESC")
    groups = {"open": [], "upcoming": [], "closed": []}
    for p in rows:
        p["phase"] = poll_phase(p, now)
        p["labels"] = json.loads(p["options"])
        p["results"], p["total"] = poll_results(p)
        p["starts"], p["ends"] = fmt_dt(p["starts_at"]), fmt_dt(p["ends_at"])
        groups[p["phase"]].append(p)
    soon = now.replace(second=0, microsecond=0)
    return render_template("polls.html", groups=groups, total=len(rows),
                           can_manage=session["role"] in POLL_MANAGERS,
                           start_default=soon.strftime("%Y-%m-%dT%H:%M"),
                           end_default=(soon + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"))


@app.route("/polls/new", methods=["POST"])
@roles_required(*POLL_MANAGERS)
def poll_create():
    ensure_poll_tables()
    question = " ".join(request.form.get("question", "").split())
    options = []
    for o in request.form.getlist("option"):
        o = " ".join(o.split())
        if o and o.lower() not in [x.lower() for x in options]:
            options.append(o)
    now = datetime.now()
    starts = parse_local(request.form.get("starts_at")) or now
    ends = parse_local(request.form.get("ends_at"))
    error = None
    if not question or len(question) > 255:
        error = "Write the poll question (up to 255 characters)."
    elif not 2 <= len(options) <= 6 or any(len(o) > 80 for o in options):
        error = "Give 2 to 6 different options (up to 80 characters each)."
    elif not ends:
        error = "Pick a closing date and time."
    elif ends <= starts:
        error = "The closing date must be after the start date."
    elif ends <= now:
        error = "The closing date must be in the future."
    if error:
        flash(error, "error")
        return redirect(url_for("polls"))
    execute("INSERT INTO polls (question, options, starts_at, ends_at, created_by) VALUES (%s,%s,%s,%s,%s)",
            (question, json.dumps(options), starts, ends, session["user_id"]))
    poll_tick(force=True)                        # if it starts now, tell everyone right away
    flash("Poll created." + (" Voting opens " + fmt_dt(starts) + "." if starts > now else " Voting is open."), "info")
    return redirect(url_for("polls"))


@app.route("/polls/<int:poll_id>/close", methods=["POST"])
@roles_required(*POLL_MANAGERS)
def poll_close(poll_id):
    """Close an open poll now (the result is kept), or cancel one that hasn't started."""
    ensure_poll_tables()
    p = query("SELECT * FROM polls WHERE id = %s", (poll_id,), one=True)
    if not p:
        abort(404)
    phase = poll_phase(p)
    if phase == "upcoming":
        execute("DELETE FROM polls WHERE id = %s", (poll_id,))
        flash("Poll cancelled.", "info")
    elif phase == "open":
        execute("UPDATE polls SET ends_at = %s WHERE id = %s", (datetime.now(), poll_id))
        poll_tick(force=True)
        flash("Poll closed. The result stays on the student site.", "info")
    return redirect(url_for("polls"))


# -------------------------------------------------------------------- admin
# roles an admin can give a new account, in the order shown in the create drawer
ROLE_CARDS = [
    (STAFF, "Writes drafts, submits outputs and works on assigned tasks."),
    (NEWS_EDITOR, "First-stage reviewer: approves, sends back or rejects submitted outputs."),
    (EIC, "Second-stage reviewer. Also assigns tasks and posts announcements."),
    (ADVISER, "Final approver before publishing. Also assigns tasks and posts announcements."),
]
POSITION_SUGGESTIONS = ["Writer", "Cartoonist", "Layout Artist", "Sports Journalist", "Photojournalist"]
INSTITUTION_DOMAIN = "@fbc.ph.education"


def admin_context(**extra):
    """Everything admin.html needs (also used when the create drawer re-opens with errors)."""
    pending = query("SELECT r.id AS reg_id, r.name, r.email, r.position FROM registrations r "
                    "LEFT JOIN users u ON u.email = r.email "
                    "WHERE r.is_verified = 0 AND (u.role IS NULL OR u.role <> 'admin') "
                    "ORDER BY r.created_at DESC")
    members = query("SELECT r.id AS reg_id, r.name, r.email, r.position, u.id AS user_id, u.role, u.picture "
                    "FROM registrations r JOIN users u ON u.email = r.email "
                    "WHERE r.is_verified = 1 AND u.role <> 'admin' ORDER BY r.name")
    post_counts = {r["status"]: r["n"] for r in
                   query("SELECT status, COUNT(*) AS n FROM posts GROUP BY status")}
    task_counts = {r["status"]: r["n"] for r in
                   query("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
    ctx = dict(pending=pending, members=members, post_counts=post_counts,
               task_counts=task_counts, role_cards=ROLE_CARDS,
               position_suggestions=POSITION_SUGGESTIONS, domain=INSTITUTION_DOMAIN,
               form={}, errors={}, open_drawer=False)
    ctx.update(extra)
    return ctx


@app.route("/admin")
@roles_required(ADMIN)
def admin():
    return render_template("admin.html", **admin_context())


@app.route("/admin/create", methods=["POST"])
@roles_required(ADMIN)
def admin_create():
    """Admin creates an already-approved account and picks its role (create drawer)."""
    name = " ".join(request.form.get("name", "").split())
    email = request.form.get("email", "").strip().lower()
    position = " ".join(request.form.get("position", "").split())
    role = request.form.get("role", STAFF)
    send = request.form.get("send_email") == "on"
    if role not in ASSIGNABLE_ROLES:           # Admin can't be handed out
        role = STAFF

    errors = {}
    if not name:
        errors["name"] = "Enter the member's full name."
    elif len(name) > 190:
        errors["name"] = "That name is too long."
    if not re.fullmatch(r"[^\s@]+" + re.escape(INSTITUTION_DOMAIN), email):
        errors["email"] = f"Use an institutional address ending in {INSTITUTION_DOMAIN}."
    elif (query("SELECT id FROM registrations WHERE email=%s", (email,), one=True)
          or query("SELECT id FROM users WHERE email=%s", (email,), one=True)):
        errors["email"] = "An account with this email already exists (or is under Pending Approvals)."
    if not position:
        errors["position"] = "Enter or pick a position."
    elif len(position) > 60:
        errors["position"] = "Keep the position under 60 characters."

    if errors:   # re-open the drawer with what was typed and the messages
        return render_template("admin.html", **admin_context(
            open_drawer=True, errors=errors,
            form={"name": name, "email": email, "position": position, "role": role,
                  "send": send}))

    if role == ADVISER:
        position = "Adviser"                  # the adviser is not a student: no beat / student position
    execute("INSERT INTO registrations (name, email, position, is_verified) VALUES (%s,%s,%s,1)",
            (name, email, position))
    uid = execute("INSERT INTO users (email, role, name) VALUES (%s,%s,%s)",
                  (email, role, name))
    notify(uid, f"An admin created your account. You can now sign in with Google as "
                f"{ROLE_LABELS[role]}.",
           "general", None, subject="Your Channel Desk account is ready", email=send,
           kind="role", from_user=session["user_id"])
    flash(f"Account created for {name} as {ROLE_LABELS[role]}."
          + (" A sign-in email was sent." if send else ""), "info")
    return redirect(url_for("admin"))


@app.route("/admin/verify/<int:reg_id>", methods=["POST"])
@roles_required(ADMIN)
def admin_verify(reg_id):
    reg = query("SELECT * FROM registrations WHERE id=%s", (reg_id,), one=True)
    if not reg:
        abort(404)
    role = request.form.get("role", STAFF)
    if role not in ASSIGNABLE_ROLES:
        role = STAFF
    execute("UPDATE registrations SET is_verified=1 WHERE id=%s", (reg_id,))
    if role == ADVISER:
        execute("UPDATE registrations SET position='Adviser' WHERE id=%s", (reg_id,))
    user = query("SELECT id, role FROM users WHERE email=%s", (reg["email"],), one=True)
    if not user:
        uid = execute("INSERT INTO users (email, role, name) VALUES (%s,%s,%s)",
                      (reg["email"], role, reg["name"]))
    else:
        uid = user["id"]
        if user["role"] != ADMIN:
            execute("UPDATE users SET role=%s, name=COALESCE(name, %s) WHERE id=%s",
                    (role, reg["name"], uid))
    notify(uid, f"Your registration was approved. You can now sign in as {ROLE_LABELS[role]}.",
           "general", None, subject="Your registration was approved", kind="role",
           from_user=session["user_id"])
    flash(f"{reg['name']} approved as {ROLE_LABELS[role]}.", "info")
    return redirect(url_for("admin"))


@app.route("/admin/role/<int:user_id>", methods=["POST"])
@roles_required(ADMIN)
def admin_role(user_id):
    role = request.form.get("role")
    if role not in ASSIGNABLE_ROLES:
        abort(400)
    target = query("SELECT id, role FROM users WHERE id=%s", (user_id,), one=True)
    if not target or target["role"] == ADMIN:
        abort(403)
    execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id))
    if role == ADVISER:                       # the Adviser's position is simply "Adviser"
        execute("UPDATE registrations SET position='Adviser' WHERE email=(SELECT email FROM users WHERE id=%s)",
                (user_id,))
    notify(user_id, f"Your role is now {ROLE_LABELS[role]}.", "general", None,
           subject="Your role was updated", kind="role", from_user=session["user_id"])
    flash("Role updated.", "info")
    return redirect(url_for("admin"))


@app.route("/admin/delete/<int:reg_id>", methods=["POST"])
@roles_required(ADMIN)
def admin_delete(reg_id):
    reg = query("SELECT * FROM registrations WHERE id=%s", (reg_id,), one=True)
    if not reg:
        abort(404)
    user = query("SELECT role FROM users WHERE email=%s", (reg["email"],), one=True)
    if reg["email"] == session["email"] or (user and user["role"] == ADMIN):
        flash("Admin accounts can't be removed here.", "error")
        return redirect(url_for("admin"))
    if not reg["is_verified"]:
        send_mail(reg["email"], "Your registration was not approved",
                  email_html("Registration not approved",
                             f"<p>Hi {escape(reg['name'])}, your registration to The Channel Desk "
                             "was not approved. Please contact your adviser or the admin.</p>"))
    execute("DELETE FROM users WHERE email=%s", (reg["email"],))
    execute("DELETE FROM registrations WHERE id=%s", (reg_id,))
    flash("Account removed.", "info")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(debug=True)
