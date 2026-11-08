"""
Helpers for moving content between FlexaFlow and WordPress / Blogger.

* sanitize_html   - allow-list HTML cleaner. Imported content is rendered with |safe, so
                    anything that comes from another site is cleaned first (no scripts,
                    no event handlers, no javascript: links).
* wp_to_html      - turns WordPress "classic" or block-editor content into plain HTML.
* safe_fetch      - downloads a remote file for media import, refusing private/internal
                    addresses (SSRF), huge files and non-media content types.
* parse_xml_bytes - XML parsing that refuses entity tricks ("billion laughs").
"""

import html
import http.client
import ipaddress
import re
import socket
import ssl
import unicodedata
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse


class MigrationError(Exception):
	"""A problem the administrator should be told about in plain words."""


def slugify(text: str) -> str:
	"""Same rules as the CMS (keeps Bangla letters), so imported addresses look native."""
	if not text:
		return ""
	text = unicodedata.normalize("NFKC", unquote(str(text)))
	slug = re.sub(r"[^\w\s\-\u0980-\u09FF]", "", text).strip().lower()
	return re.sub(r"[-_\s]+", "-", slug).strip("-")[:200]


MAX_XML_BYTES = 200 * 1024 * 1024


def parse_xml_bytes(data: bytes) -> ET.Element:
	if len(data) > MAX_XML_BYTES:
		raise MigrationError("That XML file is too large to import.")
	head = data[:200000]
	if re.search(rb"<!ENTITY", head, re.I):
		raise MigrationError("This XML file declares entities, which is not allowed for safety.")
	try:
		return ET.fromstring(data)
	except ET.ParseError as e:
		raise MigrationError(f"The XML file could not be read: {e}")


def local(tag: str) -> str:
	"""'{namespace}name' -> 'name'"""
	return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


ALLOWED_TAGS = {
	"a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "dd", "del", "div", "dl", "dt", "em",
	"figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "iframe", "img", "ins", "kbd",
	"li", "mark", "ol", "p", "pre", "q", "s", "small", "span", "strike", "strong", "sub", "sup", "table",
	"tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul", "video", "audio", "source", "center", "font",
}
VOID_TAGS = {"br", "hr", "img", "source"}
DROP_WITH_CONTENT = {"script", "style", "object", "embed", "form", "noscript", "template", "svg", "math", "applet", "head", "title"}
ALLOWED_ATTRS = {
	"*": {"class", "id", "title", "lang", "dir", "style", "align"},
	"a": {"href", "target", "rel", "name"},
	"img": {"src", "alt", "width", "height", "loading", "srcset", "sizes", "border"},
	"iframe": {"src", "width", "height", "allowfullscreen", "frameborder", "allow", "loading"},
	"video": {"src", "controls", "poster", "width", "height", "preload"},
	"audio": {"src", "controls", "preload"},
	"source": {"src", "type"},
	"td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan", "scope"},
	"font": {"color", "size", "face"},
	"ol": {"start", "type"},
}
URL_ATTRS = {"href", "src", "poster"}
SAFE_URL = re.compile(r"^(https?:|mailto:|tel:|/(?!/)|//|#|\.{0,2}/|[\w\-%.~]+(/|\.|$))", re.I)
SAFE_DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.I)
EMBED_HOSTS = (
	"www.youtube.com", "youtube.com", "www.youtube-nocookie.com", "player.vimeo.com",
	"www.google.com", "maps.google.com", "www.facebook.com", "open.spotify.com", "w.soundcloud.com",
)
SAFE_STYLE_PROPS = {
	"text-align", "font-weight", "font-style", "text-decoration", "color", "background-color",
	"width", "max-width", "height", "margin", "margin-left", "margin-right", "margin-top", "margin-bottom",
	"padding", "float", "clear", "display", "font-size", "line-height", "border", "vertical-align",
}
SAFE_STYLE_VALUE = re.compile(r"^[\w\s#%.,()\-+/]+$")


def _clean_style(value: str) -> str:
	out = []
	for part in (value or "").split(";"):
		if ":" not in part:
			continue
		prop, val = (x.strip() for x in part.split(":", 1))
		low = val.lower()
		if prop.lower() in SAFE_STYLE_PROPS and SAFE_STYLE_VALUE.match(val) and "url(" not in low and "expression" not in low:
			out.append(f"{prop.lower()}: {val}")
	return "; ".join(out)


