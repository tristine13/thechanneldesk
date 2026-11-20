# The Channel Desk (Flask)

Python port of the PHP app. Five roles:

| Role | Can do |
|---|---|
| **Admin** | Approves registrations, changes roles, removes accounts, sees all tasks, and can act at any approval stage. Can create accounts and assign roles (Admin itself can't be handed out from the Admin Panel). |
| **Adviser** | Final approver (last stage). Can assign tasks and post announcements. Approvals Dashboard shows only works the Editor-in-Chief has approved. |
| **Editor-in-Chief** | Second approver (can reject with remarks). Can assign tasks and post announcements. Approvals Dashboard shows only works the News Editor has approved. |
| **News Editor** | First approver (can reject with remarks). Approvals Dashboard shows only newly submitted works waiting for the News Editor. |
| **Staff** | Writes drafts, submits outputs, and works on assigned tasks. Default role for new members. Sees only approved work in the Timeline, and can see the announcements. |

Submission flow: **Draft → Pending (News Editor) → Editor-in-Chief → Adviser → Approved**. A reviewer can approve, send back for revision, or reject; remarks are required for revise/reject and are sent to the author.

Reviewers only see a work once the previous approver has approved it, and can only open in-review works at their own stage (see `VISIBLE_STAGES` in `roles.py`); the Admin sees every stage.

## Setup

```bash
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # then fill it in
mysql -u root -P 3307 -h 127.0.0.1 < schema.sql
python add_admin.py "Abigail Patiluna" abby03332@gmail.com
flask --app app run --debug
```

Google OAuth: create an OAuth client at console.cloud.google.com and add
`http://localhost:5000/auth/callback` as an authorized redirect URI.

## Security notes

- **Your Gmail app password was in `mail_config.php`. Treat it as leaked: revoke it** at
  myaccount.google.com/apppasswords, create a new one, and put it only in `.env`.
- `.env` is git-ignored. Never commit it.
- Every POST form carries a CSRF token; roles are re-read from the database on every request.

## PHP → Python map

| PHP | Python |
|---|---|
| `db.php` | `db.py` (PyMySQL) |
| `config.php`, sessions | Flask `session` in `app.py` |
| `mail_config.php` | `mailer.py` (smtplib, credentials from env) |
| `add_user.php` | `add_admin.py` |
| `index.php`, `register*.php`, `dashboard.php`, `logout.php` | routes in `app.py` + `templates/` |
| `pages/*` (create, drafts, submitted, my_tasks, notifications, admin, view_posts, gallery) | rebuilt from how `dashboard.php` used them: you didn't upload these, so compare against your originals |

## Assumptions to check

- The `registrations.role` column (Writer, etc.) became `registrations.position`, and `users.role` now holds the four permission roles.
- Table/column names for `tasks`, `notifications`, `submissions`, `post_images` were inferred from your queries; see `schema.sql`.
