"""SEO audit: one admin report, no database tables."""
import re
from collections import Counter

from data_store import get_pages, get_posts

IMG_NO_ALT = re.compile(r"<img\b(?![^>]*\balt\s*=\s*[\"'][^\"']+[\"'])[^>]*>", re.I)


def audit():
	items = [("post", s, p) for s, p in get_posts().items()] + [("page", s, p) for s, p in get_pages().items() if p.get("status") == "published"]
	titles = Counter((i[2].get("seo", {}).get("title") or i[2].get("title") or "").strip().lower() for i in items)
	report = []
	for kind, slug, item in items:
		seo = item.get("seo") or {}
		title = (seo.get("title") or item.get("title") or "").strip()
		desc = (seo.get("description") or item.get("excerpt") or item.get("description") or "").strip()
		problems = []
		if not desc:
			problems.append("No meta description")
		elif len(desc) < 50:
			problems.append(f"Description is short ({len(desc)} characters)")
		elif len(desc) > 160:
			problems.append(f"Description may be cut off in results ({len(desc)} characters)")
		if len(title) < 10:
			problems.append("Title is very short")
		elif len(title) > 60:
			problems.append(f"Title may be cut off in results ({len(title)} characters)")
		if titles[title.lower()] > 1:
			problems.append("Another page has the same title")
		missing = len(IMG_NO_ALT.findall(item.get("content") or ""))
		if missing:
			problems.append(f"{missing} image(s) without alt text")
		if problems:
			report.append({"kind": kind, "slug": slug, "title": title or slug, "problems": problems,
			               "edit": f"/{kind}/edit/{slug}"})
	return sorted(report, key=lambda r: -len(r["problems"])), len(items)


def register(api):
	@api.admin_route("/", capability="manage_pages")
	def report():
		rows, total = audit()
		return api.render("report.html", rows=rows, total=total)

	api.add_admin_menu("SEO", [{"label": "SEO audit", "url": "/admin/ext/seo-audit/"}], capability="manage_pages")
