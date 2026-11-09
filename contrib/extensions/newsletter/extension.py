"""Newsletter signup: /newsletter/ form, subscriber list, CSV export, site-export support."""
import csv
import datetime
import io
import re

from flask import Response, flash, redirect, request, session
from sqlalchemy import Column, DateTime, Integer, String, Table, insert, select, delete

from data_store import Base, db_manager

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# A Core table (no ORM class), so reloading the extension never redefines a model.
subscribers = Table(
	"ext_newsletter_subscribers", Base.metadata,
	Column("id", Integer, primary_key=True),
	Column("email", String(255), unique=True, nullable=False),
	Column("ip", String(64)),
	Column("created_at", DateTime, default=datetime.datetime.utcnow),
	extend_existing=True,
)


def _csv_safe(value: str) -> str:
	"""Stop spreadsheet programs from running a cell that starts with = + - @."""
	return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def register(api):
	api.create_tables()

	def export_all():
		with db_manager.engine.connect() as conn:
			rows = conn.execute(select(subscribers.c.email, subscribers.c.created_at)).fetchall()
		return [{"email": r[0], "created_at": r[1].isoformat() if r[1] else None} for r in rows]

	def import_all(data, overwrite=False):
		added = 0
		with db_manager.engine.begin() as conn:
			have = {r[0] for r in conn.execute(select(subscribers.c.email))}
			for item in data or []:
				email = str(item.get("email", "")).strip().lower()
				if EMAIL_RE.match(email) and email not in have:
					conn.execute(insert(subscribers).values(email=email))
					have.add(email)
					added += 1
		return added

	api.register_export(export_all, import_all)

	@api.route("/newsletter/", methods=("GET", "POST"))
	def signup():
		done, error = request.args.get("done") == "1", ""
		if request.method == "POST":
			email = (request.form.get("email") or "").strip().lower()
			ip = request.remote_addr or "unknown"
			if request.form.get("website"):  # honeypot
				return redirect("/newsletter/?done=1")
			if not EMAIL_RE.match(email) or len(email) > 255:
				error = "Please enter a valid email address."
			elif len(db_manager.recent_attempts("newsletter", f"ip:{ip}", 600)) >= 5:
				error = "Too many attempts. Please try again later."
			else:
				db_manager.record_attempt("newsletter", f"ip:{ip}")
				with db_manager.engine.begin() as conn:
					if not conn.execute(select(subscribers.c.id).where(subscribers.c.email == email)).first():
						conn.execute(insert(subscribers).values(email=email, ip=ip))
				return redirect("/newsletter/?done=1")
		return api.render("signup.html", page={"title": api.settings.get("heading", "Join our newsletter")},
		                  heading=api.settings.get("heading", "Join our newsletter"),
		                  intro=api.settings.get("intro", ""), done=done, error=error)

	@api.admin_route("/", capability="moderate")
	def admin_list():
		with db_manager.engine.connect() as conn:
			rows = conn.execute(select(subscribers).order_by(subscribers.c.id.desc())).fetchall()
		return api.render("admin_list.html", rows=rows, csrf=session.get("csrf_token", ""))

	@api.admin_route("/export.csv", capability="moderate")
	def export_csv():
		out = io.StringIO()
		w = csv.writer(out)
		w.writerow(["email", "subscribed_at_utc"])
		with db_manager.engine.connect() as conn:
			for r in conn.execute(select(subscribers.c.email, subscribers.c.created_at).order_by(subscribers.c.id)):
				w.writerow([_csv_safe(r[0]), r[1].strftime("%Y-%m-%d %H:%M") if r[1] else ""])
		return Response(out.getvalue(), mimetype="text/csv",
		                headers={"Content-Disposition": "attachment; filename=subscribers.csv"})

	@api.admin_route("/delete/<int:sid>", capability="moderate", methods=("POST",))
	def delete_subscriber(sid):
		with db_manager.engine.begin() as conn:
			conn.execute(delete(subscribers).where(subscribers.c.id == sid))
		flash("Subscriber removed.", "success")
		return redirect("/admin/ext/newsletter/")

	api.add_admin_menu("Newsletter", [{"label": "Subscribers", "url": "/admin/ext/newsletter/"}], capability="moderate")
