"""Remove the leading wp:image block from posts 36426 and 36430 (duplicate featured image in content)."""
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
from post_to_blog import wp_connect, WP_SITE, WP_USER, WP_PASS

session = wp_connect(WP_SITE, WP_USER, WP_PASS)

for post_id in (36426, 36430):
    r = session.get(f'{WP_SITE}/wp-json/wp/v2/posts/{post_id}', params={'context': 'edit'})
    post = r.json()
    content = post.get('content', {}).get('raw', '')

    # Remove the first wp:image block if it appears at the very start of the content
    cleaned = re.sub(
        r'^<!--\s*wp:image\b[^>]*-->.*?<!--\s*/wp:image\s*-->\s*',
        '', content, count=1, flags=re.DOTALL
    )

    if cleaned == content:
        print(f'Post {post_id}: no leading image block found — skipping')
        continue

    # Save cleaned content
    r2 = session.post(
        f'{WP_SITE}/wp-json/wp/v2/posts/{post_id}',
        data=json.dumps({'content': cleaned})
    )
    if r2.status_code in (200, 201):
        print(f'Post {post_id}: duplicate image block removed [OK]')
    else:
        print(f'Post {post_id}: update failed ({r2.status_code}): {r2.text[:100]}')
