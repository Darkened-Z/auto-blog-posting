import sys
sys.stdout.reconfigure(encoding='utf-8')
from post_to_blog import *

docs = [
    ('5', 'https://docs.google.com/document/d/1H0Mtw_2oIH4x-2Z8fAyogpPORRZI5ry-07QLMwbyqps/edit?usp=sharing'),
    ('6', 'https://docs.google.com/document/d/1m-ek9Ux33j5y6NaF9yS4B0dSleMRE6Nww2W3sifNPLU/edit?usp=sharing'),
]

session = wp_connect(WP_SITE, WP_USER, WP_PASS)

for num, url in docs:
    try:
        soup     = fetch_doc(get_doc_id(url))
        title    = extract_title(soup)
        slug     = extract_target_slug(soup) or make_slug(title)
        seo_t    = extract_seo_title(soup)
        seo_d    = extract_seo_description(soup)
        kw       = extract_primary_keyword(soup)
        alt      = extract_image_alt(soup)
        img_t    = extract_image_title(soup)
        img_d    = extract_image_description(soup)
        has_img  = bool((soup.find('body') or soup).find('img'))
        existing = find_post(session, WP_SITE, slug)
        print(f'=== DOC {num} ===')
        print(f'  title    : {title}')
        print(f'  slug     : {slug}')
        print(f'  seo_t    : {seo_t}')
        print(f'  seo_d    : {seo_d}')
        print(f'  focus_kw : {kw}')
        print(f'  img_alt  : {alt}')
        print(f'  img_title: {img_t}')
        print(f'  img_desc : {img_d}')
        print(f'  has_img  : {has_img}')
        print(f'  existing : Post {existing}' if existing else '  existing : (new post)')
        print()
    except Exception as e:
        print(f'=== DOC {num} ERROR: {e} ===')
        print()
