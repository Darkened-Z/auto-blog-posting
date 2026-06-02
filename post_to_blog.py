"""
post_to_blog.py  —  Google Docs -> WordPress in one command
Usage:
  python post_to_blog.py <google_doc_url> [options]

Options:
  --slug SLUG       Use/create post with this slug (else auto-generated from title)
  --id   ID         Update an existing post by WP post ID
  --draft           Save as draft instead of publishing
  --images FOLDER   Upload all images in FOLDER and distribute one after each H2
  --links  FILE     JSON file mapping keyword phrases -> URLs for inline linking

Examples:
  python post_to_blog.py "https://docs.google.com/document/d/ABC123/edit"
  python post_to_blog.py "https://docs.google.com/document/d/ABC123/edit" --draft
  python post_to_blog.py "https://docs.google.com/document/d/ABC123/edit" --images ./images --links links.json

════════════════════════════════════════════════════════════════
  KNOWN GOOGLE DOCS EXPORT QUIRKS — all handled below
════════════════════════════════════════════════════════════════

1. SEO META BLOCKS — TWO FORMATS
   Format A (old):  <h3>SEO Meta Title</h3>  <p>value</p>
                    <h3>SEO Meta Description</h3>  <p>value</p>
   Format B (new):  <p>SEO META</p>
                    <p>Meta Title: ...</p>
                    <p>Meta Description: ...</p>
                    <p>Target URL: ...</p>  etc.
   Both the label AND its value sibling must be stripped.
   -> Fixed in: remove_seo_meta()

2. HEADER IMAGE LOCATION (varies per doc)
   Image can be in: h1 > span > img, SEO meta p > span > img, standalone p > span > img
   Must extract BEFORE remove_seo_meta() runs.
   -> Fixed in: main() — image upload happens before remove_seo_meta()

3. LINK SPACING
   Google inserts empty spacer <a> before real links carrying the space.
   Must .replace_with(' ') not .decompose() to preserve space.
   -> Fixed in: fix_links()

4. GOOGLE REDIRECT URLS
   All links wrapped in https://www.google.com/url?q=REAL_URL&...
   -> Fixed in: fix_links()

5. LINKS INSIDE SPANS
   Google always wraps link text in <span>. Must recurse through spans.
   -> Fixed in: inner_html()

6. INTER-SPAN SPACE LOSS
   When content is split across adjacent <span> tags (e.g. "Visit our </span>
   <span>link text</span><span> here"), naive .strip() inside inner_html()
   drops the boundary spaces. Fixed by NOT stripping inside inner_html()
   and only stripping at the call sites.
   -> Fixed in: inner_html() returns unsrtipped, elem_to_block strips before check

7. TITLE EXTRACTION
   Priority: HTML <title> tag -> "Meta Title:" paragraph -> SEO Meta Title para -> first h1
   Must extract BEFORE SEO meta is stripped.
   -> Fixed in: extract_title(), called before remove_seo_meta()

8. TABLES
   Google adds <p> in cells, colspan/rowspan=1 everywhere. Strip and add thead/tbody.
   -> Fixed in: elem_to_block() table handler

9. LINK ANNOTATIONS  [LINK: /path/]
   Doc templates may annotate text with [LINK: /path/] to indicate where links
   should go. These are converted to real <a> tags during rendering.
   -> Fixed in: inner_html() NavigableString handler

10. LOCAL IMAGE DISTRIBUTION
    When a doc has no embedded images, pass --images <folder> to upload local
    files and insert one after each H2 heading block.
    -> Handled in: build_gutenberg() with local_images parameter
════════════════════════════════════════════════════════════════
"""

import re, sys, argparse, base64, mimetypes, json, os, requests, html as _html
from bs4 import BeautifulSoup, NavigableString, Tag
from urllib.parse import unquote

# ── Config ────────────────────────────────────────────────────────────────────
WP_SITE = "https://blog.nexus-clinic.com"
WP_USER = "munnaza"
WP_PASS = "#Wb7471Z9W)Q0Cix#H#f!97r"

# Module-level CSS map populated once per run from the doc's <style> tag.
# Maps  class_name -> raw CSS property string  e.g. {"c20": "border-top-style:solid;..."}
_CSS_MAP: dict = {}

# Default alt text for images — set in main() to the post title before build_gutenberg()
_IMG_ALT: str = ''

# Labels that mark SEO meta paragraphs (Format B — inline prefix)
_SEO_META_LABELS = (
    'SEO META', 'Meta Title:', 'Meta Description:', 'Target URL:',
    'Primary Keyword:', 'Secondary Keywords:', 'Author:', 'Last Updated:',
    'Alt text:', 'Alt Text:', 'Image title:', 'Image Title:',
    'Image Description:', 'Image description:',
    'Focused keyphrase:', 'Focused Keyphrase:', 'Focus Keyphrase:',
    'Featured image:', 'Featured Image:',
    'Seo main title:', 'SEO main title:', 'SEO Main Title:',
)

# Labels that appear as stand-alone headings (Format C) followed by a value sibling
_SEO_HEADING_LABELS = (
    'Meta Title', 'Meta Description', 'Featured Image',
    'SEO Meta Title', 'SEO Meta Description',
)

# ── CSS helpers ───────────────────────────────────────────────────────────────
def _parse_css(soup):
    """Build a {class_name: props_string} map from the doc's <style> tag."""
    global _CSS_MAP
    _CSS_MAP = {}
    style_tag = soup.find('style')
    if not style_tag:
        return
    for m in re.finditer(r'\.(c\d+)\s*\{([^}]+)\}', style_tag.string or ''):
        _CSS_MAP[m.group(1)] = m.group(2)

def _has_prop(classes, prop, value=None):
    """Return True if any class in `classes` defines the given CSS property (optionally=value)."""
    for cls in (classes or []):
        props = _CSS_MAP.get(cls, '')
        if value:
            if re.search(rf'{re.escape(prop)}\s*:\s*{re.escape(value)}', props):
                return True
        else:
            if re.search(rf'{re.escape(prop)}\s*:', props):
                return True
    return False

# ── WebP conversion ───────────────────────────────────────────────────────────
def convert_to_webp(filepath):
    """Convert an image file to WebP format (in-place alongside original).
    Returns the WebP filepath; if Pillow is unavailable returns original path unchanged."""
    try:
        from PIL import Image
    except ImportError:
        print("  [warn] Pillow not installed — skipping WebP conversion  (pip install Pillow)")
        return filepath
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.webp':
        return filepath
    webp_path = os.path.splitext(filepath)[0] + '.webp'
    with Image.open(filepath) as img:
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')
        img.save(webp_path, 'WEBP', quality=85)
    print(f"      Converted to WebP: {os.path.basename(webp_path)}")
    return webp_path

# ── SEO field extractors ───────────────────────────────────────────────────────
# Docs use several different template formats for the SEO meta block.
# All extractors search every block-level element (h1–h4 + p) so they work
# regardless of whether the author used a heading or a paragraph for a label.
# When multiple fields are concatenated inside one element (a Google Docs quirk
# where adjacent bold lines collapse into one heading), a stop-at-next-label
# lookahead regex is used to isolate each field's value.

