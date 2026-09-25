# Frontend

Static SPA for the multimodal product assistant - see [docs/designs/06-frontend-hosting.md](../docs/designs/06-frontend-hosting.md). Calls the query/upload API built in [workflow 03](../docs/designs/03-image-upload.md) directly from the browser.

## Local development

```bash
npm install
npm run dev
```

`.env.development`/`.env.production` set `VITE_API_URL` to the deployed `RagEcommerce-Upload` API Gateway URL - committed even though the repo otherwise ignores `.env*` files, since this value is a public API endpoint, not a secret (the same URL is visible in the built JS bundle regardless).

## Deploy

Built locally, then uploaded by CDK - this app has no build step of its own in `infra/stacks/frontend_stack.py`:

```bash
npm run build
cd ../infra && npx cdk deploy RagEcommerce-Frontend
```

`BucketDeployment` uploads `dist/` to a private S3 bucket behind CloudFront (Origin Access Control, no public bucket access) and invalidates the distribution's cache on every deploy.
