import { Fragment, useRef, useState } from "react";
import "./App.css";
import { submitQuery, type QueryResponse } from "./api";

const ACCEPTED_IMAGE_TYPES = "image/jpeg,image/png,image/webp";

// The generator occasionally writes **bold** markdown around a product
// name (see src/agents/answer_generation.py's GENERATOR_INSTRUCTION,
// which asks for plain text but doesn't forbid it) - render just that one
// pattern rather than pulling in a full markdown parser for it.
function renderWithBold(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>
  );
}

function App() {
  const [prompt, setPrompt] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function handleImageChange(file: File | null) {
    setImage(file);
    setImagePreviewUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return file ? URL.createObjectURL(file) : null;
    });
  }

  function clearImage() {
    handleImageChange(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!prompt.trim() && !image) return;

    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await submitQuery(prompt.trim(), image);
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <header>
        <h1>Multimodal Product Assistant</h1>
        <p className="subtitle">Ask about products in text, upload a photo, or both — a multi-agent RAG system over a real e-commerce catalog.</p>
      </header>

      <form className="query-form" onSubmit={handleSubmit}>
        <div className="input-row">
          {imagePreviewUrl ? (
            <div className="image-preview">
              <img src={imagePreviewUrl} alt="Upload preview" />
              <button type="button" onClick={clearImage} aria-label="Remove photo">
                &times;
              </button>
            </div>
          ) : (
            <label className="file-picker" aria-label="Add a photo">
              <span aria-hidden="true">+</span>
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_IMAGE_TYPES}
                onChange={(event) => handleImageChange(event.target.files?.[0] ?? null)}
              />
            </label>
          )}
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="e.g. gentle moisturizer for sensitive skin under $15"
            rows={2}
          />
        </div>

        <button type="submit" disabled={loading || (!prompt.trim() && !image)} className="submit-button">
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {result && (
        <section className="result">
          <p className="answer">{renderWithBold(result.answer)}</p>

          {result.dispatched.length > 0 && (
            <p className="dispatched">Consulted: {result.dispatched.join(", ")}</p>
          )}

          {result.citations.length > 0 && (
            <div className="citations">
              {result.citations.map((citation) => (
                <a key={citation.product_id} className="citation-card" href={citation.product_url} target="_blank" rel="noreferrer">
                  {citation.image_url && <img src={citation.image_url} alt={citation.title} />}
                  <div className="citation-body">
                    <h3>{citation.title}</h3>
                    <p>{renderWithBold(citation.snippet)}</p>
                  </div>
                </a>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

export default App;
