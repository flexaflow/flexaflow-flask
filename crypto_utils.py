"""
Encryption for secrets stored in the database (currently 2FA secrets).

The key comes from FLEXAFLOW_2FA_KEY (a Fernet key) when set. Otherwise one is
generated into instance/twofa.key, a file that is separate from the database
and should be backed up separately. Values written by older versions
(plain text) are still read and are re-encrypted by DatabaseManager at startup.
"""

import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import String, TypeDecorator

PREFIX = "enc:"
_fernet = None


def _key_path() -> str:
	base = os.path.dirname(os.path.abspath(__file__))
	return os.path.join(base, "instance", "twofa.key")


def _get_fernet() -> Fernet:
	global _fernet
	if _fernet is not None:
		return _fernet
	key = (os.getenv("FLEXAFLOW_2FA_KEY") or "").strip()
	if not key:
		path = _key_path()
		os.makedirs(os.path.dirname(path), exist_ok=True)
		if os.path.exists(path):
			with open(path, encoding="utf-8") as fh:
				key = fh.read().strip()
		if not key:
			key = Fernet.generate_key().decode()
			with open(path, "w", encoding="utf-8") as fh:
				fh.write(key)
			try:
				os.chmod(path, 0o600)
			except OSError:
				pass
	_fernet = Fernet(key.encode())
	return _fernet


def encrypt_secret(value):
	if value is None or value == "" or str(value).startswith(PREFIX):
		return value
	return PREFIX + _get_fernet().encrypt(str(value).encode()).decode()


def decrypt_secret(value):
	if value is None or not str(value).startswith(PREFIX):
		return value  # empty, or a legacy plain-text value
	try:
		return _get_fernet().decrypt(str(value)[len(PREFIX):].encode()).decode()
	except InvalidToken:
		# Wrong or lost key: treat as "no secret" rather than leaking ciphertext.
		return None


def is_encrypted(value) -> bool:
	return bool(value) and str(value).startswith(PREFIX)


class EncryptedString(TypeDecorator):
	"""String column that is encrypted at rest and decrypted when loaded."""
	impl = String
	cache_ok = True

	def process_bind_param(self, value, dialect):
		return encrypt_secret(value)

	def process_result_value(self, value, dialect):
		return decrypt_secret(value)
