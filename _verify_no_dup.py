import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
from post_to_blog import wp_connect, WP_SITE, WP_USER, WP_PASS

session = wp_connect(WP_SITE, WP_USER, WP_PASS)

for post_id, label in [(36426, 'Doc 1'), (36430, 'Doc 2')]:
    r = session.get(f'{WP_SITE}/wp-json/wp/v2/posts/{post_id}', params={'context': 'edit'})
    post = r.json()
    content = post.get('content', {}).get('raw', '')
    starts_with_image = bool(re.match(r'^\s*<!--\s*wp:image', content))
    first_block = re.match(r'^\s*(<!--\s*wp:\w+)', content)
    first_block_type = first_block.group(1) if first_block else '(empty)'
    fm = post.get('featured_media', 0)
    print(f'{label} (Post {post_id}):')
    print(f'  Featured media ID : {fm}')
    print(f'  Content starts with image block : {starts_with_image}')
    print(f'  First content block : {first_block_type}')
    print()
