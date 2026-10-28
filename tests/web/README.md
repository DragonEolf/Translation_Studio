# Browser verification

These checks exercise the real HTML, CSS and JavaScript in Chromium. All API
responses are mocked: no document text, credentials or translation requests leave
the test browser.

Install Playwright in a temporary directory to avoid adding Node dependencies to
the Python application, then run:

```sh
npm install --prefix /tmp/translation-studio-web-tests playwright
/tmp/translation-studio-web-tests/node_modules/.bin/playwright install chromium --only-shell
NODE_PATH=/tmp/translation-studio-web-tests/node_modules node tests/web/studio.spec.cjs
```

Screenshots are saved under `output/ui-verification/` (ignored by Git).

Coverage includes responsive layouts, native language labels, invalid/empty/large
uploads, both layout preferences, OCR and blank-page messaging, partial output,
font availability, encrypted and invalid PDFs, page limits, provider failures,
draft downloads, rendering-stage controls, connection recovery and a lost
translation-submission response. Recovery tests verify that reconnecting or
reloading does not issue another translation request.
