"""
Editorial and site features: revisions, autosave, scheduled publishing, the review queue,
the contact form and inbox, comments, redirects, feeds, robots.txt and the extra settings page.

Everything here is one Flask blueprint ("features"). The public routes are /contact,
/post/<slug>/comment, /feed.xml, /category/<slug>/feed.xml and /robots.txt.
"""

import datetime
import difflib
import html
import logging
import re
import time
from xml.sax.saxutils import escape as xml_escape

from flask import (Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup

from auth import can, capability_required, current_user, login_required
from crypto_utils import encrypt_secret
from data_store import (Comment, ContactMessage, Post, Page, Redirect, Revision, User, db_manager,
                        get_site_settings, save_revision, update_site_settings, add_redirect)
from mailer import mail_configured, send_mail
from migrate_utils import sanitize_html

logger = logging.getLogger(__name__)
bp = Blueprint("features", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------- helpers
def _now():
	return datetime.datetime.utcnow()


def _ip() -> str:
	return request.remote_addr or "unknown"


def rate_limited(kind: str, limit: int, window_seconds: int) -> bool:
	"""True if this IP already made `limit` attempts of this kind in the window; otherwise records one."""
	key = f"ip:{_ip()}"
	if len(db_manager.recent_attempts(kind, key, window_seconds)) >= limit:
		return True
	db_manager.record_attempt(kind, key)
	return False


def parse_publish_at(value: str, tz_offset: str = "0"):
	"""'YYYY-MM-DDTHH:MM' typed in the browser's time zone -> naive UTC. Empty/invalid -> None."""
	value = (value or "").strip()
	if not value:
		return None
	try:
		local = datetime.datetime.strptime(value[:16], "%Y-%m-%dT%H:%M")
		offset = int(tz_offset or 0)  # minutes, as JS getTimezoneOffset(): UTC = local + offset
	except ValueError:
		return None
	return local + datetime.timedelta(minutes=offset)


def resolve_status(requested: str, publish_at, can_publish: bool) -> str:
	"""Map the status chosen in a form to what is actually saved.

	published -> published, or scheduled when publish_at is in the future (people who can publish)
	pending   -> pending review (people who cannot publish)
	anything else -> draft
	"""
	if requested in ("published", "scheduled") and can_publish:
		return "scheduled" if publish_at and publish_at > _now() else "published"
	if requested == "pending" and not can_publish:
		return "pending"
	return "draft"


def seo_from_form(form) -> dict:
	def clean(key, limit):
		return (form.get(key) or "").strip()[:limit]
	canonical = clean("seo_canonical", 500)
	if canonical and not re.match(r"^(https?://|/)", canonical):
		canonical = ""
	og = clean("seo_og_image", 500)
	if og and not re.match(r"^(https?://|/)", og):
		og = ""
	return {"title": clean("seo_title", 200), "description": clean("seo_description", 300),
	        "canonical": canonical, "og_image": og}


def publish_fields(form, can_publish: bool):
	"""(status, publish_at, seo) from a post/page form."""
	publish_at = parse_publish_at(form.get("publish_at"), form.get("tz_offset"))
	status = resolve_status(form.get("status", "draft"), publish_at, can_publish)
	if status not in ("scheduled",):
		publish_at = None
	return status, publish_at, seo_from_form(form)


_last_due_check = {"at": 0.0}


def publish_due(force: bool = False) -> int:
	"""Publish scheduled posts/pages whose time has come. Cheap enough to call on requests (runs at most every 30 s)."""
	if not force and time.time() - _last_due_check["at"] < 30:
		return 0
	_last_due_check["at"] = time.time()
	count = 0
	db = db_manager.get_session()
	try:
		now = _now()
		for model in (Post, Page):
			rows = db.query(model).filter(model.status == "scheduled", model.publish_at <= now).all()
			for row in rows:
				row.status = "published"
				if model is Post:
					row.published_at = row.publish_at
				row.publish_at = None
				count += 1
		if count:
			db.commit()
	except Exception:
		db.rollback()
		logger.exception("publish_due failed")
		return 0
	finally:
		db.close()
	if count:
		from app import generate_and_store_sitemap, clear_page_cache
		generate_and_store_sitemap()
		clear_page_cache()
		logger.info("Published %d scheduled item(s)", count)
	return count


def _editor_emails():
	db = db_manager.get_session()
	try:
		rows = db.query(User).filter(User.is_active.is_(True), User.role.in_(["administrator", "editor"])).all()
		return [u.email for u in rows if u.email]
	finally:
		db.close()


def notify_review_requested(title: str, slug: str, author: str) -> None:
	emails = _editor_emails()
	if emails:
		send_mail(emails, f"Review requested: {title}",
		          f"{author} submitted \"{title}\" for review.\n\nOpen the queue: {request.url_root.rstrip('/')}/admin/review")


def _can_edit_target(kind: str, obj_id: int) -> bool:
	me = current_user()
	if not me:
		return False
	if kind == "page":
		return can("manage_pages")
	if can("edit_others_content"):
		return True
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.id == obj_id).first()
		return bool(post and post.author_id == me["id"])
	finally:
		db.close()


def _get_target(db, kind, obj_id):
	model = Page if kind == "page" else Post
	return db.query(model).filter(model.id == obj_id).first()


def _plain(text: str) -> str:
	text = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", text or "")
	return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def diff_html(old: str, new: str) -> Markup:
	"""Line diff of the text of two HTML bodies, as safe HTML."""
	a, b = _plain(old).splitlines(), _plain(new).splitlines()
	out = []
	for line in difflib.ndiff(a, b):
		tag, text = line[:2], html.escape(line[2:])
		if tag == "+ ":
			out.append(f'<div class="bg-success-subtle">+ {text}</div>')
		elif tag == "- ":
			out.append(f'<div class="bg-danger-subtle">- {text}</div>')
		elif tag == "  ":
			out.append(f'<div class="text-muted">&nbsp; {text}</div>')
	return Markup("".join(out) or "<em>No text differences.</em>")


# --------------------------------------------------------------------------- revisions and autosave
@bp.route("/admin/history/<kind>/<int:obj_id>")
@login_required
def history(kind, obj_id):
	if kind not in ("page", "post") or not _can_edit_target(kind, obj_id):
		abort(403 if kind in ("page", "post") else 404)
	db = db_manager.get_session()
	try:
		obj = _get_target(db, kind, obj_id)
		if not obj:
			abort(404)
		revs = (db.query(Revision).filter(Revision.kind == kind, Revision.object_id == obj_id, Revision.is_autosave.is_(False))
		        .order_by(Revision.id.desc()).all())
		names = {u.id: (u.display_name or u.username) for u in db.query(User).all()}
		edit_url = url_for("editor.edit_post", slug=obj.slug) if kind == "post" else url_for("editor.edit_page", slug=obj.slug)
		return render_template("revisions.html", kind=kind, obj=obj, revisions=revs, names=names, edit_url=edit_url)
	finally:
		db.close()


@bp.route("/admin/history/revision/<int:rev_id>")
@login_required
def revision_view(rev_id):
	db = db_manager.get_session()
	try:
		rev = db.query(Revision).filter(Revision.id == rev_id).first()
		if not rev or not _can_edit_target(rev.kind, rev.object_id):
			abort(404)
		obj = _get_target(db, rev.kind, rev.object_id)
		if not obj:
			abort(404)
		return render_template("revision_view.html", rev=rev, obj=obj, diff=diff_html(rev.content, obj.content),
		                       title_changed=rev.title != obj.title)
	finally:
		db.close()


@bp.route("/admin/history/revision/<int:rev_id>/restore", methods=["POST"])
@login_required
def revision_restore(rev_id):
	db = db_manager.get_session()
	try:
		rev = db.query(Revision).filter(Revision.id == rev_id).first()
		if not rev or not _can_edit_target(rev.kind, rev.object_id):
			abort(404)
		obj = _get_target(db, rev.kind, rev.object_id)
		if not obj:
			abort(404)
		save_revision(db, rev.kind, obj)  # the current version stays recoverable
		obj.title, obj.content = rev.title, rev.content
		if rev.kind == "page":
			obj.description = rev.summary
		else:
			obj.excerpt = rev.summary
		db.commit()
		kind, slug = rev.kind, obj.slug
	finally:
		db.close()
	from app import clear_page_cache
	clear_page_cache()
	flash("Revision restored. The version you replaced is in the history.", "success")
	return redirect(url_for("editor.edit_post", slug=slug) if kind == "post" else url_for("editor.edit_page", slug=slug))


@bp.route("/admin/autosave/<kind>/<int:obj_id>", methods=["GET", "POST"])
@login_required
def autosave(kind, obj_id):
	"""POST stores the editor's current text as this user's autosave; GET returns it if newer than the saved item."""
	if kind not in ("page", "post") or not _can_edit_target(kind, obj_id):
		abort(403)
	me = current_user()
	db = db_manager.get_session()
	try:
		obj = _get_target(db, kind, obj_id)
		if not obj:
			abort(404)
		q = db.query(Revision).filter(Revision.kind == kind, Revision.object_id == obj_id,
		                              Revision.is_autosave.is_(True), Revision.author_id == me["id"])
		if request.method == "GET":
			row = q.order_by(Revision.id.desc()).first()
			if row and (not obj.updated_at or row.created_at > obj.updated_at):
				return jsonify({"title": row.title, "content": row.content, "summary": row.summary,
				                "saved_at": row.created_at.isoformat() + "Z"})
			return jsonify(None)
		data = request.get_json(silent=True) or {}
		q.delete(synchronize_session=False)
		db.add(Revision(kind=kind, object_id=obj_id, title=str(data.get("title") or "")[:255],
		                content=sanitize_html(str(data.get("content") or "")), summary=str(data.get("summary") or "")[:2000],
		                status=obj.status, is_autosave=True, author_id=me["id"]))
		db.commit()
		return jsonify({"ok": True, "saved_at": _now().isoformat() + "Z"})
	finally:
		db.close()


# --------------------------------------------------------------------------- review queue
@bp.route("/admin/review")
@capability_required("moderate")
def review_queue():
	db = db_manager.get_session()
	try:
		posts = db.query(Post).filter(Post.status == "pending").order_by(Post.updated_at.desc()).all()
		names = {u.id: (u.display_name or u.username) for u in db.query(User).all()}
		return render_template("review_queue.html", posts=posts, names=names)
	finally:
		db.close()


@bp.route("/admin/review/<slug>/<action>", methods=["POST"])
@capability_required("moderate")
def review_action(slug, action):
	if action not in ("approve", "reject"):
		abort(404)
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.slug == slug, Post.status == "pending").first()
		if not post:
			abort(404)
		author = db.query(User).filter(User.id == post.author_id).first()
		title = post.title
		if action == "approve":
			if post.publish_at and post.publish_at > _now():
				post.status = "scheduled"
			else:
				post.status, post.publish_at = "published", None
				if not post.published_at:
					post.published_at = _now()
		else:
			post.status = "draft"
		db.commit()
		email = author.email if author else ""
	finally:
		db.close()
	note = (request.form.get("note") or "").strip()[:1000]
	if email:
		send_mail(email, f"{'Approved' if action == 'approve' else 'Needs changes'}: {title}",
		          f"Your post \"{title}\" was {'approved' if action == 'approve' else 'returned to drafts'}."
		          + (f"\n\nNote from the editor:\n{note}" if note else ""))
	from app import generate_and_store_sitemap, clear_page_cache
	generate_and_store_sitemap()
	clear_page_cache()
	from auth import log_event
	log_event(f"review_{action}", post=slug)
	flash("Post approved and published." if action == "approve" else "Post returned to its author as a draft.", "success")
	return redirect(url_for("features.review_queue"))