_STOP_PAT = (
    r'(?=Image\s+[Tt]itle:|Image\s+[Dd]escription:'
    r'|[Ff]ocused\s+[Kk]eyphrase:|[Ff]ocus\s+[Kk]eyphrase:'
    r'|Alt\s+[Tt]ext:|Primary\s+Keyword:|Secondary\s+Keywords:'
    r'|Meta\s+Title:|Meta\s+Description:|SEO\s+Main\s+Title:'
    r'|SEO\s+Meta\s+Title\b|SEO\s+Meta\s+Description\b'  # no-colon heading labels
    r'|Meta\s+Description\b'                              # no-colon (doc 11 format)
    r'|Target\s+URL:|Author:|Last\s+Updated:'
    r'|[Ff]\s*eature[ds]?\s+[Ii]mage|\Z)'  # catches "Featured", "Features", "F eatured" (split run)
)

# Strip trailing article section labels that sometimes get concatenated onto
# SEO field values when two headings collapse into one element in Google Docs.
# NOTE: no \b — labels are sometimes directly attached to the preceding word.
_TRAILING_SECTION = re.compile(
    r'(?:Introduction|Conclusion|Summary|Overview|Contents?)\s*$',
    re.IGNORECASE
)

# Any known SEO/image meta label prefix that might appear at the start of a
# Google Doc title or h1 (e.g. "Image Title:VALUE", "Seo main title:VALUE").
_LABEL_PREFIX = re.compile(
    r'^(?:Image\s+[Tt]itle|Alt\s+[Tt]ext|Image\s+[Dd]escription'
    r'|(?:SEO\s+)?[Mm]eta\s+[Tt]itle|SEO\s+[Mm]ain\s+[Tt]itle'
    r'|Focused\s+[Kk]eyphrase|Featured\s+[Ii]mage):\s*',
    re.IGNORECASE
)

def _scan_all(soup, label_regex):
    """
    Search every h1-h4 and p element in soup for label_regex followed by a value.
    The regex should NOT include a capture group — the function appends one.
    Returns the first match, stripped. Returns '' if nothing found.
    """
    full_pat = re.compile(
        label_regex + r'\s*\xa0?\s*(.+?)' + _STOP_PAT,
        re.IGNORECASE | re.DOTALL,
    )
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p']):
        text = tag.get_text(separator=' ', strip=True)
        m = full_pat.search(text)
        if m:
            # Collapse any internal newlines / nbsp
            return re.sub(r'[\n\r\xa0]+', ' ', m.group(1)).strip()
    return ''

def _heading_value(soup, heading_text):
    """Return the next sibling's text when a tag exactly matches heading_text.
    Applies the same stop-pattern as _scan_all so concatenated sibling fields
    don't bleed into the returned value."""
    _trunc = re.compile(r'(.+?)' + _STOP_PAT, re.IGNORECASE | re.DOTALL)
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p']):
        if tag.get_text(separator=' ', strip=True) == heading_text:
            nxt = tag.find_next_sibling()
            if nxt:
                raw = nxt.get_text(separator=' ', strip=True)
                m = _trunc.search(raw)
                val = re.sub(r'[\n\r\xa0]+', ' ', m.group(1)).strip() if m else raw
                return val
    return ''

def extract_seo_title(soup):
    """Return the SEO/meta title from any supported doc template format."""
    # 1. With colon: 'Meta Title: X', 'SEO Main Title: X'
    v = _scan_all(soup, r'(?:SEO\s+Main\s+Title|Meta\s+Title)\s*:')
    # 2. Without colon: 'Meta Title X' (space only, doc 23 format)
    if not v:
        v = _scan_all(soup, r'(?:SEO\s+Main\s+Title|Meta\s+Title)\s+')
    # 3. Heading-then-sibling formats: h2 "Meta Title" or h2 "SEO Meta Title"
    if not v:
        v = _heading_value(soup, 'Meta Title')
    if not v:
        v = _heading_value(soup, 'SEO Meta Title')
    # Strip trailing article section labels that sometimes bleed in
    return _TRAILING_SECTION.sub('', v).strip()

def extract_seo_description(soup):
    """Return the SEO meta description from any supported format."""
    # 1. Inline colon formats: 'Meta Description: X' or 'Meta Description X'
    for pat in (r'Meta\s+Description\s*:', r'Meta\s+Description'):
        v = _scan_all(soup, pat)
        if v:
            return _TRAILING_SECTION.sub('', v).strip()
    # 2. Heading-then-sibling formats: h2 "Meta Description" or h2 "SEO Meta Description"
    for heading in ('Meta Description', 'SEO Meta Description'):
        v = _heading_value(soup, heading)
        if v:
            return _TRAILING_SECTION.sub('', v).strip()
    # 3. Label embedded at END of another field's p (doc 11 format):
    #    e.g. p: "[title value] Meta Description"  →  value in next sibling p
    _label_at_end = re.compile(r'Meta\s+Description\s*$', re.IGNORECASE)
    _bad_start = re.compile(
        r'^(?:Image\s+[Tt]itle|Image\s+[Dd]escription|[Ff]ocused|[Ff]ocus'
        r'|Alt\s+[Tt]ext|Meta\s+Title|SEO)',
        re.IGNORECASE
    )
    _trunc3 = re.compile(r'(.+?)' + _STOP_PAT, re.IGNORECASE | re.DOTALL)
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p']):
        if _label_at_end.search(tag.get_text(separator=' ', strip=True)):
            nxt = tag.find_next_sibling()
            if nxt:
                raw = nxt.get_text(separator=' ', strip=True)
                if raw and not _bad_start.match(raw):
                    m3 = _trunc3.search(raw)
                    val = re.sub(r'[\n\r\xa0]+', ' ', m3.group(1)).strip() if m3 else raw
                    return _TRAILING_SECTION.sub('', val).strip()
    return ''

def extract_primary_keyword(soup):
    """Return the primary keyword / focused keyphrase."""
    return _scan_all(soup,
        r'(?:Primary\s+Keyword|[Ff]ocused\s+[Kk]eyphrase|[Ff]ocus\s+[Kk]eyphrase)\s*:')

def extract_image_alt(soup):
    """Return the image alt text specified in the doc."""
    return _scan_all(soup, r'Alt\s+[Tt]ext\s*:')

def extract_image_title(soup):
    """Return the image WP title specified in the doc."""
    return _scan_all(soup, r'Image\s+[Tt]itle\s*:')

def extract_image_description(soup):
    """Return the image WP description specified in the doc."""
    return _scan_all(soup, r'Image\s+[Dd]escription\s*:')

# ── WP media metadata updater ─────────────────────────────────────────────────
def update_media_metadata(session, site, wp_id, alt_text='', title='', description=''):
    """PATCH a WP media item's alt_text, title, and/or description via REST API."""
    if not wp_id:
        return
    body = {}
    if alt_text:    body['alt_text']    = alt_text
    if title:       body['title']       = title
    if description: body['description'] = description
    if not body:
        return
    r = session.post(f"{site}/wp-json/wp/v2/media/{wp_id}", data=json.dumps(body))
    if r.status_code in (200, 201):
        print(f"      Media [{wp_id}] metadata updated"
              + (f" alt={alt_text!r}" if alt_text else ''))
    else:
        print(f"  [warn] Media metadata update failed ({r.status_code}): {r.text[:80]}")

