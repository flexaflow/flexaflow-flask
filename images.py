"""Responsive images: srcset/sizes, lazy loading and WebP/AVIF sources for images in post and page content."""
import os
import re

from markupsafe import Markup
from PIL import Image, ImageOps

IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
SRC_RE = re.compile(r"""\bsrc\s*=\s*(["'])/uploads/([A-Za-z0-9_.\-]+)\1""", re.I)
FORMATS = {"webp": "image/webp", "avif": "image/avif"}
_size_cache = {}


def configured_formats():
	"""Extra formats to generate, from FLEXAFLOW_IMAGE_FORMATS (default: webp). Unsupported ones are skipped."""
	from PIL import features
	wanted = [f.strip().lower() for f in os.getenv("FLEXAFLOW_IMAGE_FORMATS", "webp").split(",")]
	return [f for f in wanted if f in FORMATS and features.check(f)]


def _width(path):
	try:
		stamp = os.path.getmtime(path)
	except OSError:
		return None
	hit = _size_cache.get(path)
	if hit and hit[0] == stamp:
		return hit[1]
	try:
		with Image.open(path) as im:
			width = im.size[0]
	except Exception:
		return None
	_size_cache[path] = (stamp, width)
	return width


def save_alternates(img, name, size_name, thumb_dir):
	"""Write name-size.webp / .avif next to a thumbnail."""
	for fmt in configured_formats():
		try:
			img.convert("RGB").save(os.path.join(thumb_dir, f"{name}-{size_name}.{fmt}"), quality=80)
		except Exception:
			pass


def make_responsive(html, upload_dir, sizes_attr="(max-width: 800px) 100vw, 800px"):
	if not html or "<img" not in html:
		return html
	thumb_dir = os.path.join(upload_dir, "thumbnails")

	def one(match):
		tag = match.group(0)
		m = SRC_RE.search(tag)
		if not m:
			return tag
		filename = m.group(2)
		name, ext = os.path.splitext(filename)
		orig = os.path.join(upload_dir, filename)
		variants = []  # (url, width)
		w = _width(orig)
		if w:
			variants.append((f"/uploads/{filename}", w))
		for size in ("medium", "large"):
			p = os.path.join(thumb_dir, f"{name}-{size}{ext}")
			tw = _width(p)
			if tw:
				variants.append((f"/uploads/thumbnails/{name}-{size}{ext}", tw))
		unique = {}
		for url, width in variants:
			unique.setdefault(width, url)
		new = tag
		if len(unique) >= 2 and "srcset=" not in tag.lower():
			srcset = ", ".join(f"{u} {w}w" for w, u in sorted(unique.items()))
			new = new[:-1].rstrip("/ ") + f' srcset="{srcset}"' + ("" if "sizes=" in tag.lower() else f' sizes="{sizes_attr}"') + ">"
		if "loading=" not in tag.lower():
			new = new[:-1].rstrip("/ ") + ' loading="lazy">'
		if "decoding=" not in tag.lower():
			new = new[:-1].rstrip("/ ") + ' decoding="async">'
		sources = []
		for fmt, mime in FORMATS.items():
			alts = []
			for size in ("medium", "large"):
				p = os.path.join(thumb_dir, f"{name}-{size}.{fmt}")
				tw = _width(p)
				if tw:
					alts.append((tw, f"/uploads/thumbnails/{name}-{size}.{fmt}"))
			if alts:
				sources.append(f'<source type="{mime}" srcset="{", ".join(f"{u} {w}w" for w, u in sorted(alts))}" sizes="{sizes_attr}">')
		if sources:
			new = "<picture>" + "".join(sources) + new + "</picture>"
		return new

	# images already inside a <picture> are left alone
	parts = re.split(r"(<picture\b.*?</picture>)", html, flags=re.I | re.S)
	return "".join(p if p.lower().startswith("<picture") else IMG_RE.sub(one, p) for p in parts)


def responsive_filter(upload_dir):
	def _filter(html):
		return Markup(make_responsive(str(html or ""), upload_dir))
	return _filter


# ---- upload verification --------------------------------------------------------------------------------------------
# The stored extension and MIME type come from the format Pillow actually decoded, never from the client.
SAFE_FORMATS = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "GIF": (".gif", "image/gif"), "WEBP": (".webp", "image/webp")}
MAX_IMAGE_PIXELS = 50_000_000  # refuses decompression bombs; Pillow raises DecompressionBombError above 2x this


class UnsafeImage(ValueError):
	"""The upload is not a real, supported image."""


def store_verified_image(source, dest_dir, stem):
	"""Verify an uploaded image, re-encode it without metadata and save it under dest_dir.

	source is a binary file object or a path. Only PNG, JPEG, GIF and WEBP are accepted (no SVG).
	Re-encoding drops EXIF/GPS data and any trailing bytes, so a file that is also valid HTML or script cannot survive.
	The file is written only after the whole image decoded; nothing is left behind on failure.
	Returns (filename, mime_type, width, height). Raises UnsafeImage.
	"""
	Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
	try:
		with Image.open(source) as probe:
			fmt = probe.format
			probe.verify()
		if fmt not in SAFE_FORMATS:
			raise UnsafeImage("Unsupported image format.")
		if hasattr(source, "seek"):
			source.seek(0)
		with Image.open(source) as img:
			img.load()
			animated = getattr(img, "is_animated", False) and fmt in ("GIF", "WEBP")
			if animated:
				frames = []
				for i in range(img.n_frames):
					img.seek(i)
					frames.append(img.copy())
				first, rest = frames[0], frames[1:]
				width, height = first.size
			else:
				first = ImageOps.exif_transpose(img) if fmt == "JPEG" else img.copy()
				rest = []
				width, height = first.size
			ext, mime = SAFE_FORMATS[fmt]
			filename = f"{stem}{ext}"
			os.makedirs(dest_dir, exist_ok=True)
			path = os.path.join(dest_dir, filename)
			try:
				if fmt == "JPEG":
					if first.mode not in ("RGB", "L", "CMYK"):
						first = first.convert("RGB")
					first.save(path, "JPEG", quality=92, optimize=True)
				elif animated:
					first.save(path, fmt, save_all=True, append_images=rest, loop=img.info.get("loop", 0), duration=img.info.get("duration", 100))
				else:
					first.save(path, fmt)
			except Exception:
				try:
					os.remove(path)
				except OSError:
					pass
				raise
		return filename, mime, width, height
	except UnsafeImage:
		raise
	except Exception as exc:  # decode errors, bombs, truncated files
		raise UnsafeImage("The file is not a valid image.") from exc
