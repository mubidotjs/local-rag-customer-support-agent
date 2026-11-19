# Local RAG static showcase

Build from the repository root with Node.js 24. The output is static HTML,
CSS and images and needs no API, accounts, environment variables, Ollama, or
backend. Use a simple loopback HTTP server to preview the contents of
`showcase/dist`. The original application and its index stay outside that directory.

```sh
node showcase/build.mjs
node --test showcase/verify.test.mjs
```

For the responsive and keyboard smoke test, install the existing frontend's
locked dependencies and Playwright Chromium. Set `SHOWCASE_PLAYWRIGHT` to the
absolute path of `frontend/node_modules/playwright` and run
`node showcase/tools/browser-smoke.mjs`. The smoke test fulfills every file
from the build in browser memory. It starts no application or model server.

## Brand and image provenance

| File in `site/assets` | Use |
| --- | --- |
| `logo.svg`, `favicon.svg` | Project mark and scalable favicon |
| `favicon-16.png`, `favicon-32.png`, `apple-touch-icon.png` | 16 × 16, 32 × 32, and 180 × 180 icons |
| `og-image.png` | 1200 × 630 Open Graph, X and GitHub social preview |
| `project-thumbnail.png` | 960 × 540 portfolio project image |
| `frontend-desktop.png`, `frontend-mobile.png` | Byte-identical copies of the earlier real local frontend captures with fictional LumaDock content |

The source captures were `frontend/test-results/actual-local-desktop.png`
and `actual-local-mobile.png`, generated during the recorded local browser
acceptance. The two brand cards use a crop of that desktop image. To regenerate
the branded raster assets, use `node showcase/tools/render-brand.mjs` with
Playwright configured as above. Source captures are already in the showcase.

## GitHub presentation for owner approval

- **Description** — Local-first support assistant with exact citations, hybrid SQLite search, Ollama inference, and bounded LangGraph/MCP investigation.
- **Topics** — `rag`, `retrieval-augmented-generation`, `local-first`, `ollama`, `fastapi`, `sqlite`, `react`, `langgraph`, `model-context-protocol`, `customer-support`.
- **Homepage** — `https://rag.mubashirhussain.dev/` after production verification.
- **Social preview** — under repository Settings → General → Social preview, upload `site/assets/og-image.png`. Suggested alt description is identical to the page's Open Graph image description.

## Publish after approval

The current portfolio is served by Vercel and authoritative DNS replies for
`mubashirhussain.dev` use Dynadot nameservers. Use a separate static Vercel
project in the owner's existing account, subject to its current plan eligibility
and quota. Use the approved showcase commit, root directory `showcase`,
framework **Other**, no dependency installation, build command
`node build.mjs`, and output directory `dist`. Alternatively, upload exactly
the locally verified `dist` output to a static host. The tracked Vercel
configuration disables Git-triggered deployments. Keep the review branch
independent of any existing production project.

1. Confirm the selected Vercel scope, static project and commit. Publish the
   approved static artifact. Verify the host-assigned URL first.
2. In that Vercel project's Domains settings, add `rag.mubashirhussain.dev`
   and copy its assigned DNS requirements. In Dynadot DNS for
   `mubashirhussain.dev`, create only the subdomain record `rag` with type
   CNAME and the exact target that Vercel provides. If Vercel requests
   verification, add only its specified TXT name/value. Leave the apex,
   nameservers, existing subdomains and mail records in place.
3. Wait for Vercel to report valid configuration and an issued TLS certificate.
   Confirm the target against the authoritative nameserver and a public
   resolver. Confirm HTTP redirects to HTTPS, HTTPS returns 200 with a valid
   certificate, and the hostname is the intended project.
4. Open the production root, robots.txt, sitemap.xml, both icon formats,
   1200 × 630 OG card and both captures. Check status, content types, canonical
   URL, social previews and mobile layout. Confirm the publish artifact has
   no API or model endpoints. Activate the already-prepared portfolio card
   and GitHub homepage only after this check.

The production address is the intended canonical domain. Publishing, DNS
attachment, HTTPS and social crawler checks require owner approval and a live
static site. Milestone 5B container acceptance and Milestone 5C stay deferred.

References: [Add a Vercel domain](https://vercel.com/docs/domains/working-with-domains/add-a-domain) · [Dynadot CNAME instructions](https://www.dynadot.com/help/question/create-CNAME).