# ── Yoast SEO meta writer ──────────────────────────────────────────────────────
def save_yoast_meta(session, site, post_id, seo_title, seo_desc, focus_kw=''):
    """Save Yoast SEO title, description, and focus keyphrase.

    Tries approaches in order until one works:
      1a. REST API with _yoast_wpseo_* keys  (Yoast >= 7.x, underscored)
      1b. REST API with  yoast_wpseo_* keys  (some versions register without underscore)
      2.  admin-ajax wpseo_save_post_metadata (older Yoast)
      3.  wp-admin/post.php form POST         (fallback: full editpost submission)
    """
    if not post_id or (not seo_title and not seo_desc and not focus_kw):
        print("  [skip] Yoast meta: nothing to save")
        return False

    def _log_saved(via):
        print(f"      Yoast SEO meta saved via {via} [OK]")
        if seo_title: print(f"        SEO title : {seo_title[:80]}")
        if seo_desc:  print(f"        SEO desc  : {seo_desc[:80]}")
        if focus_kw:  print(f"        Focus KW  : {focus_kw[:80]}")

    # ── Approaches 1a & 1b: WP REST API meta ─────────────────────────────────
    for prefix in ('_yoast_wpseo', 'yoast_wpseo'):
        meta = {}
        if seo_title: meta[f'{prefix}_title']    = seo_title
        if seo_desc:  meta[f'{prefix}_metadesc']  = seo_desc
        if focus_kw:  meta[f'{prefix}_focuskw']   = focus_kw

        r = session.post(f"{site}/wp-json/wp/v2/posts/{post_id}",
                         data=json.dumps({'meta': meta}))
        if r.status_code not in (200, 201):
            continue
        # Confirm at least one key came back saved in the response meta.
        # If resp_meta doesn't include our keys at all, the keys aren't
        # registered for REST on this site — fall through to other methods.
        resp_meta = r.json().get('meta', {})
        if any(resp_meta.get(k) for k in meta):
            _log_saved('REST API')
            return True

    # ── Fetch the WP admin edit page (needed by approaches 2 & 3) ───────────
    r_edit = session.get(f"{site}/wp-admin/post.php",
                         params={'post': post_id, 'action': 'edit'},
                         headers={'Content-Type': 'text/html', 'X-WP-Nonce': ''})
    if r_edit.status_code != 200:
        print(f"  [warn] Yoast: could not load edit page ({r_edit.status_code})")
        return False
    edit_soup = BeautifulSoup(r_edit.text, 'html.parser')

    def _inp(name):
        el = (edit_soup.find('input', {'name': name}) or
              edit_soup.find('input', {'id':   name}))
        return el.get('value', '') if el else ''

    yoast_nonce = _inp('yoast_free_metabox_nonce')

    # ── Approach 2: admin-ajax wpseo_save_post_metadata ──────────────────────
    if yoast_nonce:
        payload = {
            'action':               'wpseo_save_post_metadata',
            'nonce':                yoast_nonce,
            'postID':               str(post_id),
            'yoast_wpseo_title':    seo_title,
            'yoast_wpseo_metadesc': seo_desc,
            'yoast_wpseo_focuskw':  focus_kw,
            'yoast_wpseo_opengraph-title':       seo_title,
            'yoast_wpseo_opengraph-description': seo_desc,
        }
        r2 = session.post(
            f"{site}/wp-admin/admin-ajax.php",
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded',
                     'X-WP-Nonce': ''},
        )
        if r2.status_code == 200 and r2.text.strip() not in ('0', '-1', ''):
            _log_saved('admin-ajax')
            return True
        print(f"  [info] admin-ajax returned {r2.status_code} {r2.text.strip()[:40]!r} — trying post.php")
    else:
        print("  [info] yoast_free_metabox_nonce not found — skipping admin-ajax, trying post.php")

    # ── Approach 3: wp-admin/post.php editpost form POST ─────────────────────
    # Get the WordPress _wpnonce and the current post content (to avoid wiping it).
    wpnonce = _inp('_wpnonce')
    if not wpnonce:
        # Try alternative nonce field names WordPress sometimes uses
        for nf in ('_wpnonce', 'wpnonce', '_ajax_nonce'):
            wpnonce = _inp(nf)
            if wpnonce:
                break
    if not wpnonce:
        print("  [warn] Yoast: _wpnonce not found on edit page — meta NOT saved")
        return False

    # Fetch current post content via REST so we don't wipe it in the form POST
    r_cur = session.get(f"{site}/wp-json/wp/v2/posts/{post_id}",
                        params={'context': 'edit'})
    current_content = ''
    current_title   = ''
    current_status  = 'publish'
    if r_cur.status_code == 200:
        cur = r_cur.json()
        current_content = cur.get('content', {}).get('raw', '')
        current_title   = cur.get('title',   {}).get('raw', '')
        current_status  = cur.get('status',  'publish')

    form_data = {
        'action':        'editpost',
        '_wpnonce':      wpnonce,
        'post_ID':       str(post_id),
        'post_type':     'post',
        'post_status':   current_status,
        'post_title':    current_title,
        'content':       current_content,
        # Yoast nonce + meta fields
        'yoast_free_metabox_nonce': yoast_nonce,
        'yoast_wpseo_title':         seo_title,
        'yoast_wpseo_metadesc':      seo_desc,
        'yoast_wpseo_focuskw':       focus_kw,
        'yoast_wpseo_opengraph-title':       seo_title,
        'yoast_wpseo_opengraph-description': seo_desc,
    }
    r3 = session.post(
        f"{site}/wp-admin/post.php",
        data=form_data,
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'X-WP-Nonce': ''},
        allow_redirects=True,
    )
    if r3.status_code in (200, 302):
        _log_saved('post.php form')
        return True

    print(f"  [warn] All Yoast save attempts failed — last status: {r3.status_code}")
    return False

# ── 1. Extract Google Doc ID ──────────────────────────────────────────────────
def get_doc_id(url):
    m = re.search(r'/d/([a-zA-Z0-9_-]{25,})', url)
    if not m:
        sys.exit(f"Cannot extract Doc ID from: {url}")
    return m.group(1)

# ── 2. Download & parse Google Doc ───────────────────────────────────────────
def fetch_doc(doc_id):
    import time
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=html"
    for attempt in range(1, 6):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                soup = BeautifulSoup(r.content, "html.parser")
                _parse_css(soup)
                return soup
            if r.status_code in (500, 503) and attempt < 5:
                print(f"  [retry {attempt}/4] Google export returned {r.status_code} — waiting 8s...")
                time.sleep(8)
                continue
            r.raise_for_status()
        except requests.exceptions.Timeout:
            if attempt < 5:
                print(f"  [retry {attempt}/4] Google export timed out — waiting 8s...")
                time.sleep(8)
            else:
                raise
    raise RuntimeError(f"fetch_doc: failed after 5 attempts for {doc_id}")

