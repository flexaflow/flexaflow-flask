from datetime import datetime

def format_date(date_string):
    """Convert date string to formatted date"""
    if not date_string:
        return ""
    try:
        if isinstance(date_string, datetime):
            date_obj = date_string
        else:
            date_obj = datetime.strptime(str(date_string), '%Y-%m-%d')
        return date_obj.strftime('%B %d, %Y')
    except (ValueError, TypeError):
        return str(date_string)

def truncate_text(text, length=200):
    """Truncate text to specified length and add ellipsis"""
    if not text:
        return ""
    text = str(text)
    if len(text) <= length:
        return text
    return text[:length].rsplit(' ', 1)[0] + '...'

def get_reading_time(content):
    """Calculate estimated reading time"""
    if not content:
        return 1
    words_per_minute = 200
    word_count = len(str(content).split())
    minutes = word_count / words_per_minute
    return round(minutes) or 1

def generate_excerpt(content):
    """Generate post excerpt"""
    return truncate_text(content, 150)

def process_page_data(page_data):
    """Process page data before rendering"""
    if not isinstance(page_data, dict):
        return page_data
        
    processed = page_data.copy()
    if 'content' in processed:
        processed['excerpt'] = generate_excerpt(processed['content'])
        processed['reading_time'] = get_reading_time(processed['content'])
    if 'date' in processed:
        processed['formatted_date'] = format_date(processed['date'])
    if 'last_modified' in processed:
        processed['formatted_last_modified'] = format_date(processed['last_modified'])
    return processed