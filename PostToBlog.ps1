# PostToBlog.ps1 — Google Docs → WordPress in one command
# Usage:
#   .\PostToBlog.ps1 -DocUrl "https://docs.google.com/document/d/..."
#   .\PostToBlog.ps1 -DocUrl "..." -PostSlug "existing-slug"   # update existing post
#   .\PostToBlog.ps1 -DocUrl "..." -Status draft               # save as draft

param(
    [Parameter(Mandatory=$true)][string]$DocUrl,
    [string]$PostSlug  = "",
    [string]$PostId    = "",
    [string]$Status    = "publish",
    [string]$WpSite    = "https://blog.nexus-clinic.com",
    [string]$WpUser    = "munnaza",
    [string]$WpPass    = '#Wb7471Z9W)Q0Cix#H#f!97r'
)

$ErrorActionPreference = "Stop"

# ── Extract Google Doc ID from any share/edit URL ─────────────────────────────
function Get-DocId([string]$url) {
    if ($url -match '/d/([a-zA-Z0-9_-]{25,})') { return $Matches[1] }
    throw "Cannot extract Doc ID from: $url"
}

# ── Download + clean Google Doc HTML ─────────────────────────────────────────
function Get-DocHtml([string]$docId) {
    $raw = (Invoke-WebRequest -Uri "https://docs.google.com/document/d/$docId/export?format=html" -UseBasicParsing).Content

    # Extract body only
    $html = [regex]::Match($raw, '(?s)<body[^>]*>(.*?)</body>').Groups[1].Value

    # Drop scripts
    $html = [regex]::Replace($html, '(?s)<script[^>]*>.*?</script>', '')

    # Some docs put the article title in h1 AFTER meta blocks; others only have it in the
    # SEO Meta Title paragraph. We capture the SEO title first, then strip the whole meta block.
    $seoTitleMatch = [regex]::Match($html, '(?si)SEO Meta Title</h3>\s*<p[^>]*>(.*?)</p>')
    $script:seoTitle = if ($seoTitleMatch.Success) {
        ($seoTitleMatch.Groups[1].Value -replace '<[^>]+>','').Trim()
    } else { '' }

    # Nuke the entire SEO preamble: everything before the first h2 (the real article starts at Introduction)
    # but keep any h1 that appears after a gap from the meta block.
    $html = [regex]::Replace($html, '(?si)^.*?(?=<h1|<h2)', '')
    # If the first element is still a meta-description h1 (i.e. the h1 right after SEO meta desc),
    # drop it and fall through to h2 content.
    $firstH1 = [regex]::Match($html, '(?si)^<h1[^>]*>(.*?)</h1>')
    if ($firstH1.Success) {
        $inner = ($firstH1.Groups[1].Value -replace '<[^>]+>','').Trim()
        # If the h1 text looks like a meta description (starts with a verb like "Learn","Compare","Discover")
        # or is longer than 120 chars, it's the SEO description — skip it
        if ($inner.Length -gt 120 -or $inner -match '^(Learn|Compare|Discover|Find|Explore|Understand)') {
            $html = $html.Substring($firstH1.Index + $firstH1.Length)
        }
    }

    # Strip Google Docs noise: style/class/id attrs, spans
    $html = [regex]::Replace($html, '\s+(style|class|id)="[^"]*"', '')
    $html = [regex]::Replace($html, '</?span[^>]*>', '')

    # Fix Google redirect links → real URLs
    $html = [regex]::Replace($html, 'href="https://www\.google\.com/url\?q=([^&"]+)[^"]*"', {
        param($m) 'href="' + [Uri]::UnescapeDataString($m.Groups[1].Value) + '"'
    })

    # Remove empty spacer <a> Google adds before real links
    $html = [regex]::Replace($html, '<a href="[^"]+"> </a>', '')

    # Normalize
    $html = ($html -replace '&nbsp;', ' ') -replace '\s{2,}', ' '

    return $html
}

