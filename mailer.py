"""Outgoing email through the SMTP server configured in Admin > Site > Email, SEO & comments."""
import logging
import smtplib
import threading
from email.message import EmailMessage

from crypto_utils import decrypt_secret
from data_store import get_site_settings

logger = logging.getLogger(__name__)


def mail_configured() -> bool:
	st = get_site_settings()
	return bool(st.get("mail_host") and st.get("mail_from"))


def _send(cfg: dict, msg: EmailMessage) -> None:
	try:
		port = int(cfg.get("mail_port") or 587)
		if cfg.get("mail_security") == "ssl":
			server = smtplib.SMTP_SSL(cfg["mail_host"], port, timeout=15)
		else:
			server = smtplib.SMTP(cfg["mail_host"], port, timeout=15)
			if cfg.get("mail_security", "starttls") == "starttls":
				server.starttls()
		with server:
			if cfg.get("mail_user"):
				server.login(cfg["mail_user"], decrypt_secret(cfg.get("mail_password") or "") or "")
			server.send_message(msg)
	except Exception:
		logger.exception("Sending email failed")


def send_mail(to, subject: str, body: str, reply_to: str = "", wait: bool = False) -> bool:
	"""Queue an email. Returns False when SMTP is not configured. Header fields are stripped of newlines."""
	cfg = get_site_settings()
	if not mail_configured():
		return False
	recipients = [to] if isinstance(to, str) else list(to)
	recipients = [r.replace("\n", "").replace("\r", "") for r in recipients if r]
	if not recipients:
		return False
	msg = EmailMessage()
	msg["Subject"] = subject.replace("\n", " ").replace("\r", " ")[:200]
	msg["From"] = cfg["mail_from"].replace("\n", "").replace("\r", "")
	msg["To"] = ", ".join(recipients)
	if reply_to:
		msg["Reply-To"] = reply_to.replace("\n", "").replace("\r", "")
	msg.set_content(body)
	if wait:
		_send(cfg, msg)
	else:
		threading.Thread(target=_send, args=(cfg, msg), daemon=True).start()
	return True