# --------------------------------------------------------------------------- contact form and inbox
@bp.route("/contact", methods=["GET", "POST"])
def contact():
	from app import get_page_by_slug
	page = get_page_by_slug("contact")
	page = page if page and page.get("status") == "published" else {"title": "Contact", "content": ""}
	form = {"name": "", "email": "", "subject": "", "message": ""}
	errors = []
	if request.method == "POST":
		form = {k: (request.form.get(k) or "").strip() for k in form}
		started = session.get("contact_started", 0)
		if request.form.get("website"):  # honeypot: humans never see this field
			return redirect(url_for("features.contact", sent=1))
		if time.time() - started < 3:
			errors.append("That was very quick. Please try again.")
		if not form["name"] or len(form["name"]) > 200:
			errors.append("Please enter your name.")
		if not EMAIL_RE.match(form["email"]) or len(form["email"]) > 255:
			errors.append("Please enter a valid email address.")
		if len(form["message"]) < 5 or len(form["message"]) > 5000:
			errors.append("Please write a message (up to 5000 characters).")
		if not errors and rate_limited("contact", 5, 600):
			errors.append("Too many messages from your address. Please try again later.")
		if not errors:
			db = db_manager.get_session()
			try:
				msg = ContactMessage(name=form["name"], email=form["email"], subject=form["subject"][:300],
				                     message=form["message"], ip=_ip())
				db.add(msg)
				db.commit()
				msg_id = msg.id
			finally:
				db.close()
			settings = get_site_settings()
			to = settings.get("contact_recipient") or settings.get("mail_from")
			if to and send_mail(to, f"[{settings.get('site_title') or 'Site'}] {form['subject'] or 'New contact message'}",
			                    f"From: {form['name']} <{form['email']}>\n\n{form['message']}", reply_to=form["email"]):
				db = db_manager.get_session()
				try:
					db.query(ContactMessage).filter(ContactMessage.id == msg_id).update({"emailed": True})
					db.commit()
				finally:
					db.close()
			return redirect(url_for("features.contact", sent=1))
	else:
		session["contact_started"] = time.time()
	resp = make_response(render_template(["contact.html", "contact_fallback.html"], page=page, form=form, errors=errors,
	                                     sent=request.args.get("sent") == "1"))
	resp.headers["Cache-Control"] = "no-store"
	return resp


