import math
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import markdown
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
from xml.sax.saxutils import escape
from PIL import Image

CONFIG_PATH = "config.yaml"
HASHES_PATH = ".file_hashes.json"
GALLERY_DIR = Path("content/galleries")
GALLERY_IMG_DIR = Path("content/images/galleries")
STATIC_IMG_DIR = Path("static/images")
OUTPUT_DIR = Path("dist")
TEMPLATE_DIR = Path("templates")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?(.*)$", re.S)


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def compute_hash(file_path):
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def load_hashes():
    return json.load(open(HASHES_PATH)) if Path(HASHES_PATH).exists() else {}


def save_hashes(hashes):
    with open(HASHES_PATH, "w") as f:
        json.dump(hashes, f, indent=2)


def parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def slugify(value):
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or "untitled"


def parse_tags(value):
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    seen = []
    for item in items:
        name = str(item).strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def normalize_photo_entries(meta_photos, photos_dir):
    """Resolve the list of photos for a gallery.

    If the frontmatter declares a photos list, honor it (each entry can be
    a bare filename or {file, caption}). Otherwise auto-discover image
    files in the gallery's image directory, sorted alphanumerically.
    """
    if meta_photos:
        entries = []
        for p in meta_photos:
            if isinstance(p, str):
                entries.append({"file": p, "caption": ""})
            elif isinstance(p, dict) and p.get("file"):
                entries.append({"file": p["file"], "caption": p.get("caption", "")})
        return entries
    if not photos_dir.exists():
        return []
    return [
        {"file": img.name, "caption": ""}
        for img in sorted(photos_dir.iterdir())
        if img.is_file() and img.suffix.lower() in IMG_EXTS
    ]


def build_galleries():
    galleries = []
    md = markdown.Markdown()
    for gfile in GALLERY_DIR.glob("*.md"):
        meta, body = parse_frontmatter(gfile.read_text())
        slug = gfile.stem
        photos_dir = GALLERY_IMG_DIR / slug
        entries = normalize_photo_entries(meta.get("photos"), photos_dir)
        photos = []
        for e in entries:
            stem = Path(e["file"]).stem
            photos.append(
                {
                    "file": e["file"],
                    "slug": slugify(stem),
                    "caption": e.get("caption", ""),
                }
            )
        date_obj = datetime.strptime(str(meta["date"]), "%Y-%m-%d")
        meta["date_readable"] = date_obj.strftime("%B %d, %Y")
        tags = parse_tags(meta.get("tags"))
        cover = meta.get("cover") or (photos[0]["file"] if photos else None)
        galleries.append(
            {
                "slug": slug,
                "meta": meta,
                "body_html": md.convert(body) if body.strip() else "",
                "photos": photos,
                "tags": [{"name": t, "slug": slugify(t)} for t in tags],
                "cover": cover,
            }
        )
        md.reset()
    return sorted(galleries, key=lambda g: str(g["meta"]["date"]), reverse=True)


def build_tag_index(galleries):
    index = {}
    for g in galleries:
        if str(g["meta"].get("unlisted", "")).lower() == "true":
            continue
        for tag in g["tags"]:
            entry = index.setdefault(
                tag["slug"], {"name": tag["name"], "slug": tag["slug"], "galleries": []}
            )
            entry["galleries"].append(g)
    return index


def build_tag_cloud(tag_index):
    return [
        {"name": t["name"], "slug": t["slug"], "count": len(t["galleries"])}
        for t in sorted(tag_index.values(), key=lambda x: x["name"].lower())
    ]


def compute_adjacent_galleries(galleries):
    listed = [
        g for g in galleries if str(g["meta"].get("unlisted", "")).lower() != "true"
    ]
    nav = {}
    for i, g in enumerate(listed):
        nav[g["slug"]] = {
            "newer": listed[i - 1] if i > 0 else None,
            "older": listed[i + 1] if i < len(listed) - 1 else None,
        }
    return nav


