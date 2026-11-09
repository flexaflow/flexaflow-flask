"""SEO head tags for themes: title, description, canonical, Open Graph, JSON-LD, feed link.

Use {{ seo_head() }} inside <head>. It reads `post` or `page` and `settings` from the template context.
"""
import json
import re
from html import escape

from flask import request
from jinja2 import pass_context
from markupsafe import Markup


def _abs(base, url):
	if not url:
		return ""
	return url if re.match(r"^https?://", url) else base + (url if url.startswith("/") else "/" + url)


def _strip(text, limit=300):
	return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()[:limit]


@pass_context
def seo_head(ctx):
	settings = ctx.get("settings") or {}
	post, page = ctx.get("post"), ctx.get("page")
	item = post if isinstance(post, dict) else (page if isinstance(page, dict) else {})
	seo = item.get("seo") or {}
	domain = (settings.get("site_domain") or "").strip().rstrip("/")
	base = domain if domain.startswith("http") else request.url_root.rstrip("/")
	site = settings.get("site_title") or ""
	title = seo.get("title") or item.get("title") or ""
	full_title = f"{title} - {site}" if title and site and title != site else (title or site)
	desc = (seo.get("description") or item.get("excerpt") or item.get("description")
	        or _strip(item.get("content")) or settings.get("site_description") or "")
	desc = _strip(desc, 300)
	canonical = seo.get("canonical") or (base + request.path)
	canonical = _abs(base, canonical)
	image = _abs(base, seo.get("og_image") or item.get("cover_image") or settings.get("default_og_image") or "")
	is_post = isinstance(post, dict)

	tags = [f"<title>{escape(full_title)}</title>"]
	if desc:
		tags.append(f'<meta name="description" content="{escape(desc)}">')
	tags.append(f'<link rel="canonical" href="{escape(canonical)}">')
	for prop, val in (("og:title", title or site), ("og:description", desc), ("og:url", canonical),
	                  ("og:type", "article" if is_post else "website"), ("og:site_name", site), ("og:image", image)):
		if val:
			tags.append(f'<meta property="{prop}" content="{escape(val)}">')
	tags.append(f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">')
	tags.append(f'<link rel="alternate" type="application/atom+xml" title="{escape(site or "Feed")}" href="{base}/feed.xml">')

	ld = None
	if is_post:
		ld = {"@context": "https://schema.org", "@type": "BlogPosting", "headline": title, "description": desc,
		      "mainEntityOfPage": canonical, "datePublished": item.get("published_at") or item.get("created_at"),
		      "dateModified": item.get("updated_at"), "publisher": {"@type": "Organization", "name": site}}
		if image:
			ld["image"] = image
		if item.get("author"):
			ld["author"] = {"@type": "Person", "name": str(item["author"])}
	elif request.path == "/":
		ld = {"@context": "https://schema.org", "@type": "WebSite", "name": site, "url": base + "/",
		      "potentialAction": {"@type": "SearchAction", "target": base + "/search?q={query}", "query-input": "required name=query"}}
	elif item:
		ld = {"@context": "https://schema.org", "@type": "WebPage", "name": title, "url": canonical, "description": desc}
	if ld:
		ld = {k: v for k, v in ld.items() if v}
		body = json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
		tags.append(f'<script type="application/ld+json">{body}</script>')
	return Markup("\n    ".join(tags))