# ── 3a. Extract slug from "Target URL:" meta field ───────────────────────────
def extract_target_slug(soup):
    """
    Return the target post slug extracted from the doc's SEO meta block.

    Supports multiple formats:
    - 'Target URL: /blogs/my-slug/'  (paragraph or any tag, inline prefix)
    - Raw URL in a heading: 'https://www.nexus-clinic.com/blogs/my-slug/'

    Google Docs quirk: when a heading contains a hyperlink, get_text() concatenates
    the link-display text (e.g. "Meta") onto the URL, giving a corrupt path like
    '.../lip-filler-swelling-timeline/Meta'.  We always prefer the <a href> attribute
    (with Google-redirect unwrapped) over the heading text.

    Returns '' if not found.
    """
    # Slugs that are not real post slugs — filter them out
    _NON_SLUGS = {'meta', 'blogs', 'weight-loss', 'face', 'body', 'hair', 'skin'}

    def _slug_from_url(url):
        """Extract the last path segment from a nexus-clinic.com URL, or ''."""
        m = re.match(r'https?://(?:www\.)?nexus-clinic\.com(/[^\s?#]*)', url)
        if not m:
            return ''
        seg = m.group(1).rstrip('/').split('/')[-1]
        return seg if seg and seg.lower() not in _NON_SLUGS else ''

    def _href_from_tag(tag):
        """Return the best href found in a tag (Google-redirect unwrapped)."""
        a = tag.find('a', href=True)
        if not a:
            return ''
        href = a['href']
        redir = re.search(r'[?&]q=([^&]+)', href)
        if redir:
            href = unquote(redir.group(1))
        return href

    # Format: inline 'Target URL: VALUE' (any tag)
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p']):
        text = tag.get_text(strip=True)
        if not text.startswith('Target URL:'):
            continue
        path = re.sub(r'^Target URL:\s*\xa0?\s*', '', text).strip()
        slug = path.rstrip('/').split('/')[-1]
        if slug and slug.lower() not in _NON_SLUGS:
            return slug
        # Slug looks bad (e.g. 'Meta') — try the <a href> instead
        href = _href_from_tag(tag)
        if href:
            slug2 = _slug_from_url(href)
            if slug2:
                return slug2

    # Format: raw nexus-clinic.com URL in a heading
    # Always prefer the <a href> attribute over get_text() to avoid the
    # "display-text appended to URL" corruption.
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4']):
        # 1. Try the <a href> first (most reliable)
        href = _href_from_tag(tag)
        if href:
            slug = _slug_from_url(href)
            if slug:
                return slug
        # 2. Fallback: heading text (only if it starts with a nexus URL)
        text = tag.get_text(strip=True)
        if re.match(r'https?://(?:www\.)?nexus-clinic\.com/', text):
            slug = _slug_from_url(text)
            if slug:
                return slug

    return ''

# ── 3b. Extract title ─────────────────────────────────────────────────────────
def extract_title(soup):
    # Priority 1: HTML <title> tag (the Google Doc filename)
    title_tag = soup.find("title")
    if title_tag and title_tag.text.strip():
        t = re.sub(r'\s*-\s*Google Docs$', '', title_tag.text.strip()).strip()
        # Strip any known meta-label prefix (e.g. "Image Title:", "Seo main title:")
        t = _LABEL_PREFIX.sub('', t).strip()
        if t:
            return t
    # Priority 2: "Meta Title:" paragraph (Format B)
    for p in soup.find_all('p'):
        text = p.get_text(strip=True)
        if text.startswith('Meta Title:'):
            return re.sub(r'^Meta Title:\s*\xa0?\s*', '', text).strip()
    # Priority 3: "SEO Meta Title" label + next sibling (Format A)
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p']):
        if 'SEO Meta Title' in tag.get_text():
            nxt = tag.find_next_sibling()
            if nxt:
                return nxt.get_text(strip=True)
    # Priority 4: first real h1 — if it starts with a meta label, extract the value part
    for h1 in soup.find_all('h1'):
        text = h1.get_text(strip=True)
        label_m = _LABEL_PREFIX.match(text)
        if label_m:
            val = text[label_m.end():].strip()
            if val:
                return val
            continue
        if len(text) < 120 and not re.match(r'^(Learn|Compare|Discover|Find|Explore)', text):
            return text
    # Priority 5: fall back to SEO title (at least gives a meaningful title)
    seo_t = extract_seo_title(soup)
    if seo_t:
        return seo_t
    return "Untitled"

# ── 4a. Remove SEO meta blocks ────────────────────────────────────────────────
def remove_seo_meta(soup):
    """
    Strip SEO meta blocks in all three formats:
    Format A: <h3>SEO Meta Title</h3> + value sibling
    Format B: <p>Meta Title: value</p>  (inline prefix)
    Format C: <h2>Meta Title</h2> + <p>value</p>  (heading + sibling)
    Also strips image-metadata paragraphs (Alt text:, Image title:, etc.)
    """
    # Format A/C: stand-alone heading label + next sibling value
    for tag in list(soup.find_all(['h1', 'h2', 'h3', 'h4', 'p'])):
        text = tag.get_text(strip=True)
        if text in _SEO_HEADING_LABELS:
            nxt = tag.find_next_sibling()
            if nxt and nxt.name in ('p', 'h1', 'h2', 'h3', 'h4'):
                nxt.decompose()
            tag.decompose()

    # Format B: paragraphs whose text starts with a known meta label
    for tag in list(soup.find_all('p')):
        text = tag.get_text(strip=True)
        if text == 'SEO META':
            tag.decompose()
            continue
        if any(text.startswith(lbl) for lbl in _SEO_META_LABELS[1:]):
            tag.decompose()
            continue
        # Also catch regex-matched labels (focused keyphrase, alt text, SEO Main Title)
        if re.match(r'^(Alt\s+[Tt]ext|Image\s+[Tt]itle|Image\s+[Dd]escription'
                    r'|[Ff]ocused\s+[Kk]eyphrase|[Ff]ocus\s+[Kk]eyphrase'
                    r'|SEO\s+Main\s+Title|Seo\s+[Mm]ain\s+[Tt]itle'
                    r'|Featured\s+[Ii]mage)', text, re.IGNORECASE):
            tag.decompose()

    # Format C (raw URL heading): headings whose text IS a nexus-clinic.com URL
    # (the Target URL field written as a bare heading with no 'Target URL:' prefix)
    for tag in list(soup.find_all(['h1', 'h2', 'h3', 'h4'])):
        text = tag.get_text(strip=True)
        if re.match(r'https?://(?:www\.)?nexus-clinic\.com/', text):
            tag.decompose()

    return soup

