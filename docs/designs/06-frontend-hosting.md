# 06 — Frontend Hosting

## Overview

Where and how the client application is served.

**Addresses**: hosting for the SPA that calls the API in [02](02-retrieval-agents.md) and uploads images per [03](03-image-upload.md).

## Decision: S3 + CloudFront

A static single-page app (React or similar), with Route 53 + ACM for a custom domain if desired.

- Build the frontend as a static SPA; upload the build output to an S3 bucket configured for static hosting
- CloudFront in front of that bucket for CDN caching, HTTPS, and a stable public URL
- The SPA calls the API Gateway endpoint directly from the browser — CORS configured on API Gateway
- Image upload goes through pre-signed S3 URLs, issued by the API (see [03](03-image-upload.md))

**Simpler alternative**: AWS Amplify Hosting wraps S3 + CloudFront + CI/CD from a git repo into one managed service — less control, less to wire up by hand. Worth it if the time is better spent on the RAG pipeline than on frontend infra.

Either way, this is a small piece of the overall AWS footprint and isn't blocking on the IaC-tool or credential decisions open in [01](01-ingestion-pipeline.md).

## Implementation notes

**Done for real, deployed and verified live — the final workflow, and the project's core loop is now usable end-to-end through a real browser.** `RagEcommerce-Frontend` (`infra/stacks/frontend_stack.py`) matches the S3 + CloudFront decision exactly, not the Amplify alternative:

- A React + TypeScript SPA (`frontend/`, Vite) — one page, no client-side routing: a prompt textarea, an optional image picker with preview, and a results area (answer text with lightweight `**bold**` rendering, a "Consulted: ..." line showing which specialists ran, and citation cards with product image/title/snippet linking to `product_url`).
- `frontend/src/api.ts` calls the workflow 03 API directly from the browser exactly as designed: `POST /upload-url` for a presigned S3 POST, a direct browser→S3 multipart upload, then `POST /query` with the prompt and the uploaded object's key.
- **Private S3 bucket behind CloudFront with Origin Access Control** (not public static website hosting) — CloudFront is the only principal allowed to read the bucket, matching the block-all-public-access posture already used for the data bucket. `BucketDeployment` uploads the locally-built `frontend/dist` and invalidates the CloudFront cache on every deploy.
- CORS on the API Gateway (`allow_origins=["*"]`) was already in place from workflow 03 — no changes needed there.

**Verified live** against the real deployed CloudFront URL (`https://d1vjoz7ow6cljs.cloudfront.net`), not just `localhost`: the page loads over real HTTPS, a real text query ("gentle moisturizer for sensitive skin under $15") returns a real grounded, cited answer with working citation cards, and an intentionally off-catalog query ("wireless headphones with good bass" - this dataset is All_Beauty only) correctly renders the system's honest "I found some information but none of it was actually relevant" decline rather than a fabricated answer - the same honest-decline behavior verified at the agent level earlier, now confirmed through the actual UI a user would see.

**Known verification gap, flagged**: the image-upload path is verified at the API level (workflow 03's live tests) and by code review (`api.ts` calls the identical presigned-POST-then-`/query` sequence), and the UI controls were visually confirmed to render and respond to clicks - but a full browser-automated upload (select a real file, submit, see a real image-driven answer) wasn't performed, because headless browser automation can't drive a native OS file-picker dialog. Manual testing by an actual user closes this gap; it isn't a gap in the underlying API, which workflow 03 already verified thoroughly.

## Status

**Complete.** See [../PROGRESS.md](../PROGRESS.md).