@bp.route("/admin/messages")
@capability_required("moderate")
def messages():
	db = db_manager.get_session()
	try:
		rows = db.query(ContactMessage).order_by(ContactMessage.id.desc()).limit(200).all()
		return render_template("messages.html", messages=rows, mail_ok=mail_configured())
	finally:
		db.close()


@bp.route("/admin/messages/<int:msg_id>/<action>", methods=["POST"])
@capability_required("moderate")
def message_action(msg_id, action):
	if action not in ("read", "unread", "delete"):
		abort(404)
	db = db_manager.get_session()
	try:
		q = db.query(ContactMessage).filter(ContactMessage.id == msg_id)
		if action == "delete":
			q.delete()
		else:
			q.update({"is_read": action == "read"})
		db.commit()
	finally:
		db.close()
	return redirect(url_for("features.messages"))


# --------------------------------------------------------------------------- comments
def comments_enabled() -> bool:
	return str(get_site_settings().get("comments_enabled", "")).lower() in ("1", "true", "on", "yes")


def approved_comments(post_id: int):
	db = db_manager.get_session()
	try:
		rows = (db.query(Comment).filter(Comment.post_id == post_id, Comment.status == "approved")
		        .order_by(Comment.id).all())
		return [{"author_name": c.author_name, "body": c.body, "created_at": c.created_at} for c in rows]
	finally:
		db.close()