# ── Convert cleaned HTML → WordPress Gutenberg blocks ────────────────────────
function Convert-ToGutenberg([string]$html) {
    $blocks = [System.Collections.Generic.List[string]]::new()
    $pattern = '(?s)(<h[1-6][^>]*>.*?</h[1-6]>|<p[^>]*>.*?</p>|<ul[^>]*>.*?</ul>|<ol[^>]*>.*?</ol>|<table[^>]*>.*?</table>)'

    foreach ($m in [regex]::Matches($html, $pattern)) {
        $t = $m.Value.Trim()

        if ($t -match '^<h1') {
            $inner = [regex]::Replace($t, '(?s)^<h1[^>]*>(.*)</h1>$', '$1').Trim()
            $blocks.Add("<!-- wp:heading {`"level`":1} -->`n<h1 class=`"wp-block-heading`">$inner</h1>`n<!-- /wp:heading -->")

        } elseif ($t -match '^<h2') {
            $inner = [regex]::Replace($t, '(?s)^<h2[^>]*>(.*)</h2>$', '$1').Trim()
            $blocks.Add("<!-- wp:heading -->`n<h2 class=`"wp-block-heading`"><strong>$inner</strong></h2>`n<!-- /wp:heading -->")

        } elseif ($t -match '^<h3') {
            $inner = [regex]::Replace($t, '(?s)^<h3[^>]*>(.*)</h3>$', '$1').Trim()
            $blocks.Add("<!-- wp:heading {`"level`":3} -->`n<h3 class=`"wp-block-heading`">$inner</h3>`n<!-- /wp:heading -->")

        } elseif ($t -match '^<h4') {
            $inner = [regex]::Replace($t, '(?s)^<h4[^>]*>(.*)</h4>$', '$1').Trim()
            $blocks.Add("<!-- wp:heading {`"level`":4} -->`n<h4 class=`"wp-block-heading`">$inner</h4>`n<!-- /wp:heading -->")

        } elseif ($t -match '^<p') {
            $inner = [regex]::Replace($t, '(?s)^<p[^>]*>(.*)</p>$', '$1').Trim()
            if ($inner) {
                $blocks.Add("<!-- wp:paragraph -->`n<p>$inner</p>`n<!-- /wp:paragraph -->")
            }

        } elseif ($t -match '^<ul') {
            $l = [regex]::Replace($t, '\s+(style|class)="[^"]*"', '')
            $blocks.Add("<!-- wp:list -->`n$l`n<!-- /wp:list -->")

        } elseif ($t -match '^<ol') {
            $l = [regex]::Replace($t, '\s+(style|class)="[^"]*"', '')
            $blocks.Add("<!-- wp:list {`"ordered`":true} -->`n$l`n<!-- /wp:list -->")

        } elseif ($t -match '^<table') {
            $tbl = [regex]::Replace($t, '\s+(style|class|colspan|rowspan)="[^"]*"', '')
            $tbl = [regex]::Replace($tbl, '\s+(colspan|rowspan)="1"', '')
            $tbl = [regex]::Replace($tbl, '<p>(.*?)</p>', '$1')   # unwrap <p> inside cells
            # Wrap first row in <thead>, rest in <tbody>
            $tbl = [regex]::Replace($tbl, '(?s)^(<table>)\s*(<tr>.*?</tr>)', '$1<thead>$2</thead><tbody>')
            $tbl = $tbl -replace '</table>', '</tbody></table>'
            $blocks.Add("<!-- wp:table -->`n<figure class=`"wp-block-table`">$tbl</figure>`n<!-- /wp:table -->")
        }
    }
    return $blocks -join "`n`n"
}

# ── Slug generator from title ─────────────────────────────────────────────────
function New-Slug([string]$title) {
    ($title.ToLower() -replace '[^a-z0-9\s]', '' -replace '\s+', '-').Trim('-')
}

# ── WordPress: auth once, return session + nonce ──────────────────────────────
function Connect-WordPress([string]$site, [string]$user, [string]$pass) {
    $s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    Invoke-WebRequest -Uri "$site/wp-login.php" -Method POST -UseBasicParsing -MaximumRedirection 5 -WebSession $s `
        -Body @{ log=$user; pwd=$pass; wp_submit="Log In"; testcookie="1" } | Out-Null
    $nonce = (Invoke-WebRequest -Uri "$site/wp-admin/admin-ajax.php?action=rest-nonce" -WebSession $s -UseBasicParsing).Content
    return @{ Session=$s; Nonce=$nonce; Site=$site }
}

# ── Find post ID by slug (returns $null if not found) ─────────────────────────
function Find-PostId($wp, [string]$slug) {
    $r = Invoke-RestMethod -Uri "$($wp.Site)/wp-json/wp/v2/posts?slug=$slug&context=edit" `
        -WebSession $wp.Session -Headers @{"X-WP-Nonce"=$wp.Nonce}
    if ($r.Count -gt 0) { return $r[0].id }
    return $null
}

# ── Create or update a post ───────────────────────────────────────────────────
function Publish-Post($wp, $id, [string]$title, [string]$slug, [string]$content, [string]$status) {
    $uri    = if ($id) { "$($wp.Site)/wp-json/wp/v2/posts/$id" } else { "$($wp.Site)/wp-json/wp/v2/posts" }
    $body   = @{ title=$title; slug=$slug; content=$content; status=$status } | ConvertTo-Json -Depth 5
    return Invoke-RestMethod -Uri $uri -Method POST -Body $body -ContentType "application/json; charset=utf-8" `
        -WebSession $wp.Session -Headers @{"X-WP-Nonce"=$wp.Nonce}
}

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[1/4] Downloading Google Doc..." -ForegroundColor Cyan
$html = Get-DocHtml (Get-DocId $DocUrl)

# Pull title: prefer h1 in content, fall back to SEO Meta Title captured during parsing
$h1Match = [regex]::Match($html, '(?s)<h1[^>]*>(.*?)</h1>')
$title = if ($h1Match.Success) {
    ($h1Match.Groups[1].Value -replace '<[^>]+>','') -replace '\s+',' '
} else { '' }
$title = $title.Trim()
if (-not $title) { $title = $script:seoTitle }
if (-not $PostSlug) { $PostSlug = New-Slug $title }

Write-Host "[2/4] Converting to Gutenberg blocks..." -ForegroundColor Cyan
$gutenberg = Convert-ToGutenberg $html
$blockCount = ([regex]::Matches($gutenberg, '<!-- wp:')).Count
Write-Host "      $blockCount blocks generated, title: `"$title`""

Write-Host "[3/4] Connecting to WordPress..." -ForegroundColor Cyan
$wp = Connect-WordPress $WpSite $WpUser $WpPass

$id = if ($PostId) { $PostId } else { Find-PostId $wp $PostSlug }
$action = if ($id) { "Updating post $id" } else { "Creating new post" }
Write-Host "[4/4] $action..." -ForegroundColor Cyan

$result = Publish-Post $wp $id $title $PostSlug $gutenberg $Status

Write-Host "`n  Done!" -ForegroundColor Green
Write-Host "  ID     : $($result.id)"
Write-Host "  Title  : $($result.title.rendered)"
Write-Host "  Slug   : $($result.slug)"
Write-Host "  URL    : $($result.link)"
Write-Host "  Status : $($result.status)`n"