# ── 4b. Convert pipe-separated paragraphs → HTML table ───────────────────────
def convert_pipe_tables(soup):
    """
    Google Docs sometimes contains comparison tables written as plain paragraphs
    with pipe separators, e.g.:
        Feature | Mounjaro | Ozempic
        Drug class | Dual GIP + GLP-1 | GLP-1 only
        ...
    This function detects runs of 2+ consecutive such paragraphs with the same
    column count and converts them in-place to a proper <table> element, which
    the existing elem_to_block() table handler then renders as a Gutenberg table.
    """
    body = soup.find('body') or soup

    while True:
        children = [c for c in body.children if isinstance(c, Tag)]
        changed  = False

        for i, el in enumerate(children):
            if el.name != 'p' or '|' not in el.get_text():
                continue

            # Collect a contiguous run of pipe-separated <p> elements
            run = []
            for j in range(i, len(children)):
                c = children[j]
                if c.name != 'p':
                    break
                t = c.get_text(strip=True)
                if '|' not in t:
                    break
                cols = [col.strip() for col in t.split('|')]
                if len(cols) < 2:
                    break
                run.append((c, cols))

            if len(run) < 2:
                continue

            # All rows must have the same column count
            ncols = len(run[0][1])
            if not all(len(r[1]) == ncols for r in run):
                continue

            # Build a <table> and replace the paragraph run
            table = soup.new_tag('table')
            for row_idx, (p_el, cols) in enumerate(run):
                tr = soup.new_tag('tr')
                for col_text in cols:
                    ctag = 'th' if row_idx == 0 else 'td'
                    td   = soup.new_tag(ctag)
                    td.string = col_text
                    tr.append(td)
                table.append(tr)

            run[0][0].replace_with(table)
            for p_el, _ in run[1:]:
                p_el.decompose()

            changed = True
            break   # restart — children list has changed

        if not changed:
            break

    return soup

# ── 4c. Fix Google redirect URLs & spacer links ───────────────────────────────
def fix_links(soup):
    for a in soup.find_all('a', href=True):
        # Unwrap Google redirect
        m = re.search(r'[?&]q=([^&]+)', a['href'])
        if m:
            a['href'] = unquote(m.group(1))
        # Replace empty spacer links with a plain space
        link_text = a.get_text().replace('\xa0', '').strip()
        if not link_text:
            a.replace_with(' ')
    return soup

# ── 5. Upload base64 data-URI image to WP media library ──────────────────────
def upload_image(session, site, src, index):
    """Upload a data-URI image to WP and return (url, id)."""
    m = re.match(r'data:(image/\w+);base64,(.+)', src, re.DOTALL)
    if not m:
        return None, None
    mime  = m.group(1)
    data  = base64.b64decode(m.group(2))
    ext   = mime.split('/')[1]
    fname = f"blog-image-{index}.{ext}"
    upload_headers = {
        'X-WP-Nonce':          session.headers.get('X-WP-Nonce'),
        'Content-Type':        mime,
        'Content-Disposition': f'attachment; filename="{fname}"',
    }
    r = session.post(f"{site}/wp-json/wp/v2/media", data=data, headers=upload_headers)
    if r.status_code in (200, 201):
        media = r.json()
        return media.get('source_url'), media.get('id')
    print(f"  [warn] image upload failed ({r.status_code}): {r.text[:120]}")
    return None, None

# ── 5b. Upload a local image file to WP media library ─────────────────────────
def upload_local_image(session, site, filepath, index,
                       alt_text='', img_title='', img_desc=''):
    """Upload a local image file to WP (converting to WebP first) and return (url, id).

    After upload, sets alt_text, title, and description on the WP media item.
    """
    # Convert to WebP before uploading
    filepath = convert_to_webp(filepath)

    fname = os.path.basename(filepath)
    ext   = os.path.splitext(fname)[1].lower().lstrip('.')
    mime_map = {
        'webp': 'image/webp', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png':  'image/png',  'gif': 'image/gif',
    }
    mime = mime_map.get(ext, f'image/{ext}')
    clean_name = re.sub(r'\s+', '-', fname.lower())
    with open(filepath, 'rb') as f:
        data = f.read()
    upload_headers = {
        'X-WP-Nonce':          session.headers.get('X-WP-Nonce'),
        'Content-Type':        mime,
        'Content-Disposition': f'attachment; filename="{clean_name}"',
    }
    r = session.post(f"{site}/wp-json/wp/v2/media", data=data, headers=upload_headers)
    if r.status_code in (200, 201):
        media  = r.json()
        wp_url = media.get('source_url')
        wp_id  = media.get('id')
        print(f"      Uploaded [{index}]: {clean_name}")
        if wp_id and (alt_text or img_title or img_desc):
            update_media_metadata(session, site, wp_id, alt_text, img_title, img_desc)
        return wp_url, wp_id
    print(f"  [warn] local upload failed ({r.status_code}): {r.text[:120]}")
    return None, None

# ── 6. Inline HTML: preserve links, bold, italic; handle [LINK:] annotations ──
def inner_html(el, session=None, site=None, img_counter=None):
    """
    Recursively render an element's children as inline HTML.
    Preserves <a>, <strong>, <em>.
    Converts [LINK: /path/] annotations to real <a> tags.
    NOTE: does NOT strip the result — callers must .strip() before empty checks
    to avoid losing inter-span boundary spaces.
    """
    parts = []
    for child in el.children:
        if isinstance(child, NavigableString):
            text = str(child)
            # Handle [LINK: /path/] annotations from doc templates
            link_match = re.search(r'\s*\xa0?\s*\[LINK:\s*([^\]]+)\]', text)
            if link_match:
                link_path = link_match.group(1).strip()
                link_text = text[:link_match.start()].strip()
                if link_text:
                    url = f"https://www.nexus-clinic.com{link_path}"
                    parts.append(f'<a href="{url}">{link_text}</a>')
                # Drop the annotation itself
            else:
                parts.append(text)
        elif child.name == 'a':
            href = child.get('href', '')
            text = inner_html(child, session, site, img_counter)
            if text.strip():
                parts.append(f'<a href="{href}">{text}</a>')
        elif child.name in ('strong', 'b'):
            text = inner_html(child, session, site, img_counter)
            parts.append(f'<strong>{text}</strong>')
        elif child.name in ('em', 'i'):
            text = inner_html(child, session, site, img_counter)
            parts.append(f'<em>{text}</em>')
        else:
            # span or other inline element — recurse; honour CSS italic/bold
            content = inner_html(child, session, site, img_counter)
            if isinstance(child, Tag):
                span_classes = child.get('class', [])
                is_italic = _has_prop(span_classes, 'font-style', 'italic')
                is_bold   = _has_prop(span_classes, 'font-weight', '700')
                if is_italic and content.strip():
                    content = f'<em>{content}</em>'
                if is_bold and content.strip():
                    content = f'<strong>{content}</strong>'
            parts.append(content)
    return ''.join(parts)