@bp.route("/post/<slug>/comment", methods=["POST"])
def add_comment(slug):
	from app import get_post_by_slug
	post = get_post_by_slug(slug)
	if not comments_enabled() or not post or post.get("status") != "published":
		abort(404)
	back = url_for("public.view_post", slug=slug) if "view_post" in _endpoints() else f"/post/{slug}"
	if request.form.get("website"):  # honeypot
		return redirect(back)
	name = (request.form.get("author_name") or "").strip()[:150]
	email = (request.form.get("author_email") or "").strip()[:255]
	body = (request.form.get("body") or "").strip()
	if not name or len(body) < 2 or len(body) > 3000 or (email and not EMAIL_RE.match(email)):
		flash("Please enter your name and a comment (up to 3000 characters).", "error")
		return redirect(back + "#comments")
	if rate_limited("comment", 5, 600):
		flash("Too many comments from your address. Please try again later.", "error")
		return redirect(back + "#comments")
	me = current_user()
	status = "approved" if me and can("moderate") else "pending"
	db = db_manager.get_session()
	try:
		db.add(Comment(post_id=post["id"], author_name=name, author_email=email, body=body, status=status, ip=_ip()))
		db.commit()
	finally:
		db.close()
	if status == "pending":
		emails = _editor_emails()
		if emails:
			send_mail(emails, f"Comment awaiting moderation on {post['title']}", f"{name} wrote:\n\n{body}\n\nModerate: {request.url_root.rstrip('/')}/admin/comments")
	flash("Thank you. Your comment will appear after it has been approved." if status == "pending" else "Comment posted.", "success")
	return redirect(back + "#comments")


