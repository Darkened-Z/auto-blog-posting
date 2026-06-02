import sys, re
sys.stdout.reconfigure(encoding='utf-8')
from post_to_blog import fetch_doc, get_doc_id

problem_docs = {
    '11': 'https://docs.google.com/document/d/1yIlOfGb1KJ0HH3WEhhxfuhbP-x3Td61jRtVyzVcDCAQ/edit',
    '14': 'https://docs.google.com/document/d/18yU_OyHMFmlMJZZiRpclfMFPRRtp0nqkgXEUnMKeEXA/edit',
    '15': 'https://docs.google.com/document/d/1TmwN4X9fZE6ocGc5uD8K4pf-34ZERd5v1NjZEPx6wdY/edit',
    '18': 'https://docs.google.com/document/d/1S3URebOTB-6yly6Kw-Vak886lE5iuCaovFPtWONgIcY/edit',
}

for num, url in problem_docs.items():
    print(f'\n=== DOC {num} — SEO block elements ===')
    soup = fetch_doc(get_doc_id(url))
    for tag in soup.find_all(['h1','h2','h3','h4','p']):
        t = tag.get_text(separator=' ', strip=True)
        if any(x in t.lower() for x in ['meta', 'seo', 'description', 'title', 'keyphrase', 'focus']):
            if len(t) < 300:
                print(f'  {tag.name}: {repr(t)}')
