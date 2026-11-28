"""Roles, permissions, approval chain and login/role decorators."""
from functools import wraps

from flask import abort, redirect, session, url_for

ADMIN = "admin"
ADVISER = "adviser"
EIC = "editor_in_chief"
NEWS_EDITOR = "news_editor"
STAFF = "staff"

ROLES = [ADMIN, ADVISER, EIC, NEWS_EDITOR, STAFF]
ROLE_LABELS = {
    ADMIN: "Admin",
    ADVISER: "Adviser",
    EIC: "Editor-in-Chief",
    NEWS_EDITOR: "Editor",
    STAFF: "Staff",
}
# Roles an admin can hand out from the Admin Panel (Admin is NOT assignable)
ASSIGNABLE_ROLES = [ADVISER, EIC, NEWS_EDITOR, STAFF]
# Editorial beat / position, grouped by department (shown as a dropdown with department headings)
POSITION_GROUPS = [
    ("Editorial Board", ["Editor-in-Chief", "Managing Editor", "Associate Editor"]),
    ("Writing Department", ["News Writer", "Feature Writer", "Editorial Writer", "Sports Writer",
                           "Literary Writer", "Copyreader"]),
    ("Creatives Department", ["Layout Artist", "Photojournalist", "Illustrator/Cartoonist"]),
    ("Multimedia Department", ["Broadcaster", "Technical", "Scriptwriter"]),
]
POSITIONS = [p for _dept, _items in POSITION_GROUPS for p in _items]

# posts.status enum:
# draft, pending, with_eic, with_adviser, approved, rejected, revise
STATUS_LABELS = {
    "draft": "Draft",
    "pending": "Pending: Editor",
    "with_eic": "Pending: Editor-in-Chief",
    "with_adviser": "Pending: Adviser",
    "approved": "Approved",
    "rejected": "Rejected",
    "revise": "Needs Revision",
}
# Statuses where the author can still edit (and resubmit)
EDITABLE_STATUSES = ("draft", "revise")

# ---- approval chain: Staff -> News Editor -> Editor-in-Chief -> Adviser ----
STAGE_REVIEWER = {            # status -> role that must act on it
    "pending": NEWS_EDITOR,
    "with_eic": EIC,
    "with_adviser": ADVISER,
}
NEXT_STATUS = {               # what "Approve" does at each stage
    "pending": "with_eic",
    "with_eic": "with_adviser",
    "with_adviser": "approved",
}
APPROVE_LABELS = {
    "pending": "Approve",
    "with_eic": "Approve",
    "with_adviser": "Approve (Final)",
}
ACTION_LABELS = {"approve": "Approve", "revise": "Revise", "reject": "Reject"}

# Who may do what
REVIEWERS = {ADMIN, NEWS_EDITOR, EIC, ADVISER}   # may open the Approvals dashboard
SEE_ALL_POSTS = REVIEWERS                        # may open any *submitted* output
ASSIGN_TASKS = {EIC, ADVISER}                    # the Admin doesn't assign tasks
WORKERS = {EIC, NEWS_EDITOR, STAFF}              # roles that write / submit works and have their own tasks (the Adviser only reviews)
ANNOUNCE = {ADVISER, EIC}                        # only they may post announcements (Admin can't)
MANAGE_USERS = {ADMIN}

# What each reviewer sees on the Approvals Dashboard (and may open): only the works
# that have reached THEIR stage, i.e. that the previous approver already approved.
# Admin sees every stage.
#   News Editor     -> pending       (submitted by Staff)
#   Editor-in-Chief -> with_eic      (approved by the News Editor)
#   Adviser         -> with_adviser  (approved by the Editor-in-Chief)
VISIBLE_STAGES = {
    NEWS_EDITOR: ("pending",),
    EIC: ("with_eic",),
    ADVISER: ("with_adviser",),
}


def visible_stages(role):
    """Post statuses this role may see in the approval chain."""
    if role == ADMIN:
        return tuple(STAGE_REVIEWER)
    return VISIBLE_STAGES.get(role, ())


def can_act(role, post, user_id):
    """May this user approve / revise / reject this post right now?

    Only the reviewer for the post's current stage can act. The Admin oversees
    the system but is view-only: no approving, revising or rejecting.
    Nobody reviews their own work.
    """
    if post["user_id"] == user_id:
        return False
    stage_role = STAGE_REVIEWER.get(post["status"])
    return bool(stage_role) and role == stage_role


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*allowed):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in allowed:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
