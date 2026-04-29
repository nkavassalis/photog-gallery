from flask import Flask, request, jsonify, send_from_directory, render_template
from pathlib import Path
import yaml
from datetime import datetime
import subprocess
from PIL import Image
import re
import uuid

CONFIG_PATH = Path("config.yaml")

if CONFIG_PATH.exists():
    with open(CONFIG_PATH) as f:
        CONFIG = yaml.safe_load(f)
else:
    CONFIG = {}

EDITOR_HOST = CONFIG.get("editor", {}).get("host", "127.0.0.1")
EDITOR_PORT = CONFIG.get("editor", {}).get("port", 5001)

GALLERY_DIR = Path("content/galleries")
IMAGES_DIR = Path("content/images/galleries")
GALLERY_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

MAX_WIDTH = CONFIG.get("images", {}).get("max_width", 2400)
MAX_HEIGHT = CONFIG.get("images", {}).get("max_height", 2400)
DISPLAY_QUALITY = CONFIG.get("images", {}).get("display_quality", 85)

app = Flask(__name__)


def slugify(value):
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s or "untitled"


def read_frontmatter(text):
    if not text.startswith("---"):
        return {}
    try:
        _, meta_block, _ = text.split("---", 2)
        return yaml.safe_load(meta_block) or {}
    except Exception:
        return {}


def split_frontmatter(text):
    """Return (meta_dict, body_str). Body is whatever follows the closing '---'."""
    if not text.startswith("---"):
        return {}, text
    try:
        _, meta_block, body = text.split("---", 2)
    except ValueError:
        return {}, text
    meta = yaml.safe_load(meta_block) or {}
    return meta, body.lstrip("\n")


def write_gallery_file(slug, meta, body):
    """Serialize meta as YAML frontmatter and write back to disk."""
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=100).rstrip()
    text = f"---\n{fm}\n---\n\n{body}" if body else f"---\n{fm}\n---\n"
    (GALLERY_DIR / f"{slug}.md").write_text(text, encoding="utf-8")
    return text


def normalize_photos_meta(photos):
    """Coerce a frontmatter photos list into a list of {file, caption} dicts."""
    out = []
    for item in photos or []:
        if isinstance(item, str):
            out.append({"file": item, "caption": ""})
        elif isinstance(item, dict) and item.get("file"):
            out.append({"file": item["file"], "caption": item.get("caption", "") or ""})
    return out


def serialize_photos_meta(photos):
    """Inverse: bare filename when caption is empty, mapping when it isn't.

    Galleries that don't use captions keep the cleaner bare-filename form.
    """
    out = []
    for p in photos:
        if p.get("caption"):
            out.append({"file": p["file"], "caption": p["caption"]})
        else:
            out.append(p["file"])
    return out


def update_gallery_photos(slug, mutator):
    """Read gallery .md, apply mutator(list_of_{file,caption}), write back.

    The mutator returns the new list; we re-serialize it back to bare/mapping
    form and persist. Returns the updated raw text so the editor can refresh.
    """
    path = GALLERY_DIR / f"{slug}.md"
    if not path.exists():
        return None
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    photos_dir = IMAGES_DIR / slug
    on_disk = []
    if photos_dir.exists():
        on_disk = [
            f.name
            for f in sorted(photos_dir.iterdir())
            if f.is_file()
            and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        ]
    declared = normalize_photos_meta(meta.get("photos"))
    declared_files = {p["file"] for p in declared}
    # Bring any unlisted on-disk photos into the working list (auto-discover
    # parity), preserving order: declared first, then any new files in
    # filename order.
    working = list(declared)
    for fn in on_disk:
        if fn not in declared_files:
            working.append({"file": fn, "caption": ""})
    working = mutator(working) or []
    # Drop any entries whose source file no longer exists.
    on_disk_set = set(on_disk)
    working = [p for p in working if p["file"] in on_disk_set]
    if working:
        meta["photos"] = serialize_photos_meta(working)
    else:
        meta.pop("photos", None)
    return write_gallery_file(slug, meta, body)


def list_galleries():
    items = []
    for f in GALLERY_DIR.glob("*.md"):
        meta = read_frontmatter(f.read_text(encoding="utf-8"))
        date_str = str(meta.get("date", "")).strip()
        photo_count = len(list_gallery_photos(f.stem))
        items.append({
            "filename": f.name,
            "slug": f.stem,
            "title": meta.get("title", f.stem),
            "date": date_str,
            "photo_count": photo_count,
        })
    return sorted(items, key=lambda x: x["date"], reverse=True)


def list_gallery_photos(slug):
    folder = IMAGES_DIR / slug
    if not folder.exists():
        return []
    photos = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            photos.append({
                "filename": f.name,
                "url": f"/content-images/{slug}/{f.name}",
                "size": f.stat().st_size,
            })
    return photos


@app.route("/")
def index():
    return render_template("editor.html")


@app.route("/api/galleries")
def api_galleries():
    return jsonify(list_galleries())


