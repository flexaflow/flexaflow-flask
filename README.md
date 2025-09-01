# FlexaFlow CMS

<div align="center">

<img src="static/flexaflow.png" alt="FlexaFlow CMS" width="200"/>

[![Python](https://img.shields.io/badge/Python-3.7%2B-blue)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.0%2B-green)](https://flask.palletsprojects.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-1.4%2B-red)](https://www.sqlalchemy.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

FlexaFlow is a modern, enterprise-grade Content Management System built with Python Flask, designed for performance, security, and extensibility.




</div>

## Highlights

- **Lightning Fast**: Built for performance with optimized database queries
- **2FA Security**: Two-factor authentication, CSRF protection, and more
- **Mobile-First**: Responsive design that works on all devices
- **Theme System**: Easy-to-customize theming with Jinja2 templates
- **Custom Architecture**: Extensible system for custom functionality
- **Analytics**: Built-in support for Google Analytics and custom tracking

## Features

### Content Management
- **Pages & Posts**
  - Create and manage static pages and dynamic blog posts
  - Draft system with preview functionality
  
- **Rich Content Editor**
  - TinyMCE integration with custom plugins
  - Easy image uploads
  - Table management
  - Embedded media support

### Media Management
- **Advanced Media Library**
  - Image optimization
  - Automatic thumbnail generation
  

### Security Features
- **Two-Factor Authentication (2FA) ..Google Authenticator supports**
  - Multiple authenticator app support
  - QR code / manual key entry
  - Time based authentication




##  Getting Started

### Prerequisites

- Python 3.7 or higher
- pip package manager
- Virtual environment (recommended)
- SQLite, MySQL, or PostgreSQL

### Quick Start

1. **Clone the Repository**
   ```bash
   git clone https://github.com/flexaflow/flexaflow-flask.git
   cd flexaflow-flask
   ```

2. **Set Up Virtual Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\\Scripts\\activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration**
   Create a .env file:
   ```env
   FLASK_ENV=development
   SECRET_KEY=your_secure_secret_key
   DATABASE_URL=sqlite:///cms.db
   UPLOAD_FOLDER=uploads
   MAX_CONTENT_LENGTH=16777216
   ```

5. **Initialize and Run**
   ```bash
   python app.py
   ```

Visit http://localhost:5000/setup to complete installation.



##  Configuration

### Database Setup

Support for multiple databases:

```python
# SQLite (Default)
DATABASE_URL=sqlite:///cms.db

# MySQL
DATABASE_URL=mysql://user:password@localhost/dbname

# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost/dbname
```


## Theming

FlexaFlow uses a powerful theming system:

# Current default theme structure
```
├── functions
│   └── custom-function.py
└── templates
    ├── 404.html
    ├── category.html
    ├── main
    │   ├── contact-form.html
    │   ├── footer.html
    │   ├── header.html
    │   └── master.html
    ├── post.html
    ├── search.html
    ├── single-page.html
    └── tag.html

```




## License

Distributed under the MIT License. See `LICENSE` for more information.



## Hosting & Deployment

FlexaFlow CMS can be deployed on various hosting platforms. Here are our recommended options:

### Recommended Hosting Platforms

#### **[PythonAnywhere](https://www.pythonanywhere.com/)** ⭐ *Best Overall*
- **Perfect for beginners and professionals**
- Offers MySQL database included in plans
- Easy Flask deployment with web-based console
- Free tier available for testing
- Automatic SSL certificates
- **Setup**: Upload your code via web interface, configure WSGI file

#### **[Render](https://render.com/)**
- **Great for modern deployments**
- Free tier with automatic SSL
- Git-based deployments
- Built-in PostgreSQL database options
- Easy environment variable management
- **Setup**: Connect your GitHub repo and deploy automatically

#### **[Heroku](https://www.heroku.com/)**
- **Enterprise-grade platform**
- Extensive add-on marketplace
- Git-based deployment workflow
- PostgreSQL support via Heroku Postgres
- **Setup**: Use Procfile and requirements.txt for deployment




## Acknowledgments

### Lead Developer
- [Mashiur Rahman](https://github.com/01one) - Creator and lead developer of FlexaFlow CMS

### Core Technologies
- [Flask](https://flask.palletsprojects.com/) - The lightweight WSGI web application framework
- [SQLAlchemy](https://www.sqlalchemy.org/) - The Python SQL toolkit and ORM
- [TinyMCE](https://www.tiny.cloud/) - The powerful rich text editor
- [Bootstrap](https://getbootstrap.com/) - The responsive front-end framework





---

<div align="center">
</div>

