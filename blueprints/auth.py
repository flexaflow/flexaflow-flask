"""Sign-in, sign-out, profile, two-factor setup and user management."""
import sys as _sys

from flask import Blueprint

bp = Blueprint("auth", __name__)

# Names defined in app.py (helpers, stores, settings) are shared with this module.
# This file is imported at the end of app.py, once all of them exist.
_core = _sys.modules["app"]
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and k not in ("bp",)})


@bp.route("/login", methods=["GET", "POST"])
def login():
	"""
	Username and password login, followed by a TOTP code when the account has 2FA.

	Accounts are created from the command line (python manage.py createsuperuser),
	never from the web. Repeated failures slow further attempts down (per IP and per username+IP).
	"""
	if db_manager.count_users() == 0:
		return render_template("no_users.html")

	next_url = _safe_next(request.values.get("next"))

	if request.method == "GET":
		session.pop("pending_2fa_user_id", None)
		return render_template("login.html", show_2fa=False, username="", next_url=next_url or "")

	pending_id = session.get("pending_2fa_user_id")
	if pending_id:
		return _finish_two_factor(pending_id, next_url)

	username = (request.form.get("username") or "").strip()
	password = request.form.get("password") or ""
	if not username or not password:
		flash("Username and password are required!", "error")
		return render_template("login.html", show_2fa=False, username=username, next_url=next_url or "")

	wait = throttle_wait("login", username)
	if wait:
		flash(wait_message(wait), "error")
		return render_template("login.html", show_2fa=False, username=username, next_url=next_url or ""), 429

	user = db_manager.get_user_by_username(username, include_secrets=True)
	if not user or not user["is_active"] or not verify_password(user["password_hash"], password):
		record_failure("login", username)
		flash("Invalid credentials!", "error")
		return render_template("login.html", show_2fa=False, username=username, next_url=next_url or "")

	clear_failures("login", username)
	if user["two_fa_enabled"] and user["two_fa_secret"]:
		session["pending_2fa_user_id"] = user["id"]
		return render_template("login.html", show_2fa=True, username=username, next_url=next_url or "")

	login_user(user["id"])
	log_event("login_success", user=username)
	flash("Login successful!", "success")
	return redirect(next_url or url_for("admin.admin"))


@bp.route("/logout")
@login_required
def logout():
	"""End the session and return to the login page."""
	log_event("logout")
	logout_user()
	flash("You have been logged out.", "info")
	return redirect(url_for("auth.login"))


@bp.route("/admin/profile", methods=["GET", "POST"])
@login_required
def profile():
	"""
	The signed-in user's own profile: display name, email, password and 2FA.
	Changing the password or disabling 2FA requires the current password.
	"""
	me = current_user()
	errors = []
	if request.method == "POST":
		action = request.form.get("action", "save")
		stored = db_manager.get_user(me["id"], include_secrets=True)

		if action == "disable_2fa":
			if not verify_password(stored["password_hash"], request.form.get("current_password") or ""):
				errors.append("Current password is incorrect.")
			else:
				db_manager.update_user(me["id"], two_fa_enabled=False, two_fa_secret=None, two_fa_recovery=None)
				log_event("2fa_disabled", user=me["username"])
				flash("Two-factor authentication has been disabled.", "success")
				return redirect(url_for("auth.profile"))
		else:
			display_name = (request.form.get("display_name") or "").strip()[:150]
			email = (request.form.get("email") or "").strip()
			changes = {"display_name": display_name or None, "email": email}
			if not EMAIL_RE.match(email):
				errors.append("Enter a valid email address.")
			new_password = request.form.get("new_password") or ""
			if new_password:
				if not verify_password(stored["password_hash"], request.form.get("current_password") or ""):
					errors.append("Current password is incorrect.")
				elif new_password != request.form.get("confirm_password"):
					errors.append("New passwords do not match.")
				else:
					problem = validate_password(new_password, me["username"])
					if problem:
						errors.append(problem)
					else:
						changes["password_hash"] = hash_password(new_password)
			if not errors:
				try:
					db_manager.update_user(me["id"], **changes)
					if "password_hash" in changes:
						invalidate_other_sessions(me["id"])
						log_event("password_changed", user=me["username"])
						flash("Password changed. All other sessions were signed out.", "success")
					else:
						flash("Profile updated.", "success")
					return redirect(url_for("auth.profile"))
				except ValueError as e:
					errors.append(str(e))

	return render_template(
		"user_form.html",
		mode="profile",
		user=current_user(),
		form=None,
		errors=errors,
		roles=ROLES,
		role_labels=ROLE_LABELS,
	)