@app.route("/api/gallery/<filename>", methods=["GET"])
def api_get_gallery(filename):
    path = GALLERY_DIR / filename
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    return jsonify({"content": path.read_text(encoding="utf-8")})


@app.route("/api/gallery/<filename>", methods=["POST"])
def api_save_gallery(filename):
    data = request.json
    (GALLERY_DIR / filename).write_text(data["content"], encoding="utf-8")
    return jsonify({"status": "saved"})


@app.route("/api/delete/<filename>", methods=["DELETE"])
def api_delete_gallery(filename):
    path = GALLERY_DIR / filename
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    slug = Path(filename).stem
    img_folder = IMAGES_DIR / slug
    if img_folder.exists():
        for f in img_folder.glob("*"):
            if f.is_file():
                f.unlink()
        img_folder.rmdir()
    path.unlink()
    return jsonify({"status": "deleted"})


@app.route("/api/new", methods=["POST"])
def api_new_gallery():
    data = request.json
    title = data.get("title", "Untitled Gallery")
    slug = slugify(title)
    filename = f"{slug}.md"
    today = datetime.now().strftime("%Y-%m-%d")
    template = f"""---
title: {title}
date: {today}
description: Write a short description of this gallery.
tags:
unlisted: false
---

Optional intro markdown rendered above the photo grid.
"""
    (GALLERY_DIR / filename).write_text(template, encoding="utf-8")
    (IMAGES_DIR / slug).mkdir(parents=True, exist_ok=True)
    return jsonify({"filename": filename, "slug": slug})


@app.route("/api/upload_image/<slug>", methods=["POST"])
def api_upload_image(slug):
    folder = IMAGES_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for file in request.files.getlist("files"):
        unique_name = f"{uuid.uuid4().hex}.jpg"
        save_path = folder / unique_name
        img = Image.open(file.stream)
        img = img.convert("RGB")
        img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)
        img.save(save_path, "JPEG", quality=DISPLAY_QUALITY, optimize=True)
        uploaded.append({"filename": save_path.name, "size": save_path.stat().st_size})
    # Reconcile the frontmatter so newly uploaded photos appear in the order
    # they were uploaded (after any previously declared photos).
    update_gallery_photos(slug, lambda working: working)
    return jsonify({"status": "uploaded", "files": uploaded})


@app.route("/api/images/<slug>", methods=["GET"])
def api_images(slug):
    """Return photos in the order declared by the gallery's frontmatter,
    falling back to filename order for any not yet listed. Includes captions."""
    path = GALLERY_DIR / f"{slug}.md"
    declared = []
    if path.exists():
        meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        declared = normalize_photos_meta(meta.get("photos"))
    declared_files = {p["file"] for p in declared}
    on_disk = list_gallery_photos(slug)
    on_disk_by_name = {p["filename"]: p for p in on_disk}
    ordered = []
    for d in declared:
        live = on_disk_by_name.get(d["file"])
        if live:
            ordered.append({**live, "caption": d["caption"]})
    for p in on_disk:
        if p["filename"] not in declared_files:
            ordered.append({**p, "caption": ""})
    return jsonify(ordered)


@app.route("/api/delete_image/<slug>/<filename>", methods=["DELETE"])
def api_delete_image(slug, filename):
    path = IMAGES_DIR / slug / filename
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    path.unlink()
    update_gallery_photos(slug, lambda working: working)
    return jsonify({"status": "deleted"})


@app.route("/api/photos/<slug>/reorder", methods=["POST"])
def api_reorder_photos(slug):
    new_order = (request.json or {}).get("order") or []
    if not isinstance(new_order, list):
        return jsonify({"error": "order must be a list"}), 400
    def mutate(working):
        by_name = {p["file"]: p for p in working}
        ordered = [by_name[fn] for fn in new_order if fn in by_name]
        # Append any files we didn't see in the request (paranoia).
        seen = set(new_order)
        ordered.extend(p for p in working if p["file"] not in seen)
        return ordered
    text = update_gallery_photos(slug, mutate)
    if text is None:
        return jsonify({"error": "gallery not found"}), 404
    return jsonify({"status": "reordered", "content": text})


@app.route("/api/photos/<slug>/<filename>/caption", methods=["POST"])
def api_set_caption(slug, filename):
    caption = (request.json or {}).get("caption", "")
    def mutate(working):
        for p in working:
            if p["file"] == filename:
                p["caption"] = caption.strip()
        return working
    text = update_gallery_photos(slug, mutate)
    if text is None:
        return jsonify({"error": "gallery not found"}), 404
    return jsonify({"status": "saved", "content": text})


@app.route("/api/regenerate", methods=["POST"])
def api_regenerate():
    try:
        result = subprocess.run(
            ["python", "make.py"], capture_output=True, text=True, check=False,
        )
        return jsonify({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/content-images/<slug>/<path:filename>")
def serve_image(slug, filename):
    return send_from_directory((IMAGES_DIR / slug).resolve(), filename)


if __name__ == "__main__":
    print(f"Starting editor on {EDITOR_HOST}:{EDITOR_PORT}")
    app.run(debug=True, host=EDITOR_HOST, port=EDITOR_PORT)
