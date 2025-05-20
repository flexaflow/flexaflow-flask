import os
import uuid
import json
import datetime
from flask import (
    Flask, render_template, request, redirect, 
    url_for, jsonify, send_from_directory, flash,
    abort
)
from werkzeug.utils import secure_filename
from jinja2 import Environment, FileSystemLoader
from utils.theme_loader import load_theme_functions
from data_store import (
    get_pages, get_draft_pages, get_posts, get_draft_posts,
    get_categories, get_tags, get_site_settings,
    add_page, add_draft_page, delete_page, add_post, add_draft_post,
    update_pages, update_draft_pages, update_posts, update_draft_posts,
    update_categories, update_tags, update_site_settings, update_tag_counts
)

site_settings = get_site_settings()
THEME_NAME = site_settings["theme"]
UPLOAD_DIR = "uploads"
THEME_PATH = f"themes/{THEME_NAME}"

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

app = Flask(__name__, 
            static_folder="static",
            template_folder=f"{THEME_PATH}/templates")

app.jinja_loader = FileSystemLoader([
    os.path.join(os.path.dirname(__file__), THEME_PATH, "templates"),
    os.path.join(os.path.dirname(__file__), "templates")
])

theme_functions = load_theme_functions(THEME_NAME)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_change_in_production')
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024


@app.context_processor
def inject_globals():

    data = {'year': datetime.datetime.now().year}
    for name, func in theme_functions.items():
        data[name] = func
    data['settings'] = get_site_settings()
    return data

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html', page={'title': '404 Not Found'}), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('404.html', page={'title': 'Error'}), 500