@bp.route("/admin/users")
@capability_required("manage_users")
def users_index():
	"""List all accounts."""
	return render_template(
		"users.html",
		users=db_manager.list_users(),
		role_labels=ROLE_LABELS,
		role_descriptions=ROLE_DESCRIPTIONS,
	)


@bp.route("/admin/users/new", methods=["GET", "POST"])
@capability_required("manage_users")
def user_new():
	"""Create an account from the web UI (administrators only). The first account is made with the CLI."""
	errors = []
	form = {"username": "", "email": "", "display_name": "", "role": ROLE_CONTRIBUTOR, "is_active": True}
	if request.method == "POST":
		form = _user_form_from_request()
		password = request.form.get("password") or ""
		confirm = request.form.get("confirm_password") or ""
		if not USERNAME_RE.match(form["username"]):
			errors.append("Username must be 3-64 characters: letters, digits, '.', '_' or '-'.")
		if not EMAIL_RE.match(form["email"]):
			errors.append("Enter a valid email address.")
		if validate_role(form["role"]):
			errors.append(validate_role(form["role"]))
		if password != confirm:
			errors.append("Passwords do not match.")
		problem = validate_password(password, form["username"])
		if problem:
			errors.append(problem)
		if not errors:
			try:
				db_manager.create_user(
					username=form["username"],
					email=form["email"],
					password_hash=hash_password(password),
					role=form["role"],
					display_name=form["display_name"] or None,
				)
				log_event("user_created", user=form["username"], role=form["role"])
				flash(f"User '{form['username']}' created.", "success")
				return redirect(url_for("auth.users_index"))
			except ValueError as e:
				errors.append(str(e))
	return render_template(
		"user_form.html", mode="create", user=None, form=form, errors=errors,
		roles=ROLES, role_labels=ROLE_LABELS, role_descriptions=ROLE_DESCRIPTIONS,
	)


@bp.route("/admin/users/<int:user_id>", methods=["GET", "POST"])
@capability_required("manage_users")
def user_edit(user_id):
	"""Edit another account: role, status, email, display name, and optionally reset its password."""
	target = db_manager.get_user(user_id)
	if not target:
		abort(404)
	me = current_user()
	is_self = target["id"] == me["id"]
	errors = []
	form = dict(target)
	if request.method == "POST":
		posted = _user_form_from_request()
		form.update({"email": posted["email"], "display_name": posted["display_name"]})
		if is_self:
			posted["role"] = target["role"]
			posted["is_active"] = True
		form.update({"role": posted["role"], "is_active": posted["is_active"]})

		if not EMAIL_RE.match(posted["email"]):
			errors.append("Enter a valid email address.")
		if validate_role(posted["role"]):
			errors.append(validate_role(posted["role"]))
		demoting = target["role"] == ROLE_ADMINISTRATOR and (
			posted["role"] != ROLE_ADMINISTRATOR or not posted["is_active"]
		)
		if demoting and db_manager.count_active_administrators() <= 1:
			errors.append("This is the last active administrator. Make another administrator first.")

		changes = {
			"email": posted["email"],
			"display_name": posted["display_name"] or None,
			"role": posted["role"],
			"is_active": posted["is_active"],
		}
		new_password = request.form.get("new_password") or ""
		if new_password:
			if new_password != (request.form.get("confirm_password") or ""):
				errors.append("Passwords do not match.")
			else:
				problem = validate_password(new_password, target["username"])
				if problem:
					errors.append(problem)
				else:
					changes["password_hash"] = hash_password(new_password)

		if not errors:
			try:
				db_manager.update_user(user_id, **changes)
				if changes["role"] != target["role"]:
					log_event("role_changed", user=target["username"], old=target["role"], new=changes["role"])
				if "password_hash" in changes or not changes["is_active"] or changes["role"] != target["role"]:
					invalidate_other_sessions(user_id) if not is_self else None
					if "password_hash" in changes:
						log_event("password_reset_by_admin", user=target["username"])
				flash("User updated.", "success")
				return redirect(url_for("auth.user_edit", user_id=user_id))
			except ValueError as e:
				errors.append(str(e))

	return render_template(
		"user_form.html", mode="edit", user=target, form=form, errors=errors,
		roles=ROLES, role_labels=ROLE_LABELS, role_descriptions=ROLE_DESCRIPTIONS,
		is_self=is_self,
	)


@bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@capability_required("manage_users")
def user_delete(user_id):
	"""Delete an account. Its pages and posts are handed to the administrator doing the deletion."""
	me = current_user()
	target = db_manager.get_user(user_id)
	if not target:
		abort(404)
	if target["id"] == me["id"]:
		flash("You cannot delete your own account.", "error")
	elif target["role"] == ROLE_ADMINISTRATOR and db_manager.count_active_administrators() <= 1:
		flash("This is the last active administrator and cannot be deleted.", "error")
	elif db_manager.delete_user(user_id, reassign_to_id=me["id"]):
		log_event("user_deleted", user=target["username"])
		flash(f"User '{target['username']}' deleted. Their content now belongs to you.", "success")
	else:
		flash("Could not delete that user.", "error")
	return redirect(url_for("auth.users_index"))


@bp.route("/admin/enable-2fa")
@login_required
def enable_2fa():
	"""Start (or restart) 2FA enrolment: create a pending secret, then show the QR code."""
	session["pending_2fa_secret"] = pyotp.random_base32()
	flash("Scan the QR code with your authenticator app, then enter the code to confirm.", "success")
	return redirect(url_for("auth.setup_2fa"))


@bp.route("/admin/2fa-recovery", methods=["POST"])
@login_required
def regenerate_recovery_codes():
	"""Replace the user's recovery codes (requires the current password)."""
	me = current_user()
	stored = db_manager.get_user(me["id"], include_secrets=True)
	if not stored["two_fa_enabled"]:
		abort(400)
	if not verify_password(stored["password_hash"], request.form.get("current_password") or ""):
		flash("Current password is incorrect.", "error")
		return redirect(url_for("auth.profile"))
	plain, hashed = generate_recovery_codes()
	db_manager.update_user(me["id"], two_fa_recovery=hashed)
	log_event("recovery_codes_regenerated", user=me["username"])
	return render_template("recovery_codes.html", codes=plain)


@bp.route("/admin/2fa-setup", methods=["GET", "POST"])
@login_required
def setup_2fa():
	"""Confirm 2FA enrolment for the current user. Saved only after a correct code is entered."""
	me = current_user()
	secret = session.get("pending_2fa_secret")
	if not secret:
		return redirect(url_for("auth.enable_2fa"))

	if request.method == "POST":
		if request.form.get("cancel_2fa") == "1":
			session.pop("pending_2fa_secret", None)
			flash("Two-factor authentication setup was canceled.", "info")
			return redirect(url_for("auth.profile"))
		code = (request.form.get("verify_2fa_code") or "").strip()
		if pyotp.TOTP(secret).verify(code, valid_window=1):
			plain, hashed = generate_recovery_codes()
			db_manager.update_user(me["id"], two_fa_secret=secret, two_fa_enabled=True, two_fa_recovery=hashed)
			session.pop("pending_2fa_secret", None)
			log_event("2fa_enabled", user=me["username"])
			flash("Two-factor authentication is now active.", "success")
			return render_template("recovery_codes.html", codes=plain)
		flash("Invalid code. Please try again.", "danger")

	provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
		name=me["email"], issuer_name="FlexaFlow CMS"
	)
	return render_template("setup_2fa.html", provisioning_uri=provisioning_uri, secret=secret)