def _endpoints():
	from flask import current_app
	return {r.endpoint for r in current_app.url_map.iter_rules()}


@bp.route("/admin/comments")
@capability_required("moderate")
def comments_admin():
	status = request.args.get("status", "pending")
	db = db_manager.get_session()
	try:
		q = db.query(Comment, Post.title, Post.slug).join(Post, Post.id == Comment.post_id)
		if status in ("pending", "approved", "spam"):
			q = q.filter(Comment.status == status)
		rows = q.order_by(Comment.id.desc()).limit(200).all()
		return render_template("comments_admin.html", rows=rows, status=status, enabled=comments_enabled())
	finally:
		db.close()


@bp.route("/admin/comments/<int:cid>/<action>", methods=["POST"])
@capability_required("moderate")
def comment_action(cid, action):
	if action not in ("approve", "spam", "delete"):
		abort(404)
	db = db_manager.get_session()
	try:
		q = db.query(Comment).filter(Comment.id == cid)
		if action == "delete":
			q.delete()
		else:
			q.update({"status": "approved" if action == "approve" else "spam"})
		db.commit()
	finally:
		db.close()
	from app import clear_page_cache
	clear_page_cache()
	return redirect(url_for("features.comments_admin", status=request.args.get("status", "pending")))


# --------------------------------------------------------------------------- redirects
@bp.route("/admin/redirects", methods=["GET", "POST"])
@capability_required("moderate")
def redirects_admin():
	if request.method == "POST":
		source = (request.form.get("source") or "").strip()
		target = (request.form.get("target") or "").strip()
		if not source.startswith("/") or source.startswith("//") or not re.match(r"^(/(?!/)|https?://)", target):
			flash("The old address must start with / and the new one with / or https://.", "error")
		elif source.startswith(("/admin", "/login")):
			flash("Admin addresses cannot be redirected.", "error")
		else:
			db = db_manager.get_session()
			try:
				add_redirect(db, source.rstrip("/") or "/", target, request.form.get("permanent") == "on")
				db.commit()
			finally:
				db.close()
			flash("Redirect saved.", "success")
		return redirect(url_for("features.redirects_admin"))
	db = db_manager.get_session()
	try:
		rows = db.query(Redirect).order_by(Redirect.id.desc()).all()
		return render_template("redirects.html", redirects=rows)
	finally:
		db.close()


@bp.route("/admin/redirects/<int:rid>/delete", methods=["POST"])
@capability_required("moderate")
def redirect_delete(rid):
	db = db_manager.get_session()
	try:
		db.query(Redirect).filter(Redirect.id == rid).delete()
		db.commit()
	finally:
		db.close()
	return redirect(url_for("features.redirects_admin"))


def redirect_for_path(path: str):
	"""A Response for a stored redirect matching this path, or None."""
	db = db_manager.get_session()
	try:
		row = db.query(Redirect).filter(Redirect.source.in_([path, path.rstrip("/") or "/"])).first()
		if not row:
			return None
		row.hits = (row.hits or 0) + 1
		db.commit()
		return redirect(row.target, code=301 if row.permanent else 302)
	except Exception:
		db.rollback()
		return None
	finally:
		db.close()


