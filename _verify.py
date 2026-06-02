import sys
sys.stdout.reconfigure(encoding='utf-8')
from post_to_blog import wp_connect, WP_SITE, WP_USER, WP_PASS
session = wp_connect(WP_SITE, WP_USER, WP_PASS)

checks = [(36426, 37088, 'Doc 1 - Lip Filler Swelling Timeline'),
          (36430, 37091, 'Doc 2 - How Much Lip Filler')]

def title_val(obj):
    if isinstance(obj, dict):
        return obj.get('raw') or obj.get('rendered') or 'NOT SET'
    return obj or 'NOT SET'

for pid, mid, label in checks:
    rp = session.get(WP_SITE + f'/wp-json/wp/v2/posts/{pid}', params={'context': 'edit'})
    p  = rp.json()
    yj = p.get('yoast_head_json') or {}
    rm = session.get(WP_SITE + f'/wp-json/wp/v2/media/{mid}', params={'context': 'edit'})
    m  = rm.json()
    print(f'=== {label} (Post {pid}) ===')
    print(f'  Slug      : {p.get("slug")}')
    print(f'  Status    : {p.get("status")}')
    print(f'  SEO title : {yj.get("title", "NOT SET")}')
    print(f'  SEO desc  : {yj.get("description", "NOT SET")[:90]}')
    print(f'  Img alt   : {m.get("alt_text", "NOT SET")}')
    print(f'  Img title : {title_val(m.get("title"))}')
    print(f'  Img desc  : {title_val(m.get("description"))[:60]}')
    print()
