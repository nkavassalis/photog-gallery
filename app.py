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
    return jsonify({"status": "uploaded", "files": uploaded})


@app.route("/api/images/<slug>", methods=["GET"])
def api_images(slug):
    return jsonify(list_gallery_photos(slug))


@app.route("/api/delete_image/<slug>/<filename>", methods=["DELETE"])
def api_delete_image(slug, filename):
    path = IMAGES_DIR / slug / filename
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    path.unlink()
    return jsonify({"status": "deleted"})


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
