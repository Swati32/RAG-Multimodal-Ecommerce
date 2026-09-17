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

## Status

Not started — see [../PROGRESS.md](../PROGRESS.md)