# ── 7. Convert one element to a Gutenberg block ───────────────────────────────
def elem_to_block(el, session, site, img_counter):
    if not isinstance(el, Tag):
        return None

    tag  = el.name
    text = el.get_text(strip=True)

    # Skip SEO meta label elements (Format A / C headings)
    if text in ('SEO Meta Title', 'SEO Meta Description', 'SEO META'):
        return None
    if text in _SEO_HEADING_LABELS:
        return None
    # Skip SEO meta paragraphs (Format B inline labels)
    if any(text.startswith(lbl) for lbl in _SEO_META_LABELS[1:]):
        return None
    # Skip image metadata inline labels and SEO Main Title (all case variants)
    if re.match(r'^(Alt\s+[Tt]ext|Image\s+[Tt]itle|Image\s+[Dd]escription'
                r'|[Ff]ocused\s+[Kk]eyphrase|[Ff]ocus\s+[Kk]eyphrase'
                r'|SEO\s+Main\s+Title|Seo\s+[Mm]ain\s+[Tt]itle'
                r'|Featured\s+[Ii]mage)', text, re.IGNORECASE):
        return None
    # Skip raw nexus-clinic.com URL labels (target URL in heading format)
    if re.match(r'https?://(?:www\.)?nexus-clinic\.com/', text):
        return None
    # Skip editorial/placeholder notes that should never be published
    if re.match(r'^Note for editor\b', text, re.IGNORECASE):
        return None
    # Skip SEO description h1s (long meta descriptions)
    if tag == 'h1':
        if len(text) > 120 and re.match(r'^(Learn|Compare|Discover|Find|Explore|Understand)', text):
            return None

    # ── Image (standalone <img>) ───────────────────────────────────────────────
    if tag == 'img':
        src = el.get('src', '')
        if src.startswith('data:image'):
            img_counter[0] += 1
            wp_url, wp_id = upload_image(session, site, src, img_counter[0])
            if wp_url:
                alt_esc = _html.escape(_IMG_ALT, quote=True)
                id_attr = f'"id":{wp_id},' if wp_id else ''
                return (f'<!-- wp:image {{{id_attr}"sizeSlug":"large","linkDestination":"none"}} -->\n'
                        f'<figure class="wp-block-image size-large">'
                        f'<img src="{wp_url}" alt="{alt_esc}"/></figure>\n<!-- /wp:image -->')
        return None

    # ── Paragraph ─────────────────────────────────────────────────────────────
    if tag == 'p':
        # Image-only paragraph
        imgs = el.find_all('img')
        if imgs and not text:
            blocks = []
            for img in imgs:
                src = img.get('src', '')
                if src.startswith('data:image'):
                    img_counter[0] += 1
                    wp_url, wp_id = upload_image(session, site, src, img_counter[0])
                    if wp_url:
                        alt_esc = _html.escape(_IMG_ALT, quote=True)
                        id_attr = f'"id":{wp_id},' if wp_id else ''
                        blocks.append(
                            f'<!-- wp:image {{{id_attr}"sizeSlug":"large","linkDestination":"none"}} -->\n'
                            f'<figure class="wp-block-image size-large">'
                            f'<img src="{wp_url}" alt="{alt_esc}"/></figure>\n<!-- /wp:image -->'
                        )
            return '\n\n'.join(blocks) if blocks else None

        inner = inner_html(el, session, site, img_counter).strip()
        if not inner:
            return None

        # If the paragraph has a top border in the doc (e.g. editorial footer note),
        # emit a Gutenberg separator before it so the line is preserved in the post.
        p_classes = el.get('class', [])
        prefix = ''
        if _has_prop(p_classes, 'border-top-style', 'solid'):
            prefix = ('<!-- wp:separator {"className":"is-style-wide"} -->\n'
                      '<hr class="wp-block-separator has-alpha-channel-opacity is-style-wide"/>\n'
                      '<!-- /wp:separator -->\n\n')

        return f'{prefix}<!-- wp:paragraph -->\n<p>{inner}</p>\n<!-- /wp:paragraph -->'

    # ── Headings ──────────────────────────────────────────────────────────────
    elif tag == 'h1':
        # WordPress already renders the post title as the page H1 via the theme.
        # Including an H1 block in the content creates a duplicate heading.
        # Skip all H1 elements — content structure starts at H2.
        return None

    elif tag == 'h2':
        inner = inner_html(el, session, site, img_counter).strip()
        if not inner: return None
        return (f'<!-- wp:heading -->\n'
                f'<h2 class="wp-block-heading"><strong>{inner}</strong></h2>\n<!-- /wp:heading -->')

    elif tag == 'h3':
        inner = inner_html(el, session, site, img_counter).strip()
        if not inner: return None
        return (f'<!-- wp:heading {{"level":3}} -->\n'
                f'<h3 class="wp-block-heading">{inner}</h3>\n<!-- /wp:heading -->')

    elif tag == 'h4':
        inner = inner_html(el, session, site, img_counter).strip()
        if not inner: return None
        return (f'<!-- wp:heading {{"level":4}} -->\n'
                f'<h4 class="wp-block-heading">{inner}</h4>\n<!-- /wp:heading -->')

    # ── Lists ─────────────────────────────────────────────────────────────────
    elif tag in ('ul', 'ol'):
        items = [f'<li>{inner_html(li, session, site, img_counter).strip()}</li>'
                 for li in el.find_all('li', recursive=False)]
        if not items: return None
        lt      = 'ul' if tag == 'ul' else 'ol'
        ordered = ' {"ordered":true}' if tag == 'ol' else ''
        return f'<!-- wp:list{ordered} -->\n<{lt}>{"".join(items)}</{lt}>\n<!-- /wp:list -->'

    # ── Table ─────────────────────────────────────────────────────────────────
    elif tag == 'table':
        rows = el.find_all('tr')
        if not rows: return None
        html_rows = []
        for i, row in enumerate(rows):
            cells      = row.find_all(['td', 'th'])
            ctag       = 'th' if i == 0 else 'td'
            cells_html = ''.join(f'<{ctag}>{c.get_text(strip=True)}</{ctag}>' for c in cells)
            html_rows.append(f'<tr>{cells_html}</tr>')
        thead = f'<thead>{html_rows[0]}</thead>'
        tbody = f'<tbody>{"".join(html_rows[1:])}</tbody>' if len(html_rows) > 1 else ''
        return (f'<!-- wp:table -->\n'
                f'<figure class="wp-block-table"><table>{thead}{tbody}</table></figure>\n'
                f'<!-- /wp:table -->')

    return None

# ── 8. Build full Gutenberg content ───────────────────────────────────────────
def build_gutenberg(soup, session, site, header_img_block=None, local_images=None):
    """
    Convert soup body to Gutenberg blocks.
    header_img_block : pre-built wp:image block to prepend (uploaded before SEO strip)
    local_images     : list of pre-built wp:image block strings; one is inserted
                       after each H2 heading block (skips H1).
    """
    body        = soup.find('body') or soup
    img_counter = [0]
    blocks      = []
    img_idx     = 0

    if header_img_block:
        blocks.append(header_img_block)

    for el in body.children:
        block = elem_to_block(el, session, site, img_counter)
        if not block:
            continue
        blocks.append(block)
        # After each H2 (not H1/H3/H4), insert the next local image
        if (local_images
                and img_idx < len(local_images)
                and '<!-- wp:heading -->' in block
                and '"level"' not in block):          # H2 has no "level" attr
            blocks.append(local_images[img_idx])
            img_idx += 1

    print(f"      Doc images uploaded : {img_counter[0]}")
    if local_images:
        print(f"      Local images inserted: {img_idx} of {len(local_images)}")
    return '\n\n'.join(blocks)

