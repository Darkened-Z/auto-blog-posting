import sys, json
sys.stdout.reconfigure(encoding='utf-8')
from post_to_blog import wp_connect, WP_SITE, WP_USER, WP_PASS
session = wp_connect(WP_SITE, WP_USER, WP_PASS)

mid = 37088
# GET current state
r = session.get(WP_SITE + f'/wp-json/wp/v2/media/{mid}')
m = r.json()
print('=== CURRENT STATE ===')
print('title field type:', type(m.get('title')))
print('title raw       :', m.get('title'))
print('alt_text        :', m.get('alt_text'))
print('description     :', str(m.get('description'))[:80])
print()

# PATCH
body = {'title': 'Lip filler aftercare: Nexus clinic',
        'description': 'Lip Filler aftercare timeline Malaysia',
        'alt_text': 'Lip filler aftercare timeline in Malaysia'}
r2 = session.post(WP_SITE + f'/wp-json/wp/v2/media/{mid}', data=json.dumps(body))
print('PATCH status:', r2.status_code)
m2 = r2.json()
print('After PATCH:')
print('  title      :', m2.get('title'))
print('  alt_text   :', m2.get('alt_text'))
print('  description:', str(m2.get('description'))[:80])