def _save_resized(src, dst, max_w, max_h, quality):
    """Save src into dst capped to (max_w, max_h), preserving aspect."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.thumbnail((max_w, max_h))
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=quality, optimize=True)


def process_images(galleries, config):
    """For each photo, write a display-size copy and a thumbnail to dist.

    Both are regenerated whenever the source file is newer than the
    output, so updating an image rebuilds it on the next run.
    """
    img_cfg = config["images"]
    out_root = OUTPUT_DIR / "images" / "galleries"
    for g in galleries:
        src_dir = GALLERY_IMG_DIR / g["slug"]
        out_dir = out_root / g["slug"]
        thumb_dir = out_dir / "thumbs"
        for photo in g["photos"]:
            src = src_dir / photo["file"]
            if not src.exists():
                continue
            display_dst = out_dir / photo["file"]
            thumb_dst = thumb_dir / photo["file"]
            src_mtime = src.stat().st_mtime
            if (
                not display_dst.exists()
                or display_dst.stat().st_mtime < src_mtime
            ):
                _save_resized(
                    src,
                    display_dst,
                    img_cfg["max_width"],
                    img_cfg["max_height"],
                    img_cfg["display_quality"],
                )
            if not thumb_dst.exists() or thumb_dst.stat().st_mtime < src_mtime:
                _save_resized(
                    src,
                    thumb_dst,
                    img_cfg["thumb_width"],
                    img_cfg["thumb_height"],
                    img_cfg["thumb_quality"],
                )


def render_templates(galleries, config):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    per_page = config["website"]["galleries_per_page"]
    related_limit = config["website"].get("related_galleries_per_tag", 5)

    listed = [
        g for g in galleries if str(g["meta"].get("unlisted", "")).lower() != "true"
    ]
    total_pages = max(1, math.ceil(len(listed) / per_page))

    tag_index = build_tag_index(galleries)
    tag_cloud = build_tag_cloud(tag_index)
    adjacent = compute_adjacent_galleries(galleries)

    index_t = env.get_template("index.html")
    for page in range(1, total_pages + 1):
        s = (page - 1) * per_page
        chunk = listed[s : s + per_page]
        name = "index.html" if page == 1 else f"page{page}.html"
        (OUTPUT_DIR / name).write_text(
            index_t.render(
                galleries=chunk,
                config=config,
                current_page=page,
                total_pages=total_pages,
                tag_cloud=tag_cloud,
                path_prefix="",
            )
        )

    tag_t = env.get_template("tag.html")
    tags_dir = OUTPUT_DIR / "tags"
    tags_dir.mkdir(exist_ok=True)
    for tag in tag_index.values():
        (tags_dir / f"{tag['slug']}.html").write_text(
            tag_t.render(
                tag=tag,
                galleries=tag["galleries"],
                config=config,
                tag_cloud=tag_cloud,
                path_prefix="../",
            )
        )

    gallery_t = env.get_template("gallery.html")
    photo_t = env.get_template("photo.html")
    galleries_root = OUTPUT_DIR / "galleries"
    galleries_root.mkdir(exist_ok=True)
    for g in galleries:
        nav = adjacent.get(g["slug"], {"newer": None, "older": None})
        related = compute_related_galleries(g, tag_index, related_limit)
        (galleries_root / f"{g['slug']}.html").write_text(
            gallery_t.render(
                gallery=g,
                config=config,
                newer=nav["newer"],
                older=nav["older"],
                related_sections=related,
                path_prefix="../",
            )
        )
        photo_subdir = galleries_root / g["slug"]
        photo_subdir.mkdir(exist_ok=True)
        for i, photo in enumerate(g["photos"]):
            prev_p = g["photos"][i - 1] if i > 0 else None
            next_p = g["photos"][i + 1] if i < len(g["photos"]) - 1 else None
            (photo_subdir / f"{photo['slug']}.html").write_text(
                photo_t.render(
                    gallery=g,
                    photo=photo,
                    prev_photo=prev_p,
                    next_photo=next_p,
                    config=config,
                    path_prefix="../../",
                )
            )

    not_found_t = env.get_template("404.html")
    (OUTPUT_DIR / "404.html").write_text(not_found_t.render(config=config))

    generate_rss_feed(listed, OUTPUT_DIR, config)


def compute_related_galleries(gallery, tag_index, limit):
    sections = []
    for tag in gallery["tags"]:
        entry = tag_index.get(tag["slug"])
        if not entry:
            continue
        others = [g for g in entry["galleries"] if g["slug"] != gallery["slug"]][:limit]
        if others:
            sections.append({"tag": tag, "galleries": others})
    return sections


def copy_static_assets():
    (OUTPUT_DIR / "style.css").write_text(Path("static/style.css").read_text())
    out_images = OUTPUT_DIR / "images"
    out_images.mkdir(exist_ok=True, parents=True)
    if STATIC_IMG_DIR.exists():
        for image in STATIC_IMG_DIR.glob("*.*"):
            (out_images / image.name).write_bytes(image.read_bytes())


def generate_rss_feed(galleries, output_dir, config, feed_size=25):
    items = []
    base_url = config["website"]["base_url"]
    feed_url = f"{base_url}/feed.xml"
    for g in galleries[:feed_size]:
        link = f"{base_url}/galleries/{g['slug']}.html"
        title = escape(g["meta"]["title"])
        desc = escape(g["meta"].get("description", title))
        pub = datetime.strptime(str(g["meta"]["date"]), "%Y-%m-%d").strftime(
            "%a, %d %b %Y 00:00:00 GMT"
        )
        items.append(
            f"""
        <item>
            <title>{title}</title>
            <link>{link}</link>
            <description>{desc}</description>
            <pubDate>{pub}</pubDate>
            <guid>{link}</guid>
        </item>"""
        )
    rss = f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(config["website"]["title"])}</title>
    <link>{base_url}</link>
    <atom:link href="{feed_url}" rel="self" type="application/rss+xml" />
    <description>{escape(config["website"]["description"])}</description>
    {''.join(items)}
  </channel>
</rss>"""
    (output_dir / "feed.xml").write_text(rss, encoding="utf-8")