def _clean_url(value: str, attr: str, tag: str) -> Optional[str]:
	value = re.sub(r"[\x00-\x20]+", "", html.unescape(value or ""))
	if not value:
		return None
	if value.lower().startswith("data:"):
		return value if tag == "img" and attr == "src" and SAFE_DATA_IMAGE.match(value) else None
	if not SAFE_URL.match(value):
		return None
	if tag == "iframe":
		host = (urlparse(value if "://" in value else "https:" + value).hostname or "").lower()
		if host not in EMBED_HOSTS:
			return None
	return value


class _Sanitizer(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.out: List[str] = []
		self.stack: List[str] = []
		self.skip = 0

	def handle_starttag(self, tag, attrs):
		tag = tag.lower()
		if self.skip:
			if tag in DROP_WITH_CONTENT:
				self.skip += 1
			return
		if tag in DROP_WITH_CONTENT:
			self.skip = 1
			return
		if tag not in ALLOWED_TAGS:
			return
		clean = []
		allowed = ALLOWED_ATTRS["*"] | ALLOWED_ATTRS.get(tag, set())
		for name, value in attrs:
			name = (name or "").lower()
			if name.startswith("on") or name not in allowed:
				continue
			value = value or ""
			if name in URL_ATTRS:
				value = _clean_url(value, name, tag)
				if value is None:
					continue
			elif name == "srcset":
				value = ", ".join(
					u for u in (_clean_url(p.split()[0], "src", "img") and p.strip() for p in value.split(",") if p.strip()) if u)
				if not value:
					continue
			elif name == "style":
				value = _clean_style(value)
				if not value:
					continue
			clean.append((name, value))
		if tag == "iframe" and not any(n == "src" for n, _ in clean):
			return
		if tag == "a" and any(n == "target" and v == "_blank" for n, v in clean):
			clean = [(n, v) for n, v in clean if n != "rel"] + [("rel", "noopener noreferrer")]
		rendered = "".join(f' {n}="{html.escape(v, quote=True)}"' for n, v in clean)
		self.out.append(f"<{tag}{rendered}>")
		if tag not in VOID_TAGS:
			self.stack.append(tag)

	def handle_startendtag(self, tag, attrs):
		self.handle_starttag(tag, attrs)
		if tag.lower() not in VOID_TAGS:
			self.handle_endtag(tag)

	def handle_endtag(self, tag):
		tag = tag.lower()
		if self.skip:
			if tag in DROP_WITH_CONTENT:
				self.skip -= 1
			return
		if tag in VOID_TAGS or tag not in ALLOWED_TAGS or tag not in self.stack:
			return
		while self.stack:
			top = self.stack.pop()
			self.out.append(f"</{top}>")
			if top == tag:
				break

	def handle_data(self, data):
		if not self.skip:
			self.out.append(html.escape(data, quote=False))

	def handle_comment(self, data):
		pass

	def close(self):
		super().close()
		while self.stack:
			self.out.append(f"</{self.stack.pop()}>")


def sanitize_html(text: str) -> str:
	if not text:
		return ""
	parser = _Sanitizer()
	parser.feed(text)
	parser.close()
	return "".join(parser.out).strip()


BLOCK_TAGS = re.compile(r"<(p|div|h[1-6]|ul|ol|li|table|blockquote|pre|figure|hr|section|article|iframe)\b", re.I)
CAPTION_RE = re.compile(r"\[caption[^\]]*\](.*?)\[/caption\]", re.S | re.I)
SHORTCODE_PAIR = re.compile(r"\[/?[a-zA-Z][\w-]*(?:\s[^\]]*)?\]")
YOUTUBE_LINE = re.compile(
	r"^\s*(?:<p>)?\s*https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})[^\s<]*\s*(?:</p>)?\s*$", re.M)


def _caption(match):
	inner = match.group(1).strip()
	img = re.search(r"(<a\b[^>]*>\s*)?<img\b[^>]*>(\s*</a>)?", inner, re.I)
	if not img:
		return inner
	text = (inner[:img.start()] + inner[img.end():]).strip()
	return f"<figure>{img.group(0)}<figcaption>{text}</figcaption></figure>" if text else f"<figure>{img.group(0)}</figure>"


def _autop(text: str) -> str:
	"""A small version of WordPress' wpautop: blank line = new paragraph, single newline = <br>."""
	text = text.replace("\r\n", "\n").strip()
	if not text:
		return ""
	paragraphs = re.split(r"\n\s*\n", text)
	return "\n".join(f"<p>{p.strip().replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip())


def wp_to_html(content: str) -> str:
	content = content or ""
	content = re.sub(r"<!--\s*/?wp:[^>]*-->", "", content)
	content = re.sub(r"<!--\s*more[^>]*-->", "", content, flags=re.I)
	content = CAPTION_RE.sub(_caption, content)
	content = re.sub(r"\[gallery[^\]]*\]", "", content, flags=re.I)
	content = SHORTCODE_PAIR.sub("", content)
	if not BLOCK_TAGS.search(content):
		content = _autop(content)
	content = YOUTUBE_LINE.sub(
		lambda m: f'<iframe src="https://www.youtube.com/embed/{m.group(1)}" width="560" height="315" allowfullscreen></iframe>',
		content)
	return sanitize_html(content)


def html_to_wp(content: str, base_url: str = "") -> str:
	"""FlexaFlow HTML -> WordPress content. WordPress reads plain HTML fine; only addresses need fixing."""
	return make_absolute(content or "", base_url)


def make_absolute(content: str, base_url: str) -> str:
	if not base_url:
		return content
	base = base_url.rstrip("/") + "/"
	return re.sub(
		r'(\b(?:src|href|poster)=")(/(?!/)[^"]*)(")',
		lambda m: m.group(1) + urljoin(base, m.group(2)) + m.group(3), content)


def image_urls(content: str) -> List[str]:
	return re.findall(r'<img\b[^>]*?\bsrc="([^"]+)"', content or "", re.I)


def strip_tags(text: str) -> str:
	return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_DOWNLOAD_TYPES = ("image/", "application/pdf")


def _public_ips(host: str, port: int) -> List[str]:
	try:
		infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
	except socket.gaierror:
		raise MigrationError(f"Could not resolve {host}.")
	ips = []
	for info in infos:
		ip = info[4][0]
		if not ipaddress.ip_address(ip).is_global:
			raise MigrationError(f"{host} points to a private or internal address; not downloaded.")
		ips.append(ip)
	if not ips:
		raise MigrationError(f"Could not resolve {host}.")
	return ips


class _PinnedHTTP(http.client.HTTPConnection):
	def __init__(self, host, port, ip, timeout):
		super().__init__(host, port, timeout=timeout)
		self._ip = ip

	def connect(self):
		self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
	def __init__(self, host, port, ip, timeout):
		super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
		self._ip = ip

	def connect(self):
		raw = socket.create_connection((self._ip, self.port), self.timeout)
		self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def safe_fetch(url: str, *, timeout: float = 15.0, max_redirects: int = 3) -> Tuple[bytes, str]:
	"""Download url -> (bytes, content_type). The connection is pinned to the validated IP address."""
	for _ in range(max_redirects + 1):
		parts = urlparse(url)
		if parts.scheme not in ("http", "https") or not parts.hostname:
			raise MigrationError("Only http(s) addresses can be downloaded.")
		port = parts.port or (443 if parts.scheme == "https" else 80)
		ip = _public_ips(parts.hostname, port)[0]
		cls = _PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP
		conn = cls(parts.hostname, port, ip, timeout)
		try:
			path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
			conn.request("GET", path, headers={"User-Agent": "FlexaFlow-Importer/1.0", "Accept": "*/*"})
			resp = conn.getresponse()
			if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
				url = urljoin(url, resp.getheader("Location"))
				continue
			if resp.status != 200:
				raise MigrationError(f"HTTP {resp.status}")
			ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
			if not ctype.startswith(ALLOWED_DOWNLOAD_TYPES):
				raise MigrationError(f"unsupported file type {ctype or 'unknown'}")
			length = resp.getheader("Content-Length")
			if length and length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
				raise MigrationError("file is too large")
			data = resp.read(MAX_DOWNLOAD_BYTES + 1)
			if len(data) > MAX_DOWNLOAD_BYTES:
				raise MigrationError("file is too large")
			return data, ctype
		except (OSError, http.client.HTTPException) as e:
			raise MigrationError(str(e) or e.__class__.__name__)
		finally:
			conn.close()
	raise MigrationError("too many redirects")
