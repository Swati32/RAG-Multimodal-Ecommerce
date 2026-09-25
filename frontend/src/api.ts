// Client for the query/upload API built in workflow 03 (see
// docs/designs/03-image-upload.md) - a presigned S3 upload, then a single
// call to the router via the /query endpoint (see src/api/submit_query.py
// for why this collapses the router's SSE stream into one JSON response).

const API_URL = import.meta.env.VITE_API_URL as string;

export interface Review {
  rating: number;
  text: string;
}

export interface Citation {
  product_id: string;
  title: string;
  brand: string | null;
  image_url: string | null;
  product_url: string;
  snippet: string;
  rank_reason: string;
  reviews: Review[];
}

export interface SpecialistTrace {
  result_count: number;
  timed_out: boolean;
}

export interface Trace {
  dispatch_decision: Record<string, boolean>;
  specialists: Record<string, SpecialistTrace>;
  consolidation: { candidate_count: number; ranked_count: number } | null;
  citations: { drafted: number; verified: number } | null;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  dispatched: string[];
  trace: Trace | null;
}

interface PresignedPost {
  url: string;
  fields: Record<string, string>;
  key: string;
}

async function presignUpload(contentType: string): Promise<PresignedPost> {
  const response = await fetch(`${API_URL}/upload-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content_type: contentType }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Upload not allowed (${response.status})`);
  }
  return response.json();
}

async function uploadToS3(presigned: PresignedPost, file: File): Promise<void> {
  const form = new FormData();
  for (const [key, value] of Object.entries(presigned.fields)) {
    form.append(key, value);
  }
  form.append("file", file);
  const response = await fetch(presigned.url, { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(`Image upload failed (${response.status}) - check the file type and size (max 5MB, jpg/png/webp).`);
  }
}

export async function submitQuery(prompt: string, image: File | null): Promise<QueryResponse> {
  let objectKey: string | undefined;
  if (image) {
    const presigned = await presignUpload(image.type);
    await uploadToS3(presigned, image);
    objectKey = presigned.key;
  }

  const response = await fetch(`${API_URL}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, object_key: objectKey }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Query failed (${response.status})`);
  }
  return response.json();
}