# ── 9. Apply keyword → URL link map to rendered HTML ─────────────────────────
def apply_keyword_links(html, link_map):
    """
    Wrap keyword phrases in <a> tags in the rendered HTML.
    - Processes longest phrases first (avoids partial-match conflicts).
    - Only replaces the first occurrence of each phrase (good SEO practice).
    - Skips text already inside existing <a>...</a> tags (no nested links).

    Strategy: temporarily replace all <a>...</a> spans with placeholders so
    the substitution regex can't see their content, then restore afterwards.
    """
    if not link_map:
        return html

    # 1. Save every existing link and replace with a placeholder
    saved_links = {}
    def _save(m):
        key = f'\x00LINK{len(saved_links)}\x00'
        saved_links[key] = m.group(0)
        return key
    safe_html = re.sub(r'<a\b[^>]*>.*?</a>', _save, html, flags=re.DOTALL | re.IGNORECASE)

    # 2. Apply keyword replacements on the placeholder-safe HTML
    for phrase, url in sorted(link_map.items(), key=lambda x: -len(x[0])):
        esc     = re.escape(phrase)
        pattern = re.compile(
            r'(?<=[>])([^<]*)(' + esc + r')([^<]*)(?=[<])',
            re.IGNORECASE
        )
        replaced = [False]
        def _rep(m, _url=url, _replaced=replaced):
            if _replaced[0]:
                return m.group(0)          # first occurrence only
            _replaced[0] = True
            return f'{m.group(1)}<a href="{_url}">{m.group(2)}</a>{m.group(3)}'
        safe_html = pattern.sub(_rep, safe_html)

    # 3. Restore original links
    for key, original in saved_links.items():
        safe_html = safe_html.replace(key, original)

    return safe_html

# ── 10. Slug generator ────────────────────────────────────────────────────────
def make_slug(title):
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug.strip())
    return re.sub(r'-+', '-', slug)

# ── 11. WordPress auth (cookie + nonce) ───────────────────────────────────────
def wp_connect(site, user, password):
    s = requests.Session()
    s.post(f"{site}/wp-login.php", data={
        'log': user, 'pwd': password,
        'wp_submit': 'Log In', 'testcookie': '1'
    }, allow_redirects=True)
    nonce = s.get(f"{site}/wp-admin/admin-ajax.php?action=rest-nonce").text.strip()
    s.headers.update({'X-WP-Nonce': nonce, 'Content-Type': 'application/json'})
    return s

# ── 12. Find post by slug ─────────────────────────────────────────────────────
def find_post(session, site, slug):
    """Return the WP post ID matching slug, or None.
    Searches published + all non-published statuses so that previously-drafted
    posts are still found on subsequent runs."""
    if not slug:
        return None
    for status in ('publish', 'draft,publish,future,pending,private', 'any'):
        r = session.get(f"{site}/wp-json/wp/v2/posts",
                        params={'slug': slug, 'context': 'edit', 'status': status})
        if r.status_code != 200:
            continue
        posts = r.json()
        if isinstance(posts, list) and posts:
            return posts[0]['id']
    return None

# ── 13a. Get post's current featured image (returns url, id or None, None) ────
def get_featured_image(session, site, post_id):
    if not post_id:
        return None, None
    r = session.get(f"{site}/wp-json/wp/v2/posts/{post_id}",
                    params={'context': 'edit'})
    p = r.json()
    fm_id = p.get('featured_media', 0)
    if not fm_id:
        return None, None
    rm = session.get(f"{site}/wp-json/wp/v2/media/{fm_id}")
    media = rm.json()
    return media.get('source_url'), fm_id

