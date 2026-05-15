# Changelog

## 1.0.0

- Add no-cache metadata to generated report HTML to reduce stale pages in browsers and Home Assistant WebView.
- Publish `latest.html` through a temporary file and atomic replace so Home Assistant does not serve a partially updated file.
- Print a cache-busted `/local/latest.html?v=YYYYMMDDHHMMSS` URL in add-on logs after each successful report publish.
- Document the cache-busted URL behavior in the add-on docs.
- Keep upgrade guidance for existing installs that still publish under `/local/stock/latest.html`.
- Avoid printing misleading `/local/...` URLs when `public_subdir` is outside `www`.
