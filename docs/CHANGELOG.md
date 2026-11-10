# Changelog

## Unreleased — image groups

### Added
- **Image groups** (e.g. one per event). Create, rename and delete groups under *Manage groups* in the media library. An image can belong to several groups.
- **Batch upload into a group.** Choose a group next to *Upload new media* and every image in that upload is added to it.
- **Add and remove images.** Select images with the checkboxes and use the bulk bar to add them to a group or remove them from the group being viewed, or use the group checklist in *Edit details*. Removing an image from a group never deletes the file. Deleting a group keeps its images.
- **Group filter** in the media library and in the editor's image picker.
- New migration `0005_media_groups` creates `media_groups` and `media_group_items`.

### Permissions
- Creating groups and adding images: `upload_media`. Renaming, deleting groups and removing images: `manage_media`.

### Known limitation
- Site export/import does not include image groups yet, so an imported site keeps its images but not their group memberships.

## Unreleased — media picker and image viewer

### Changed
- **Image library picker (post and page editors).** Search by filename or title, and Previous/Next paging through the whole library (it previously showed only the first 20 images with no way to reach the rest). Double-click inserts.
- **Inserting images into the editor.** The "Image library" button now asks for a size: *Auto fit to width* (default) scales the image down to the content width and keeps its aspect ratio, so large photos no longer need to be dragged smaller; *Keep original size* inserts the file's own dimensions. Images are never scaled up.
- **Media library viewer.** Clicking an image opens a full-size preview with its dimensions, file size and position in the page, Previous/Next buttons and arrow keys, and an "Edit details" shortcut. Ctrl/Shift-click still opens the image in a new tab.

### Tests
- No automated tests added for these front-end changes; they were checked in a headless browser against the rendered templates.

## Unreleased — Phase 1 (security), items 1 and 3

### Security
- **Stored XSS via media upload fixed.** `/admin/media/upload` now applies the same allow-list as `/upload_image` (png, jpg, jpeg, gif, webp). All image uploads (`/admin/media/upload`, `/upload_image`, the migration importer, image files sent to `/admin/upload_file`) go through the new `images.store_verified_image`: Pillow must decode the file, the image is re-encoded (drops trailing bytes and EXIF/GPS), the stored extension and MIME type come from the decoded format, the client `Content-Type` is ignored, files above 50 megapixels are refused, and nothing is left on disk after a failure.
- `/uploads/<name>` always sends `X-Content-Type-Options: nosniff`; non-image, non-PDF, non-text files are served as `application/octet-stream` with `Content-Disposition: attachment`; everything except PDF gets `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'none'; sandbox`. This also neutralises HTML files left behind by the old bug.
- `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD` now defaults to off (`1` enables browser uploads). README, `docs/SECURITY.md`, `docs/EXTENSIONS.md` and `.env.example` agree.

### Tests
- `tests/test_uploads.py` (14 tests).

### Migrations
- None.