# --------------------------------------------------------------------------- extra settings (mail, SEO, comments)
EXTRA_KEYS = ["mail_host", "mail_port", "mail_security", "mail_user", "mail_from", "contact_recipient",
              "robots_txt", "default_og_image", "site_description", "comments_enabled"]


@bp.route("/admin/settings/more", methods=["GET", "POST"])
@capability_required("manage_settings")
def settings_more():
	if request.method == "POST":
		values = {k: (request.form.get(k) or "").strip()[:5000] for k in EXTRA_KEYS if k != "comments_enabled"}
		values["comments_enabled"] = "1" if request.form.get("comments_enabled") == "on" else "0"
		if values["mail_port"] and not values["mail_port"].isdigit():
			values["mail_port"] = ""
		if values["mail_security"] not in ("starttls", "ssl", "none"):
			values["mail_security"] = "starttls"
		new_pw = request.form.get("mail_password") or ""
		if new_pw:
			values["mail_password"] = encrypt_secret(new_pw)
		elif request.form.get("clear_mail_password") == "on":
			values["mail_password"] = ""
		update_site_settings(values)
		if request.form.get("send_test") == "on":
			ok = send_mail(values["contact_recipient"] or values["mail_from"], "FlexaFlow test email",
			               "If you can read this, outgoing email works.", wait=True)
			flash("Test email sent (check the inbox and the server log if it does not arrive)." if ok
			      else "Email is not configured: set the server and From address.", "success" if ok else "error")
		from auth import log_event
		log_event("settings_extra_updated")
		from app import clear_page_cache
		clear_page_cache()
		flash("Settings saved.", "success")
		return redirect(url_for("features.settings_more"))
	settings = get_site_settings()
	return render_template("settings_more.html", s=settings, has_password=bool(settings.get("mail_password")))


# --------------------------------------------------------------------------- feeds and robots.txt
def _feed(category_slug=None):
	from app import post_url
	settings = get_site_settings()
	base = request.url_root.rstrip("/")
	db = db_manager.get_session()
	try:
		q = db.query(Post).filter(Post.status == "published")
		if category_slug:
			from data_store import Category
			cat = db.query(Category).filter(Category.slug == category_slug).first()
			if not cat:
				abort(404)
			q = q.filter(Post.category_id == cat.id)
		posts = q.order_by(Post.published_at.desc(), Post.id.desc()).limit(20).all()
		items = []
		for p in posts:
			d = p.to_dict()
			stamp = (p.updated_at or p.published_at or _now())
			items.append(
				f"<entry><title>{xml_escape(p.title or '')}</title><id>{base}{xml_escape(post_url(d))}</id>"
				f'<link href="{base}{xml_escape(post_url(d))}"/><updated>{stamp.strftime("%Y-%m-%dT%H:%M:%SZ")}</updated>'
				f'<summary type="html">{xml_escape(p.excerpt or "")}</summary>'
				f'<content type="html">{xml_escape(p.content or "")}</content></entry>')
		updated = (posts[0].updated_at if posts and posts[0].updated_at else _now()).strftime("%Y-%m-%dT%H:%M:%SZ")
	finally:
		db.close()
	title = xml_escape(settings.get("site_title") or "FlexaFlow")
	xml = ('<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
	       f"<title>{title}</title><id>{base}/</id><link href=\"{base}/\"/><updated>{updated}</updated>" + "".join(items) + "</feed>")
	resp = make_response(xml)
	resp.headers["Content-Type"] = "application/atom+xml; charset=utf-8"
	return resp


@bp.route("/feed.xml")
def feed():
	return _feed()


@bp.route("/category/<slug>/feed.xml")
def category_feed(slug):
	return _feed(slug)


@bp.route("/robots.txt")
def robots():
	custom = (get_site_settings().get("robots_txt") or "").strip()
	body = custom or f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /login\n\nSitemap: {request.url_root.rstrip('/')}/sitemap.xml\n"
	resp = make_response(body)
	resp.headers["Content-Type"] = "text/plain; charset=utf-8"
	return resp
