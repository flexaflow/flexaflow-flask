"""The public site: home, posts, categories, tags, search, pages, sitemap and health check."""
import sys as _sys

from flask import Blueprint

bp = Blueprint("public", __name__)

# Names defined in app.py (helpers, stores, settings) are shared with this module.
# This file is imported at the end of app.py, once all of them exist.
_core = _sys.modules["app"]
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and k not in ("bp",)})


@bp.route("/healthz")
def healthz():
	"""Liveness/readiness probe: 200 when the database answers, 503 otherwise. No auth, no private data."""
	from sqlalchemy import text as _sql
	try:
		with db_manager.engine.connect() as conn:
			conn.execute(_sql("SELECT 1"))
	except Exception:
		logger.exception("health check failed")
		return jsonify({"status": "error"}), 503
	return jsonify({"status": "ok", "version": CORE_VERSION})


@bp.route("/")
def home():
	"""Home page view that displays published posts with pagination.  Features: - Displays most recent published posts - Paginates posts (10 per page) - Shows tag cloud and categories - Redirects to setup if system is not configured  Returns: str: Rendered home page template with posts and metadata ..."""

	if not db_manager.is_setup_complete():
		return render_template("no_users.html")

	homepage_slug = str(get_site_settings().get("homepage") or "").strip()
	if homepage_slug:
		home_page = get_page_by_slug(homepage_slug)
		if home_page and home_page.get("status") == "published":
			return view_page(homepage_slug)

	try:
		page = max(1, int(request.args.get("page", 1)))
		posts_per_page = 10

		published_posts = []
		posts = get_posts()
		

		if not isinstance(posts, dict):
			posts = {}

		for slug, post in posts.items():
			if post.get("status") == "published":
				post_copy = post.copy()
				post_copy["slug"] = slug
				published_posts.append(post_copy)

		published_posts.sort(
			key=lambda x: x.get("created_at", x.get("updated_at", "")), reverse=True
		)

		total_posts = len(published_posts)
		total_pages = max(1, (total_posts + posts_per_page - 1) // posts_per_page)

		page = min(page, total_pages)

		start = (page - 1) * posts_per_page
		end = start + posts_per_page
		current_posts = published_posts[start:end]

		pagination = {
			"current_page": page,
			"total_pages": total_pages,
			"pages": range(1, total_pages + 1),
			"prev_page": page - 1 if page > 1 else None,
			"next_page": page + 1 if page < total_pages else None,
		}

		tags = get_tags()
		if not isinstance(tags, dict):
			tags = {}
		max_tag_count = (
			max([tag.get("count", 0) for tag in tags.values()]) if tags else 1
		)

		categories = get_categories()
		if not isinstance(categories, dict):
			categories = {}

		default_page = {
			"title": "Home",
			"description": get_site_settings().get(
				"site_description", "Welcome to our website"
			),
		}


		return render_template(
			"main/master.html",
			page=default_page,
			posts=current_posts,
			pagination=pagination,
			categories=categories,
			tags=tags,
			max_tag_count=max_tag_count,
			error=None,
		)

	except Exception as e:
		default_page = {
			"title": "Home",
			"description": get_site_settings().get(
				"site_description", "Welcome to our website"
			),
		}

		return render_template(
			"main/master.html",
			page=default_page,
			posts=[],
			pagination={
				"current_page": 1,
				"total_pages": 1,
				"pages": [1],
				"prev_page": None,
				"next_page": None,
			},
			categories={},
			tags={},
			max_tag_count=1,
			error=str(e),
		)


@bp.route("/post/<slug>")
def view_post(slug):
	"""Displays a single blog post and its related content.  Features: - Shows full post content - Displays related posts based on tags - Shows categories and tags - Handles both published and draft posts - Limits related posts to 3 items  Args: slug (str): URL slug of the post to display  Returns:..."""
	post = get_post_by_slug(slug)

	if not post:
		abort(404)

	if post.get("status") == "published" and (post.get("category") or {}).get("section_path"):
		return redirect(post_url(post), code=301)

	if post.get("status") != "published":
		me = current_user()
		if not me or not _can_edit_post(post, me):
			abort(404)  # drafts, scheduled and pending posts are not public

	categories = get_categories()
	tags = get_tags()

	related_posts = []
	post_tags = post.get("tags", [])
	all_posts = get_posts()

	for other_slug, other_post in all_posts.items():
		if other_slug != slug and other_post.get("status") == "published":
			other_tags = other_post.get("tags", [])
			if set(post_tags) & set(other_tags):
				other_post_copy = other_post.copy()
				other_post_copy["slug"] = other_slug
				related_posts.append(other_post_copy)
				if len(related_posts) >= 3:
					break

	post["slug"] = slug

	return render_template(
		"post.html",
		post=post,
		related_posts=related_posts,
		categories=categories,
		tags=tags,
		comments_enabled=features.comments_enabled(),
		comments=features.approved_comments(post["id"]) if features.comments_enabled() else [],
	)


@bp.route("/category/<category_slug>")
def view_category(category_slug):
	"""Displays posts from a specific category.  Features: - Lists all posts in category - Shows category metadata - Filters by published status - Updates post counts - 404 handling for invalid categories  URL Parameters: category_slug (str): URL slug of the category  Template Context: - category:..."""


	categories = get_categories()
	if category_slug in categories and categories[category_slug].get("section_path"):
		return redirect(f"/{categories[category_slug]['section_path']}/", code=301)
	if category_slug in categories:
		category_posts = []
		all_posts = get_posts()

		for slug, post in all_posts.items():
			if (
				(post.get("category") or {}).get("slug") == category_slug
				and post.get("status") == "published"
			):
				post_copy = post.copy()
				post_copy["slug"] = slug
				category_posts.append(post_copy)


		category_posts.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
		category_data = categories[category_slug]
		category_data["count"] = len(category_posts)
		page_posts, pager = paginate_list(category_posts)

		return render_template(
			"category.html", category=category_data, posts=page_posts, pager=pager
		)
	else:
		abort(404)


@bp.route("/tag/<tag_name>")
def view_tag(tag_name):
	"""Displays all posts with a specific tag.  Features: - Lists all posts with tag - Shows tag statistics - Filters by published status - Orders by publication date  URL Parameters: tag_name (str): Name of the tag to filter by  Template Context: tag: Tag information including: - name: Tag name -..."""

	tagged_posts = []
	all_posts = get_posts()

	for slug, post in all_posts.items():
		if tag_name in post.get("tags", []) and post.get("status") == "published":
			post_copy = post.copy()
			post_copy["slug"] = slug
			tagged_posts.append(post_copy)

	tagged_posts.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
	page_posts, pager = paginate_list(tagged_posts)
	return render_template(
		"tag.html",
		tag={"name": tag_name, "count": len(tagged_posts)},
		posts=page_posts,
		pager=pager,
	)


@bp.route("/search")
def search():
	"""Performs a full-text search across posts.  Features: - Searches in title, content, excerpt, and tags - Case-insensitive search - Only returns published posts - Supports empty search query  Query Parameters: q (str): Search query string  Returns: str: Rendered search results template with: -..."""

	query = request.args.get("q", "")
	search_results = []

	if query:
		search_results = search_posts(query)

	total_results = len(search_results)
	search_results, pager = paginate_list(search_results)
	return render_template(
		"search.html", query=query, results=search_results, total_results=total_results, pager=pager
	)


@bp.route("/<slug>")
def view_page(slug):
	"""
	Public page. The page's template decides the layout: plain pages use the theme's
	master template; templates with custom fields receive them as ``f``.
	"""
	if get_section_by_path(slug):
		return section_index_view(slug)
	page = get_page_by_slug(slug)
	if not page or page.get("status") != "published":
		abort(404)
	template_key = _resolve_template(page.get("template"))
	template = _core.PAGE_TEMPLATES[template_key]
	if template_key == DEFAULT_TEMPLATE_KEY:
		return render_template("main/master.html", page=page)
	values = fill_defaults(page.get("fields") or {}, template["fields"])
	return render_template(template["template_file"], page=page, f=values)


@bp.route('/sitemap.xml')
def sitemap():
	"""
	Serves the pre-generated sitemap from the dedicated sitemaps table.
	"""
	sitemap_xml = db_manager.get_sitemap_content()
	
	if not sitemap_xml:
		logger.info("generating sitemap")
		sitemap_task=generate_and_store_sitemap()
		if not sitemap_task:
			return '<!-- Sitemap not available -->'
		sitemap_xml = db_manager.get_sitemap_content() or '<!-- Sitemap not available -->'

	response = make_response(sitemap_xml)
	response.headers["Content-Type"] = "application/xml"
	return response
