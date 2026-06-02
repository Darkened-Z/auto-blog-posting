"""Batch post docs 21-30 in sequence and report results."""
import sys, subprocess
sys.stdout.reconfigure(encoding='utf-8')
from post_to_blog import wp_connect, WP_SITE, WP_USER, WP_PASS, update_media_metadata

docs = [
    ('21', 'https://docs.google.com/document/d/12QYI2fXc5O0b_Ed6JpJi9HHsPNXq0z1aUDL5XigEJOc/edit?usp=sharing'),
    ('22', 'https://docs.google.com/document/d/1uZYp3O7idXI7EZdQBnoK5qGIu5rZMneq9H-5PttHWho/edit?usp=sharing'),
    ('23', 'https://docs.google.com/document/d/1KAYft52KPuFWJ13XtXwzUi2zxrGn9NYU7B4sIg3fzGw/edit?usp=sharing'),
    ('24', 'https://docs.google.com/document/d/1JC6ZFiHUPeGDyl1Q04Q4rUBkGW9hLnAIRXobOC8ADL0/edit?usp=sharing'),
    ('25', 'https://docs.google.com/document/d/1MhOUZd5Tjwnvmmjqrcp18JrfRSunTbLfVh3c5BKiq7g/edit?usp=sharing'),
    ('26', 'https://docs.google.com/document/d/1TiukhsCh3AG9vHjkpFpEKsA3H7EVSAbGRxA0_-BlZ2M/edit?usp=sharing'),
    ('27', 'https://docs.google.com/document/d/1ZcToMctvDnRzH_MfQ4BUvBGRyt85I4DsclG-p8V5wYA/edit?usp=sharing'),
    ('28', 'https://docs.google.com/document/d/16Ckg6_CMjxaI3Vw6csiww5_fCtZgYmuYlXhegI-npBk/edit?usp=sharing'),
    ('29', 'https://docs.google.com/document/d/1rxEAvQtAO1XFYTCAKP1qWe5ilgIrKcAPlFf82PIJrc0/edit?usp=sharing'),
    ('30', 'https://docs.google.com/document/d/1XS-7iSgibJMb_ESX4lyGrmpHfnAOHMh928CyyMwEo3k/edit?usp=sharing'),
]

results = []

for num, url in docs:
    print(f'\n{"="*60}')
    print(f'  POSTING DOC {num}')
    print(f'{"="*60}')
    proc = subprocess.run(
        [sys.executable, 'post_to_blog.py', url],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        cwd='D:\\Work\\Blogs of nexus'
    )
    output = proc.stdout + proc.stderr
    print(output)

    post_id, post_url = None, None
    for line in output.splitlines():
        if line.strip().startswith('ID     :'):
            post_id = line.split(':', 1)[1].strip()
        if line.strip().startswith('URL    :'):
            post_url = line.split('URL    :', 1)[1].strip()

    results.append((num, post_id, post_url, proc.returncode))

    # Doc 26: fix "kaula lampur" typo in featured image title
    if num == '26' and post_id:
        print(f'  [fix] Correcting image title typo for doc 26 (post {post_id})...')
        try:
            session = wp_connect(WP_SITE, WP_USER, WP_PASS)
            rp = session.get(f'{WP_SITE}/wp-json/wp/v2/posts/{post_id}',
                             params={'context': 'edit'})
            fm_id = rp.json().get('featured_media', 0)
            if fm_id:
                update_media_metadata(
                    session, WP_SITE, fm_id,
                    title='Karisma Skin Booster Price Malaysia'
                )
                print(f'  [fix] Image title corrected on media {fm_id}')
        except Exception as e:
            print(f'  [fix] Could not patch image title: {e}')

print(f'\n{"="*60}')
print('  SUMMARY')
print(f'{"="*60}')
for num, pid, url, rc in results:
    status = 'OK' if rc == 0 else 'FAILED'
    print(f'  Doc {num:>2} | {status} | Post {pid} | {url}')