def setup_hosting(config):
    bucket = config["aws"]["s3_bucket"]
    dist_id = config["aws"]["cloudfront_dist_id"]

    print(f"Configuring S3 website on s3://{bucket} (index.html / 404.html)...")
    subprocess.run(
        [
            "aws", "s3", "website", f"s3://{bucket}",
            "--index-document", "index.html",
            "--error-document", "404.html",
        ],
        check=True,
    )

    print(f"Configuring CloudFront custom error response on {dist_id}...")
    result = subprocess.run(
        ["aws", "cloudfront", "get-distribution-config", "--id", dist_id],
        check=True, capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    etag = payload["ETag"]
    dist_config = payload["DistributionConfig"]

    desired_rules = [
        {
            "ErrorCode": code,
            "ResponsePagePath": "/404.html",
            "ResponseCode": "404",
            "ErrorCachingMinTTL": 10,
        }
        for code in (403, 404)
    ]
    errors = dist_config.setdefault("CustomErrorResponses", {"Quantity": 0, "Items": []})
    items = errors.get("Items", []) or []
    changed = False
    for desired in desired_rules:
        existing = next((i for i in items if i.get("ErrorCode") == desired["ErrorCode"]), None)
        if existing and all(existing.get(k) == v for k, v in desired.items()):
            continue
        if existing:
            existing.update(desired)
        else:
            items.append(desired)
        changed = True
    if not changed:
        print("CloudFront already has the desired 403/404 rules; skipping update.")
        return
    errors["Items"] = items
    errors["Quantity"] = len(items)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(dist_config, f)
        cfg_path = f.name
    subprocess.run(
        [
            "aws", "cloudfront", "update-distribution",
            "--id", dist_id,
            "--if-match", etag,
            "--distribution-config", f"file://{cfg_path}",
        ],
        check=True,
    )
    print("CloudFront distribution updated. Propagation may take several minutes.")


def sync_s3_and_invalidate(config):
    bucket = config["aws"]["s3_bucket"]
    dist_id = config["aws"]["cloudfront_dist_id"]
    subprocess.run(
        ["aws", "s3", "sync", str(OUTPUT_DIR), f"s3://{bucket}", "--acl", "public-read"],
        check=True,
    )
    subprocess.run(
        [
            "aws", "cloudfront", "create-invalidation",
            "--distribution-id", dist_id,
            "--paths", "/*",
        ],
        check=True,
    )
    print("Upload complete and CloudFront invalidated.")


def collect_input_hashes():
    hashes = {
        "config": compute_hash(Path(CONFIG_PATH)),
        "style": compute_hash(Path("static/style.css")),
    }
    for tpl in TEMPLATE_DIR.glob("*.html"):
        hashes[f"tpl/{tpl.name}"] = compute_hash(tpl)
    for f in GALLERY_DIR.glob("*.md"):
        hashes[str(f)] = compute_hash(f)
    if GALLERY_IMG_DIR.exists():
        for f in GALLERY_IMG_DIR.glob("**/*"):
            if f.is_file():
                hashes[str(f)] = compute_hash(f)
    if STATIC_IMG_DIR.exists():
        for f in STATIC_IMG_DIR.glob("*.*"):
            hashes[str(f)] = compute_hash(f)
    return hashes


def main():
    config = load_config()
    prior = load_hashes()
    current = collect_input_hashes()
    if prior == current:
        print("No changes detected; skipping build.")
        return
    galleries = build_galleries()
    process_images(galleries, config)
    render_templates(galleries, config)
    copy_static_assets()
    sync_s3_and_invalidate(config)
    save_hashes(current)
    print("Site rebuilt and deployed.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_hosting(load_config())
    else:
        main()
