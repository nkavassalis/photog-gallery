# photog-gallery

A static photo-gallery generator. Sibling project to
[baka-blog](https://github.com/nkavassalis/baka-blog) — same dark theme,
deploy pipeline (S3 + CloudFront) and editor shape, but the content unit
is a *gallery* (set of photos + description) rather than a blog post.

## Content layout

```
content/
  galleries/
    <gallery-slug>.md        # YAML frontmatter + optional intro markdown
  images/
    galleries/
      <gallery-slug>/
        001.jpg              # photos for that gallery
        002.jpg
        ...
```

A gallery file looks like:

```markdown
---
title: Tokyo, Spring 2026
date: 2026-04-15
description: Two weeks wandering Shibuya, Shinjuku and back-alley ramen.
tags: tokyo, japan, travel
cover: 001.jpg
photos:
  - file: 001.jpg
    caption: Shibuya crossing at night
  - file: 002.jpg
  - 003.jpg
---

Optional intro markdown rendered above the photo grid.
```

The `photos:` list is optional — if omitted, all images in the
matching `content/images/galleries/<slug>/` directory are used in
sorted order with no captions. Entries can be either bare filenames
or `{file, caption}` mappings.

## URLs

- Landing page: `/index.html` — newest galleries first, paginated
- Gallery page: `/galleries/<slug>.html` — description + thumbnail grid
- Photo page: `/galleries/<slug>/<photo-slug>.html` — full-size single photo
- Tag page: `/tags/<tag-slug>.html` — galleries with that tag
- RSS feed: `/feed.xml` — galleries, newest first

## Build & deploy

```sh
cp config.yaml.example config.yaml   # edit
pip install -r requirements.txt
make                                  # build + deploy (rebuilds only on hash change)
make clean all                        # force rebuild
make serve                            # run the editor on localhost
```

`make setup` configures S3 website hosting and CloudFront 403/404
redirects, same as in baka-blog.
