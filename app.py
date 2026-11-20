"""The Channel Desk (Flask + MySQL/MariaDB).

Roles: Admin, Adviser, Editor-in-Chief, News Editor, Staff.
Run:   flask --app app run --debug
"""
import os
import re
import secrets
import uuid
from html import escape

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.utils import secure_filename

load_dotenv()

from db import close_db, execute, query  # noqa: E402
from mailer import email_html, send_mail  # noqa: E402
from roles import (ACTION_LABELS, ADMIN, ADVISER, ANNOUNCE, APPROVE_LABELS,  # noqa: E402
                   ASSIGN_TASKS, ASSIGNABLE_ROLES, EDITABLE_STATUSES, EIC,
                   NEWS_EDITOR, NEXT_STATUS, POSITIONS, REVIEWERS, ROLE_LABELS,
                   ROLES, SEE_ALL_POSTS, STAFF, STAGE_REVIEWER, STATUS_LABELS,
                   can_act, login_required, roles_required, visible_stages)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.teardown_appcontext(close_db)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

# type of work: used for tasks.type and for new posts.type
TASK_TYPES = ["News", "Sports", "Feature", "Column"]
POST_TYPES = TASK_TYPES

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


def unread_count(user_id):
    row = query("SELECT COUNT(*) AS total FROM notifications WHERE user_id = %s AND is_read = 0",
                (user_id,), one=True)
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
    # in-review work: only the reviewers whose stage(s) it is sitting at (Admin: all)
    return post["status"] in visible_stages(role)


def sync_submission(post_id, status, reviewer_id=None, feedback=None):
    """Keep the `submissions` review-trail row in step with the post."""
    row = query("SELECT id FROM submissions WHERE post_id=%s", (post_id,), one=True)
    if row:
        execute("UPDATE submissions SET status=%s, reviewed_by=%s, feedback=%s WHERE post_id=%s",
                (status, reviewer_id, feedback, post_id))
    else:
        execute("INSERT INTO submissions (post_id, status, reviewed_by, feedback) "
                "VALUES (%s,%s,%s,%s)", (post_id, status, reviewer_id, feedback))


def get_my_task(task_id):
    """A not-yet-completed task assigned to the logged-in user (or None)."""
    if not task_id:
        return None
    return query(
        "SELECT t.* FROM tasks t JOIN task_assignments ta ON ta.task_id = t.id "
        "WHERE t.id = %s AND ta.user_id = %s AND t.status <> 'completed'",
        (task_id, session["user_id"]), one=True)


# ------------------------------------------------------------ request hooks
@app.before_request
def csrf_and_session_refresh():
    if request.method == "POST":
        token = session.get("csrf")
        if not token or token != request.form.get("csrf_token"):
            abort(400, "Invalid CSRF token")
    if "user_id" in session:  # always trust the DB for the role
        u = query("SELECT id, role FROM users WHERE id = %s", (session["user_id"],), one=True)
        if not u:
            session.clear()
        else:
            session["role"] = u["role"]