@app.route('/')
def home():
    try:
        page = max(1, int(request.args.get("page", 1)))
        posts_per_page = 10
        
        published_posts = []
        posts = get_posts()
        
        if not isinstance(posts, dict):
            posts = {}
            
        for slug, post in posts.items():
            if post.get('status') == 'published':
                post_copy = post.copy()
                post_copy['slug'] = slug
                if 'created_at' not in post_copy:
                    post_copy['created_at'] = datetime.datetime.now().isoformat()
                published_posts.append(post_copy)
        
        published_posts.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        total_posts = len(published_posts)
        total_pages = max(1, (total_posts + posts_per_page - 1) // posts_per_page)
        
        page = min(page, total_pages)
        
        start = (page - 1) * posts_per_page
        end = start + posts_per_page
        current_posts = published_posts[start:end]
        
        pagination = {
            'current_page': page,
            'total_pages': total_pages,
            'pages': range(1, total_pages + 1),
            'prev_page': page - 1 if page > 1 else None,
            'next_page': page + 1 if page < total_pages else None
        }
        
        tags = get_tags()
        if not isinstance(tags, dict):
            tags = {}
        max_tag_count = max([tag.get('count', 0) for tag in tags.values()]) if tags else 1
        categories = get_categories()
        if not isinstance(categories, dict):
            categories = {}
            
        for cat_slug in categories:
            categories[cat_slug]['count'] = sum(
                1 for post in posts.values() 
                if post.get('category') == cat_slug and post.get('status') == 'published'
            )
        
        default_page = {
            'title': 'Home',
            'description': get_site_settings().get('site_description', 'Welcome to our website')
        }
        
        return render_template("main/master.html", 
                           page=default_page,
                           posts=current_posts,
                           pagination=pagination,
                           categories=categories,
                           tags=tags,
                           max_tag_count=max_tag_count,
                           error=None)
                           
    except Exception as e:
        default_page = {
            'title': 'Home',
            'description': get_site_settings().get('site_description', 'Welcome to our website')
        }
        
        return render_template("main/master.html",
                           page=default_page,
                           posts=[],
                           pagination={'current_page': 1, 'total_pages': 1, 'pages': [1], 'prev_page': None, 'next_page': None},
                           categories={},
                           tags={},
                           max_tag_count=1,
                           error=str(e))

@app.route('/admin')
def admin():
    all_pages = []
    for slug, page in get_pages().items():
        page_copy = page.copy()
        page_copy["slug"] = slug
        all_pages.append(page_copy)
        
    for slug, page in get_draft_pages().items():
        page_copy = page.copy()  
        page_copy["slug"] = slug
        all_pages.append(page_copy)

    # Get all posts
    all_posts = []
    for slug, post in get_posts().items():
        post_copy = post.copy()
        post_copy["slug"] = slug
        all_posts.append(post_copy)
        
    for slug, post in get_draft_posts().items():
        post_copy = post.copy()
        post_copy["slug"] = slug
        all_posts.append(post_copy)
        
    return render_template("admin.html", pages=all_pages, posts=all_posts)

@app.route('/page/add', methods=['GET', 'POST'])
def add_page():
    if request.method == 'POST':
        slug = request.form.get("slug")
        title = request.form.get("title")
        description = request.form.get("description") 
        content = request.form.get("content")
        status = request.form.get("status", "draft")
        
        page_data = {
            "content": content,
            "title": title, 
            "description": description,
            "status": status,
            "created_at": datetime.datetime.now().isoformat()
        }
        
        if status == "published":
            add_page(slug, page_data)
            draft_pages = get_draft_pages()
            if slug in draft_pages:
                del draft_pages[slug]
                update_draft_pages(draft_pages)
        else:
            add_draft_page(slug, page_data)
            
        return redirect(url_for('admin'))
    
    return render_template("add_page.html")

@app.route('/page/edit/<slug>', methods=['GET', 'POST'])
def edit_page(slug):
    page = None
    if slug in get_pages():
        page = get_pages()[slug]
    elif slug in get_draft_pages():
        page = get_draft_pages()[slug]
        
    if not page:
        return "<h1>Page not found</h1>", 404
        
    if request.method == 'POST':
        title = request.form.get("title")
        description = request.form.get("description")
        content = request.form.get("content")
        status = request.form.get("status", "draft")
        
        page_data = {
            "content": content,
            "title": title,
            "description": description,
            "status": status,
            "updated_at": datetime.datetime.now().isoformat()
        }

        if status == "published":
            add_page(slug, page_data)
            draft_pages = get_draft_pages()
            if slug in draft_pages:
                del draft_pages[slug]
                update_draft_pages(draft_pages)
        else:
            add_draft_page(slug, page_data)
            pages = get_pages()
            if slug in pages:
                del pages[slug]
                update_pages(pages)
                
        return redirect(url_for('admin'))
        
    return render_template("edit_page.html", slug=slug, page=page)

@app.route('/page/preview/<slug>')
def preview_page(slug):
    page = None
    if slug in get_pages():
        page = get_pages()[slug]
    elif slug in get_draft_pages():
        page = get_draft_pages()[slug]
        
    if page:
        theme = {
            'use_custom_css': False,
            'custom_css_url': '',
            'custom_styles': '',
            'body_class': '',
            'content_class': '',
            'dark_mode': False
        }
        return render_template("preview.html", page=page, theme=theme)
    else:
        return "<h1>Page not found</h1>", 404

@app.route('/page/preview_draft', methods=['POST'])
def preview_draft():
    title = request.form.get("title")
    description = request.form.get("description")
    content = request.form.get("content")
    page = {"title": title, "description": description, "content": content}
    return render_template("preview.html", page=page)

@app.route('/page/delete/<slug>')
def delete_page(slug):
    delete_page(slug)
    return redirect(url_for('admin'))

@app.route('/post/add', methods=['GET', 'POST'])
def add_post():
    try:
        if request.method == 'POST':
            slug = request.form.get("slug", "").strip()
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            content = request.form.get("content", "").strip()
            category = request.form.get("category", "").strip()
            tag_list = request.form.get("tags", "").strip()
            status = request.form.get("status", "draft").strip()
            
            if not slug or not title or not content:
                raise ValueError("Slug, title and content are required")
                
            if slug in get_posts() or slug in get_draft_posts():
                raise ValueError(f"A post with slug '{slug}' already exists")
            
            post_tags = [tag.strip() for tag in tag_list.split(',') if tag.strip()]
            
            post_data = {
                "content": content,
                "title": title,
                "description": description or title,  # Use title as description if not provided
                "status": status,
                "category": category,
                "tags": post_tags,
                "created_at": datetime.datetime.now().isoformat(),
                "updated_at": datetime.datetime.now().isoformat(),
                "author": "Admin"  # You can modify this based on your auth system
            }
            
            if status == "published":
                add_post(slug, post_data)
                draft_posts = get_draft_posts()
                if slug in draft_posts:
                    del draft_posts[slug]
                    update_draft_posts(draft_posts)
            else:
                add_draft_post(slug, post_data)
            
            update_tag_counts()
            
            return redirect(url_for('admin'))
            
        categories = get_categories()
        if not categories:
            categories = {}
        return render_template("add_post.html", categories=categories, error=None)
        
    except Exception as e:
        return render_template("add_post.html", 
                            categories=get_categories(),
                            error=str(e),
                            form_data={
                                'slug': request.form.get("slug", ""),
                                'title': request.form.get("title", ""),
                                'description': request.form.get("description", ""),
                                'content': request.form.get("content", ""),
                                'category': request.form.get("category", ""),
                                'tags': request.form.get("tags", ""),
                                'status': request.form.get("status", "draft")
                            })

@app.route('/post/edit/<slug>', methods=['GET', 'POST'])
def edit_post(slug):
    post = None
    if slug in get_posts():
        post = get_posts()[slug]
    elif slug in get_draft_posts():
        post = get_draft_posts()[slug]
        
    if not post:
        return "<h1>Post not found</h1>", 404
        
    if request.method == 'POST':
        title = request.form.get("title")
        description = request.form.get("description")
        content = request.form.get("content")
        category = request.form.get("category")
        tag_list = request.form.get("tags", "")
        status = request.form.get("status", "draft")
        
        post_tags = [tag.strip() for tag in tag_list.split(',') if tag.strip()]
        
        existing_post = None
        if slug in get_posts():
            existing_post = get_posts()[slug]
        elif slug in get_draft_posts():
            existing_post = get_draft_posts()[slug]
            
        post_data = {
            "content": content,
            "title": title,
            "description": description,
            "status": status,
            "category": category,
            "tags": post_tags,
            "updated_at": datetime.datetime.now().isoformat()
        }
        
        if existing_post and 'created_at' in existing_post:
            post_data['created_at'] = existing_post['created_at']
        
        if existing_post and 'author' in existing_post:
            post_data['author'] = existing_post['author']

        if status == "published":
            add_post(slug, post_data)
            
            draft_posts = get_draft_posts()
            if slug in draft_posts:
                del draft_posts[slug]
                update_draft_posts(draft_posts)
        else:
            add_draft_post(slug, post_data)
            
            posts = get_posts()
            if slug in posts:
                del posts[slug]
                update_posts(posts)
        
        update_tag_counts()
        
        return redirect(url_for('admin'))
        
    return render_template("edit_post.html", slug=slug, post=post, categories=get_categories())

@app.route('/post/delete/<slug>')
def delete_post(slug):
    posts = get_posts()
    if slug in posts:
        del posts[slug]
        update_posts(posts)
        
    draft_posts = get_draft_posts()
    if slug in draft_posts:
        del draft_posts[slug]
        update_draft_posts(draft_posts)
        
    update_tag_counts()
    
    return redirect(url_for('admin'))

@app.route('/post/<slug>')
def view_post(slug):
    post = None
    posts = get_posts()
    if slug in posts:
        post = posts[slug]
        if post.get('status') != 'published':
            post = None
            
    if not post:
        draft_posts = get_draft_posts()
        if slug in draft_posts:
            post = draft_posts[slug]
    
    if not post:
        abort(404)
    
    categories = get_categories()
    tags = get_tags()
    
    related_posts = []
    post_tags = post.get('tags', [])
    
    for other_slug, other_post in posts.items():
        if other_slug != slug and other_post.get('status') == 'published':
            other_tags = other_post.get('tags', [])
            if set(post_tags) & set(other_tags):
                other_post_copy = other_post.copy()
                other_post_copy['slug'] = other_slug
                related_posts.append(other_post_copy)
                if len(related_posts) >= 3:
                    break
    
    return render_template("post.html", post=post, related_posts=related_posts, categories=categories, tags=tags)

@app.route('/category/<category_slug>')
def view_category(category_slug):
    categories = get_categories()
    if category_slug in categories:
        category_posts = []
        for slug, post in get_posts().items():
            if post.get('category') == category_slug and post.get('status') == 'published':
                post_copy = post.copy()
                post_copy['slug'] = slug
                category_posts.append(post_copy)
        
        categories[category_slug]['count'] = len(category_posts)
                
        return render_template("category.html", 
                            category=categories[category_slug],
                            posts=category_posts)
    else:
        abort(404)

@app.route('/tag/<tag_name>')
def view_tag(tag_name):
    tagged_posts = []
    for slug, post in get_posts().items():
        if tag_name in post.get('tags', []) and post.get('status') == 'published':
            post_copy = post.copy()
            post_copy['slug'] = slug
            tagged_posts.append(post_copy)
            
    return render_template("tag.html", 
                        tag={'name': tag_name, 'count': len(tagged_posts)},
                        posts=tagged_posts)

@app.route('/search')
def search():
    query = request.args.get("q", "")
    search_results = []
    
    if query:
        for slug, post in get_posts().items():
            if post.get('status') == 'published':
                if (query.lower() in post['title'].lower() or 
                    query.lower() in post['content'].lower() or
                    query.lower() in post['description'].lower() or
                    query.lower() in ','.join(post.get('tags', [])).lower()):
                    post_copy = post.copy()
                    post_copy['slug'] = slug
                    search_results.append(post_copy)
                    
    return render_template("search.html", 
                        query=query,
                        results=search_results)

@app.route('/admin/settings')
def settings():
    return render_template("settings.html", settings=get_site_settings(), pages=get_pages())

@app.route('/admin/menu')
def menu_editor():
    return render_template("menu-editor.html", menu_items=get_site_settings()["menu_items"], pages=get_pages())

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'GET':
        return jsonify(get_site_settings())
    elif request.method == 'POST':
        data = request.json
        settings = get_site_settings()
        settings.update(data)
        update_site_settings(settings)
        return jsonify({"status": "success", "message": "Settings updated"})

@app.route('/api/menu', methods=['GET', 'POST'])
def api_menu():
    if request.method == 'GET':
        return jsonify(get_site_settings()["menu_items"])
    elif request.method == 'POST':
        data = request.json
        settings = get_site_settings()
        settings["menu_items"] = data
        update_site_settings(settings)
        return jsonify({"status": "success", "message": "Menu updated"})

@app.route('/admin/category/add', methods=['POST'])
def add_category():
    name = request.form.get("name")
    description = request.form.get("description", "")
    slug = request.form.get("slug")
    
    categories = get_categories()
    categories[slug] = {
        "name": name,
        "description": description,
        "slug": slug
    }
    update_categories(categories)
    
    return redirect(url_for('admin'))

@app.route('/upload_image', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file:
        filename = str(uuid.uuid4()) + os.path.splitext(secure_filename(file.filename))[1]
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        image_url = f"/{UPLOAD_DIR}/{filename}"
        return jsonify({'location': image_url})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/<slug>')
def view_page(slug):
    pages = get_pages()
    if slug in pages:
        page = pages[slug]
        return render_template("main/master.html", page=page)
    else:
        abort(404)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
    print("Server is running on http://127.0.0.1:5000")
