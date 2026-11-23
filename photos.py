#!/usr/bin/env python3
"""Search Pexels, Pixabay and Unsplash from Alfred.

    photos.py search <query>     Script Filter output
    photos.py download <url>     save a photo, details come in as workflow variables
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Alfred passes the Workflow Configuration in as environment variables.
# The /tmp fallback is only there for running the script from a terminal.
CACHE_DIR = os.environ.get("alfred_workflow_cache", "/tmp/stockphoto")
PROVIDER = os.environ.get("provider", "pexels")
SIZE = os.environ.get("download_size", "large")
LANG = "de" if os.environ.get("language") == "de" else "en"
PER_PAGE = 30
# plain text field in the config, so don't trust it
try:
    CACHE_DAYS = max(1, int(os.environ.get("cache_days", "7")))
except ValueError:
    CACHE_DAYS = 7

USER_AGENT = "alfred-stock-photo-search/1.1"
# Unsplash wants UTM parameters on every link back to their site
UTM = "utm_source=alfred_stock_photo_search&utm_medium=referral"

KEYS = {
    "pexels": os.environ.get("PEXELS_API_KEY", "").strip(),
    "pixabay": os.environ.get("PIXABAY_API_KEY", "").strip(),
    "unsplash": os.environ.get("UNSPLASH_ACCESS_KEY", "").strip(),
}
NAMES = {"pexels": "Pexels", "pixabay": "Pixabay", "unsplash": "Unsplash"}
KEY_URLS = {
    "pexels": "https://www.pexels.com/api/",
    "pixabay": "https://pixabay.com/api/docs/",
    "unsplash": "https://unsplash.com/oauth/applications",
}
PREFIXES = {"px": "pexels", "pb": "pixabay", "us": "unsplash"}

# Only English and German so far. Add another block here for more languages.
STRINGS = {
    "en": {
        "photo": "Photo",
        "credit": "Photo by {author} on {provider}",
        "credit_anon": "Photo on {provider}",
        "hints": "↩ download · ⇧ preview · ⌘ page · ⌥ link · ⌃ credit",
        "open_page": "Open photo page in browser",
        "copy_info": "Copy link with details: ",
        "copy_credit": "Copy credit: ",
        "no_key": "{provider} API key missing",
        "no_key_sub": "Add it in the Workflow's Configuration. ↩ opens ",
        "prompt": "Search royalty-free photos on {provider}…",
        "prompt_sub": "Type a search term · prefix px: / pb: / us: switches provider",
        "http_error": "{provider}: HTTP error {code}",
        "check_key": "Check your API key",
        "rate_limit": "Rate limit reached, try again later",
        "conn_error": "Connection error ({provider})",
        "no_results": "No results for “{query}” on {provider}",
        "no_results_sub": "Try a different search term",
        "saved": "Photo saved",
        "failed": "Download failed",
    },
    "de": {
        "photo": "Foto",
        "credit": "Foto von {author} auf {provider}",
        "credit_anon": "Foto auf {provider}",
        "hints": "↩ laden · ⇧ Vorschau · ⌘ Seite · ⌥ Link · ⌃ Credit",
        "open_page": "Fotoseite im Browser öffnen",
        "copy_info": "Link + Angaben kopieren: ",
        "copy_credit": "Credit kopieren: ",
        "no_key": "API-Key für {provider} fehlt",
        "no_key_sub": "In der Workflow-Konfiguration eintragen. ↩ öffnet ",
        "prompt": "Lizenzfreie Fotos auf {provider} suchen …",
        "prompt_sub": "Suchbegriff eingeben · Präfix px: / pb: / us: wechselt den Anbieter",
        "http_error": "{provider}: HTTP-Fehler {code}",
        "check_key": "API-Key prüfen",
        "rate_limit": "Rate-Limit erreicht, später erneut versuchen",
        "conn_error": "Verbindungsfehler ({provider})",
        "no_results": "Keine Treffer für „{query}“ auf {provider}",
        "no_results_sub": "Anderen Begriff versuchen",
        "saved": "Foto gespeichert",
        "failed": "Download fehlgeschlagen",
    },
}


def t(key, **kwargs):
    return STRINGS[LANG][key].format(**kwargs)


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.load(resp)


def with_utm(url):
    if not url:
        return ""
    return url + ("&" if "?" in url else "?") + UTM


def cache_file(name):
    return os.path.join(CACHE_DIR, name)


def thumb_file(url):
    return cache_file("thumb_" + hashlib.md5(url.encode()).hexdigest() + ".jpg")


def has_thumb(url):
    path = thumb_file(url)
    return os.path.exists(path) and os.path.getsize(path) > 0


def fetch_thumbs(urls):
    # Downloads run detached; the Script Filter reruns until all icons are there.
    # Write to .part first so Alfred never picks up a half-written file.
    for url in urls:
        path = thumb_file(url)
        if not os.path.exists(path):
            subprocess.Popen(
                ["/bin/sh", "-c", 'curl -sfL --max-time 8 -o "$1.part" "$2" && mv "$1.part" "$1"',
                 "sh", path, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def message(title, subtitle="", url=None):
    # A single row for hints and errors. If it gets a url, ↩ opens that
    # in the browser instead of starting a download (see main()).
    item = {"title": title, "subtitle": subtitle, "valid": url is not None}
    if url:
        item["arg"] = url
        item["variables"] = {"open_only": "1"}
    return {"items": [item]}


# All providers return the same dict shape:
# id, thumb, preview, download {original, large, medium}, page,
# author, author_url, width, height, desc, trigger

def search_pexels(query, key):
    params = urllib.parse.urlencode({
        "query": query,
        "per_page": PER_PAGE,
        "locale": "de-DE" if LANG == "de" else "en-US",
    })
    # Pexels takes the bare key as Authorization header, no "Bearer" etc.
    data = get_json("https://api.pexels.com/v1/search?" + params, {"Authorization": key})
    photos = []
    for p in data.get("photos", []):
        src = p.get("src", {})
        photos.append({
            "id": str(p["id"]),
            "thumb": src.get("tiny") or src.get("small"),
            "preview": src.get("large"),
            "download": {
                "original": src.get("original"),
                "large": src.get("large2x"),  # ~1880px, close enough to the others
                "medium": src.get("large"),
            },
            "page": p.get("url"),
            "author": p.get("photographer", ""),
            "author_url": p.get("photographer_url", ""),
            "width": p.get("width"),
            "height": p.get("height"),
            "desc": p.get("alt", ""),
            "trigger": "",
        })
    return photos


def search_pixabay(query, key):
    params = urllib.parse.urlencode({
        "key": key,
        "q": query,
        "per_page": PER_PAGE,
        "lang": LANG,
        "image_type": "photo",
        "safesearch": "true",
    })
    data = get_json("https://pixabay.com/api/?" + params)
    photos = []
    for p in data.get("hits", []):
        large = p.get("largeImageURL")
        user = p.get("user", "")
        # tags come back with duplicates once Pixabay translates them
        tags = dict.fromkeys(tag.strip() for tag in p.get("tags", "").split(",") if tag.strip())
        photos.append({
            "id": str(p["id"]),
            "thumb": p.get("previewURL"),
            "preview": p.get("webformatURL"),
            # imageURL/fullHDURL are only included with full API access
            "download": {
                "original": p.get("imageURL") or p.get("fullHDURL") or large,
                "large": p.get("fullHDURL") or large,
                "medium": large,
            },
            "page": p.get("pageURL"),
            "author": user,
            "author_url": f"https://pixabay.com/users/{user}-{p.get('user_id')}/" if user else "",
            "width": p.get("imageWidth"),
            "height": p.get("imageHeight"),
            "desc": ", ".join(tags),
            "trigger": "",
        })
    return photos


def search_unsplash(query, key):
    params = urllib.parse.urlencode({"query": query, "per_page": PER_PAGE, "lang": LANG})
    data = get_json(
        "https://api.unsplash.com/search/photos?" + params,
        {"Authorization": "Client-ID " + key, "Accept-Version": "v1"},
    )
    photos = []
    for p in data.get("results", []):
        urls = p.get("urls", {})
        user = p.get("user", {})
        links = p.get("links", {})
        # raw + params goes through Unsplash's resizer, so we get a sane size
        # instead of the 20+ MB original
        large = urls["raw"] + "&w=2400&fm=jpg&q=85" if urls.get("raw") else urls.get("full")
        photos.append({
            "id": p["id"],
            "thumb": urls.get("thumb"),
            "preview": urls.get("regular"),
            "download": {
                "original": urls.get("full"),
                "large": large,
                "medium": urls.get("regular"),
            },
            "page": with_utm(links.get("html")),
            "author": user.get("name", ""),
            "author_url": with_utm(user.get("links", {}).get("html")),
            "width": p.get("width"),
            "height": p.get("height"),
            "desc": p.get("alt_description") or p.get("description") or "",
            # has to be called once per download (Unsplash API guidelines)
            "trigger": links.get("download_location", ""),
        })
    return photos


SEARCH = {"pexels": search_pexels, "pixabay": search_pixabay, "unsplash": search_unsplash}


def clean_cache():
    """Remove thumbnails and cached searches older than CACHE_DAYS, at most once an hour."""
    marker = cache_file(".last_cleanup")
    now = time.time()
    try:
        if now - os.path.getmtime(marker) < 3600:
            return
    except OSError:
        pass
    os.makedirs(CACHE_DIR, exist_ok=True)
    cutoff = now - CACHE_DAYS * 86400
    for name in os.listdir(CACHE_DIR):
        path = cache_file(name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass
    open(marker, "w").close()


def parse_query(raw):
    # "pb: berge" -> ("pixabay", "berge")
    provider = PROVIDER if PROVIDER in SEARCH else "pexels"
    query = raw.strip()
    m = re.match(r"(px|pb|us):\s*(.*)", query, re.IGNORECASE)
    if m:
        provider = PREFIXES[m.group(1).lower()]
        query = m.group(2).strip()
    return provider, query


def search(provider, query):
    # cached so the reruns while thumbnails load don't hit the API again
    os.makedirs(CACHE_DIR, exist_ok=True)
    digest = hashlib.md5(f"{provider}\0{LANG}\0{query}".encode()).hexdigest()
    path = cache_file(f"search_{digest}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    photos = SEARCH[provider](query, KEYS[provider])
    with open(path, "w") as f:
        json.dump(photos, f)
    return photos


def to_item(photo, provider):
    name = NAMES[provider]
    dl = photo["download"]
    url = dl.get(SIZE) or dl.get("large") or dl.get("original")
    page = photo["page"] or url
    size = f"{photo['width']}×{photo['height']}" if photo.get("width") and photo.get("height") else ""

    desc = photo["desc"] or f"{t('photo')} #{photo['id']}"
    title = desc[0].upper() + desc[1:]
    if photo["author"]:
        credit = t("credit", author=photo["author"], provider=name)
    else:
        credit = t("credit_anon", provider=name)

    # ⌥ copies this, meant for pasting into notes or mails
    info = f"{title}\n{credit}" + (f" ({size})" if size else "") + f"\n{page}"
    credit_text = credit + (f" – {photo['author_url']}" if photo["author_url"] else "") + f"\n{page}"
    subtitle = " · ".join(x for x in (size, credit, t("hints")) if x)

    thumb = photo["thumb"]
    icon = thumb_file(thumb) if thumb and has_thumb(thumb) else "icon.png"

    return {
        "uid": f"{provider}-{photo['id']}",
        "title": title,
        "subtitle": subtitle,
        "arg": url,
        "icon": {"path": icon},
        "quicklookurl": photo["preview"],
        "variables": {
            "photo_provider": provider,
            "photo_id": photo["id"],
            "photo_author": photo["author"],
            "photo_trigger": photo["trigger"],
        },
        "mods": {
            "cmd": {"arg": page, "subtitle": t("open_page")},
            "alt": {"arg": info, "subtitle": t("copy_info") + page},
            "ctrl": {"arg": credit_text, "subtitle": t("copy_credit") + credit},
        },
        "text": {"copy": info, "largetype": info},
    }


def script_filter(raw_query):
    clean_cache()
    provider, query = parse_query(raw_query)
    name = NAMES[provider]

    if not KEYS[provider]:
        return message(t("no_key", provider=name), t("no_key_sub") + KEY_URLS[provider], KEY_URLS[provider])
    if len(query) < 2:
        return message(t("prompt", provider=name), t("prompt_sub"))

    try:
        photos = [p for p in search(provider, query) if any(p["download"].values())]
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            hint = t("check_key")
        elif e.code == 429:
            hint = t("rate_limit")
        else:
            hint = ""
        return message(t("http_error", provider=name, code=e.code), hint)
    except Exception as e:
        return message(t("conn_error", provider=name), str(e))

    if not photos:
        return message(t("no_results", query=query, provider=name), t("no_results_sub"))

    thumbs = [p["thumb"] for p in photos if p["thumb"]]
    missing = [u for u in thumbs if not has_thumb(u)]
    fetch_thumbs(missing)

    result = {"items": [to_item(p, provider) for p in photos], "skipknowledge": True}
    # Alfred calls us again after 0.5s and keeps going until rerun is gone,
    # i.e. until every thumbnail is on disk
    if missing:
        result["rerun"] = 0.5
    return result


def download(url):
    provider = os.environ.get("photo_provider", PROVIDER)
    photo_id = os.environ.get("photo_id", "photo")
    author = os.environ.get("photo_author", "")
    trigger = os.environ.get("photo_trigger", "")

    folder = os.path.expanduser(os.environ.get("download_dir") or "~/Downloads")
    os.makedirs(folder, exist_ok=True)

    if provider == "unsplash" and trigger:
        try:
            get_json(trigger, {"Authorization": "Client-ID " + KEYS["unsplash"]})
        except Exception:
            pass  # not worth failing the download over

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content_type = resp.headers.get_content_type()
        data = resp.read()

    # pexels-max-muster-417074.jpg, with -2, -3 ... if it already exists
    ext = {"image/png": ".png", "image/webp": ".webp", "image/avif": ".avif"}.get(content_type, ".jpg")
    slug = re.sub(r"[^a-z0-9]+", "-", author.lower()).strip("-")
    base = "-".join(x for x in (provider, slug, photo_id) if x)
    path = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}-{n}{ext}")
        n += 1
    with open(path, "wb") as f:
        f.write(data)

    if os.environ.get("reveal_in_finder", "1") == "1":
        subprocess.run(["open", "-R", path])
    return path


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "search"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if mode != "download":
        print(json.dumps(script_filter(arg)))
        return
    if os.environ.get("open_only") == "1":
        subprocess.run(["open", arg])
        return

    try:
        path = download(arg)
        out = {"arg": path, "variables": {"notify_title": t("saved"), "notify_text": os.path.basename(path)}}
    except Exception as e:
        out = {"arg": str(e), "variables": {"notify_title": t("failed"), "notify_text": str(e)}}
    print(json.dumps({"alfredworkflow": out}))


if __name__ == "__main__":
    main()