# ── 13b. Create or update post ────────────────────────────────────────────────
def publish_post(session, site, post_id, title, slug, content, status,
                 featured_media=None):
    body = {'title': title, 'slug': slug, 'content': content, 'status': status}
    if featured_media is not None:          # 0 = clear, >0 = set
        body['featured_media'] = featured_media
    payload = json.dumps(body)
    uri = (f"{site}/wp-json/wp/v2/posts/{post_id}" if post_id
           else f"{site}/wp-json/wp/v2/posts")
    r = session.post(uri, data=payload)
    r.raise_for_status()
    return r.json()

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Post a Google Doc to WordPress as Gutenberg blocks.')
    parser.add_argument('doc_url',          help='Google Docs URL')
    parser.add_argument('--slug',  default='', help='Existing post slug to update')
    parser.add_argument('--id',    default='', help='Existing post ID to update')
    parser.add_argument('--draft', action='store_true', help='Save as draft')
    parser.add_argument('--header-image', default='',
                        help='Single local image file to place at the top of the post')
    parser.add_argument('--header-media-id', default='',
                        help='Existing WP media ID to use as the header image (no re-upload)')
    parser.add_argument('--use-featured-as-header', action='store_true',
                        help='Move the existing WP Featured Image into the post content as the '
                             'header image block, then clear the featured_media field')
    parser.add_argument('--images', default='',
                        help='Folder of local images to distribute across H2 sections')
    parser.add_argument('--title', default='',
                        help='Override the post H1 title (supports transactional keywords)')
    parser.add_argument('--links',  default='',
                        help='JSON file: {"phrase": "https://url"} for inline linking')
    args   = parser.parse_args()
    status = 'draft' if args.draft else 'publish'

    # ── Step 1: Fetch doc ─────────────────────────────────────────────────────
    print("[1/4] Downloading Google Doc...")
    soup = fetch_doc(get_doc_id(args.doc_url))

    # Post title: --title flag overrides the doc-extracted title
    # Must be defined FIRST — other extractions fall back to it
    title = args.title or extract_title(soup)
    fix_links(soup)

    # Extract SEO fields BEFORE remove_seo_meta() strips them
    seo_title = extract_seo_title(soup)
    seo_desc  = extract_seo_description(soup)
    focus_kw  = extract_primary_keyword(soup)
    # Image-specific metadata (alt text, WP title, WP description)
    # Falls back to post title / seo_desc if not explicitly set in doc
    img_alt  = extract_image_alt(soup)   or title
    img_ttl  = extract_image_title(soup) or title
    img_dsc  = extract_image_description(soup) or seo_desc

    # Slug priority: --slug arg > Target URL meta > generated from title
    slug  = args.slug or extract_target_slug(soup) or make_slug(title)
    print(f"      Title : {title!r}")
    print(f"      Slug  : {slug!r}")
    if seo_title: print(f"      SEO title : {seo_title!r}")
    if seo_desc:  print(f"      SEO desc  : {seo_desc[:60]!r}...")
    if focus_kw:  print(f"      Focus KW  : {focus_kw!r}")
    if extract_image_alt(soup) or extract_image_title(soup):
        print(f"      Img alt   : {img_alt!r}")
        print(f"      Img title : {img_ttl!r}")

    # ── Step 2: Connect to WordPress ─────────────────────────────────────────
    print("[2/4] Connecting to WordPress...")
    session = wp_connect(WP_SITE, WP_USER, WP_PASS)

    # Set global alt text for all images in this post (used by elem_to_block)
    global _IMG_ALT
    _IMG_ALT = img_alt

    # Upload the doc's embedded image as the featured image ONLY.
    # It must NOT also appear as a content block — the theme renders featured
    # images above the post; adding it to blocks too creates a visible duplicate.
    header_img_block  = None
    header_img_wp_id  = None   # WP media ID of the featured image
    first_img = (soup.find('body') or soup).find('img')
    if first_img and first_img.get('src', '').startswith('data:image'):
        wp_url, wp_id = upload_image(session, WP_SITE, first_img['src'], 1)
        if wp_url:
            # Set as featured image — do NOT add to content blocks
            header_img_wp_id = wp_id
            fname = wp_url.split("/")[-1]
            print(f"      Featured image uploaded: {fname}")
            if wp_id:
                update_media_metadata(session, WP_SITE, wp_id,
                                      alt_text=img_alt, title=img_ttl, description=img_dsc)
        # Remove from soup so it doesn't appear in the body content blocks either
        (first_img.find_parent('span') or first_img).decompose()

    remove_seo_meta(soup)
    convert_pipe_tables(soup)      # pipe-separated rows → real <table> elements

    # Use an existing WP media item by ID as the header image (--header-media-id)
    if args.header_media_id and not header_img_block:
        rm = session.get(f"{WP_SITE}/wp-json/wp/v2/media/{args.header_media_id}")
        if rm.status_code == 200:
            media  = rm.json()
            fm_url = media.get('source_url')
            fm_id  = media.get('id')
            if fm_url:
                alt_esc = _html.escape(title, quote=True)
                id_attr = f'"id":{fm_id},' if fm_id else ''
                header_img_block = (
                    f'<!-- wp:image {{{id_attr}"sizeSlug":"large","linkDestination":"none"}} -->\n'
                    f'<figure class="wp-block-image size-large">'
                    f'<img src="{fm_url}" alt="{alt_esc}"/></figure>\n<!-- /wp:image -->'
                )
                header_img_wp_id = fm_id
                print(f"      Using existing media [{fm_id}]: {fm_url.split('/')[-1]}")
                # Update metadata on existing media (alt text, title)
                update_media_metadata(session, WP_SITE, fm_id,
                                      alt_text=img_alt, title=img_ttl, description=img_dsc)
        else:
            print(f"  [warn] media ID {args.header_media_id} not found ({rm.status_code})")

    # Move existing Featured Image → content header block (--use-featured-as-header)
    # The image stays as featured_media (we no longer clear it).
    if args.use_featured_as_header:
        post_id_for_lookup = args.id or find_post(session, WP_SITE, slug)
        fm_url, fm_id = get_featured_image(session, WP_SITE, post_id_for_lookup)
        if fm_url:
            alt_esc = _html.escape(title, quote=True)
            id_attr = f'"id":{fm_id},' if fm_id else ''
            header_img_block = (
                f'<!-- wp:image {{{id_attr}"sizeSlug":"large","linkDestination":"none"}} -->\n'
                f'<figure class="wp-block-image size-large">'
                f'<img src="{fm_url}" alt="{alt_esc}"/></figure>\n<!-- /wp:image -->'
            )
            header_img_wp_id = fm_id
            print(f"      Featured image also inserted in content: {fm_url.split('/')[-1]}")
            # Update metadata on the existing media item
            update_media_metadata(session, WP_SITE, fm_id,
                                  alt_text=img_alt, title=img_ttl, description=img_dsc)
        else:
            print("  [warn] --use-featured-as-header: no featured image found on this post")

    # Upload a single local header image if provided (--header-image)
    if args.header_image and os.path.isfile(args.header_image) and not header_img_block:
        wp_url, wp_id = upload_local_image(
            session, WP_SITE, args.header_image, 1,
            alt_text=img_alt, img_title=img_ttl, img_desc=img_dsc,
        )
        if wp_url:
            alt_esc = _html.escape(title, quote=True)
            id_attr = f'"id":{wp_id},' if wp_id else ''
            header_img_block = (
                f'<!-- wp:image {{{id_attr}"sizeSlug":"large","linkDestination":"none"}} -->\n'
                f'<figure class="wp-block-image size-large">'
                f'<img src="{wp_url}" alt="{alt_esc}"/></figure>\n<!-- /wp:image -->'
            )
            header_img_wp_id = wp_id

    # Upload local images if a folder was provided
    local_images = []
    if args.images and os.path.isdir(args.images):
        exts = {'.webp', '.jpg', '.jpeg', '.png', '.gif'}
        img_files = sorted([
            os.path.join(args.images, f)
            for f in os.listdir(args.images)
            if os.path.splitext(f)[1].lower() in exts
        ])
        if img_files:
            print(f"      Uploading {len(img_files)} local images from {args.images!r}...")
            alt_esc = _html.escape(title, quote=True)
            for i, fpath in enumerate(img_files, 1):
                wp_url, wp_id = upload_local_image(
                    session, WP_SITE, fpath, i,
                    alt_text=img_alt, img_title=img_ttl, img_desc=img_dsc,
                )
                if wp_url:
                    id_attr = f'"id":{wp_id},' if wp_id else ''
                    local_images.append(
                        f'<!-- wp:image {{{id_attr}"sizeSlug":"large","linkDestination":"none"}} -->\n'
                        f'<figure class="wp-block-image size-large">'
                        f'<img src="{wp_url}" alt="{alt_esc}"/></figure>\n<!-- /wp:image -->'
                    )
        else:
            print(f"  [warn] No image files found in {args.images!r}")

    # Load keyword-to-URL link map
    link_map = {}
    if args.links and os.path.isfile(args.links):
        with open(args.links, encoding='utf-8') as f:
            link_map = json.load(f)
        print(f"      Loaded {len(link_map)} keyword link(s)")

    # ── Step 3: Convert to Gutenberg ─────────────────────────────────────────
    print("[3/4] Converting to Gutenberg blocks...")
    content = build_gutenberg(
        soup, session, WP_SITE,
        header_img_block=header_img_block,
        local_images=local_images or None
    )
    if link_map:
        content = apply_keyword_links(content, link_map)

    blocks = content.count('<!-- wp:')
    print(f"      {blocks} blocks generated")

    # ── Step 4: Publish ───────────────────────────────────────────────────────
    post_id = args.id or find_post(session, WP_SITE, slug)
    action  = f"Updating post {post_id}" if post_id else "Creating new post"
    print(f"[4/4] {action}...")

    # Set featured_media to the header image ID (if we have one), else leave untouched
    featured_media_to_set = header_img_wp_id if header_img_wp_id else None

    result = publish_post(session, WP_SITE, post_id, title, slug, content, status,
                          featured_media=featured_media_to_set)

    final_id = result['id']
    print(f"\n  Done!")
    print(f"  ID     : {final_id}")
    print(f"  Title  : {result['title']['rendered']}")
    print(f"  Slug   : {result['slug']}")
    print(f"  URL    : {result['link']}")
    print(f"  Status : {result['status']}")

    # ── Step 5: Save Yoast SEO meta ───────────────────────────────────────────
    if seo_title or seo_desc or focus_kw:
        print("[5/5] Saving Yoast SEO meta...")
        save_yoast_meta(session, WP_SITE, final_id, seo_title, seo_desc, focus_kw)
    print()


if __name__ == '__main__':
    main()