@app.context_processor
def inject_globals():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    count = unread_count(session["user_id"]) if "user_id" in session else 0
    return dict(csrf_token=session["csrf"], ROLE_LABELS=ROLE_LABELS, notif_count=count,
                approval_count=approval_count(),
                APPROVE_LABELS=APPROVE_LABELS,
                SEE_ALL_POSTS=SEE_ALL_POSTS, ASSIGN_TASKS=ASSIGN_TASKS, ANNOUNCE=ANNOUNCE,
                REVIEWERS=REVIEWERS, ADMIN=ADMIN, ROLES=ROLES,
                ASSIGNABLE_ROLES=ASSIGNABLE_ROLES, EDITABLE_STATUSES=EDITABLE_STATUSES,
                ACTION_LABELS=ACTION_LABELS, STATUS_LABELS=STATUS_LABELS)


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

    session.clear()
    session.update(user_id=user["id"], email=email, name=reg["name"],
                   role=user["role"], position=reg["position"],
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


# ---------------------------------------------------------------- dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    """Timeline: every APPROVED output, newest first. Drafts never appear here."""
    posts = query(POST_SELECT + "WHERE p.status = 'approved' ORDER BY p.created_at DESC")
    for p in posts:
        p["images"] = images_for(p)
    parts = session["name"].split(" ", 1)
    return render_template("dashboard.html", posts=posts, first=parts[0],
                           last=parts[1] if len(parts) > 1 else "")


# -------------------------------------------------------------------- posts
@app.route("/create", methods=["GET", "POST"])
@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
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
        title = request.form["title"].strip()
        content = request.form["content"].strip()
        ptype = request.form.get("type", "News")
        if ptype not in types:
            ptype = "News"
        submit = request.form.get("action") == "submit"
        if not title or not content:
            flash("Title and content are required.", "error")
            return redirect(request.url)

        status = "pending" if submit else ("revise" if post and post["status"] == "revise" else "draft")
        if post:
            execute("UPDATE posts SET title=%s, type=%s, content=%s, status=%s WHERE id=%s",
                    (title, ptype, content, status, post_id))
        else:
            post_id = execute(
                "INSERT INTO posts (user_id, title, type, content, status) VALUES (%s,%s,%s,%s,%s)",
                (session["user_id"], title, ptype, content, status))
        save_images(post_id, request.files.getlist("images"))

        # keep the task in step with the work being done on it
        if task:
            execute("UPDATE tasks SET status=%s WHERE id=%s",
                    ("submitted" if submit else "in_progress", task["id"]))
            if submit:
                notify(task["created_by"], f"{session['name']} submitted: {task['title']}",
                       "task", task["id"], subject=f"Task submitted: {task['title']}",
                       kind="task", from_user=session["user_id"])

        if submit:
            sync_submission(post_id, "pending")        # starts the approval chain
            saved = {"id": post_id, "title": title, "user_id": session["user_id"]}
            notify_status(saved, "pending")            # confirmation to the author
            # first stop of the chain: the News Editor(s)
            notify_role(NEWS_EDITOR,
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
            flash("Submitted for review. Status: Pending (News Editor).", "info")
            return redirect(url_for("submitted"))
        flash("Draft saved.", "info")
        return redirect(url_for("drafts"))

    return render_template("create.html", post=post, post_types=types, task=task)


@app.route("/drafts")
@login_required
def drafts():
    """Only the author's own drafts (status = 'draft')."""
    rows = query(POST_SELECT + "WHERE p.user_id = %s AND p.status = 'draft' "
                 "ORDER BY p.created_at DESC", (session["user_id"],))
    return render_template("post_list.html", title="Drafts", posts=rows,
                           empty="No drafts yet.")


@app.route("/submitted")
@login_required
def submitted():
    """The author's own work that has left the draft stage."""
    rows = query(POST_SELECT + "WHERE p.user_id = %s AND p.status <> 'draft' "
                 "ORDER BY p.created_at DESC", (session["user_id"],))
    return render_template("post_list.html", title="Submitted", posts=rows,
                           empty="Nothing submitted yet.")


STAGES = [("pending", "Awaiting News Editor"),
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

    if is_admin:
        posts = query(POST_SELECT + "WHERE p.status <> 'draft' ORDER BY p.created_at")
    else:
        marks = ",".join(["%s"] * len(shown))
        posts = query(POST_SELECT + f"WHERE p.status IN ({marks}) ORDER BY p.created_at",
                      tuple(shown))
    for p in posts:
        p["can_act"] = can_act(role, p, uid)

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

    # Revise / Rejected / Approved history is for the Admin only
    outcomes = []
    if is_admin:
        outcomes = [("revise", "Needs Revision (sent back to the author)", latest("revise")),
                    ("rejected", "Rejected", latest("rejected")),
                    ("approved", "Approved", latest("approved"))]

    waiting = sum(1 for p in posts if p["can_act"])
    return render_template("approvals.html", sections=sections, outcomes=outcomes,
                           counts=counts, waiting=waiting, stages=stages,
                           show_outcomes=is_admin)

@app.route("/post/<int:post_id>")
@login_required
def view_post(post_id):
    post = query(POST_SELECT + "WHERE p.id = %s", (post_id,), one=True)
    if not post or not can_view(post):
        abort(404)
    post["images"] = images_for(post)
    can_review = can_act(session["role"], post, session["user_id"])
    can_edit = (post["user_id"] == session["user_id"]
                and post["status"] in EDITABLE_STATUSES)
    return render_template("view_post.html", post=post, can_review=can_review, can_edit=can_edit,
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
@login_required
def my_tasks():
    uid = session["user_id"]
    mine = query(
        "SELECT t.*, COALESCE(r.name, cu.name, cu.email) AS creator "
        "FROM tasks t "
        "JOIN task_assignments ta ON ta.task_id = t.id "
        "JOIN users cu ON cu.id = t.created_by "
        "LEFT JOIN registrations r ON r.email = cu.email "
        "WHERE ta.user_id = %s ORDER BY t.status = 'completed', t.created_at DESC", (uid,))
    return render_template("tasks.html", mine=mine, TASK_STATUSES=TASK_STATUSES,
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
    return render_template("all_tasks.html", all_tasks=rows, TASK_LABELS=TASK_LABELS)


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
    return render_template("assigned_tasks.html", assigned=assigned, TASK_LABELS=TASK_LABELS)


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
    is_creator = task["created_by"] == uid or session["role"] == ADMIN
    # assignees move it forward; only the creator (or the admin) can mark it completed
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

    rows = query("SELECT a.*, COALESCE(r.name, u.name, u.email) AS author FROM announcements a "
                 "LEFT JOIN users u ON u.id=a.created_by "
                 "LEFT JOIN registrations r ON r.email=u.email "
                 "ORDER BY a.created_at DESC LIMIT 50")
    return render_template("announcements.html", rows=rows)


# ------------------------------------------------------------ notifications
@app.route("/notifications")
@login_required
def notifications():
    uid = session["user_id"]
    rows = query("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 100",
                 (uid,))
    if rows:
        execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (uid,))
    return render_template("notifications.html", rows=rows)


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
    members = query("SELECT r.id AS reg_id, r.name, r.email, r.position, u.id AS user_id, u.role "
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
        errors["position"] = "Enter or pick a beat / position."
    elif len(position) > 60:
        errors["position"] = "Keep the position under 60 characters."

    if errors:   # re-open the drawer with what was typed and the messages
        return render_template("admin.html", **admin_context(
            open_drawer=True, errors=errors,
            form={"name": name, "email": email, "position": position, "role": role,
                  "send": send}))

    execute("INSERT INTO registrations (name, email, position, is_verified) VALUES (%s,%s,%s,1)",
            (name, email, position))
    uid = execute("INSERT INTO users (email, role, name) VALUES (%s,%s,%s)", (email, role, name))
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