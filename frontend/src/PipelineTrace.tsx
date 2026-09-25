import "./PipelineTrace.css";
import type { Trace } from "./api";

const SPECIALIST_LABELS: Record<string, string> = {
  search_agent: "Search",
  graph_agent: "Graph",
  lookup_agent: "Lookup",
  image_agent: "Image",
};

function Arrow() {
  return (
    <div className="trace-arrow" aria-hidden="true">
      →
    </div>
  );
}

export function PipelineTrace({ trace }: { trace: Trace }) {
  const specialistNames = Object.keys(trace.dispatch_decision);
  const dispatchedNames = specialistNames.filter((name) => trace.dispatch_decision[name]);
  const dropped = trace.citations ? trace.citations.drafted - trace.citations.verified : 0;

  return (
    <div className="trace-wrapper">
      <p className="trace-caption">How the system reached this answer:</p>
      <div className="trace">
        <div className="trace-stage">
          <div className="trace-stage-label">1. Specialists considered</div>
          <div className="trace-stage-hint">Which of the 4 retrieval specialists the router thought were relevant</div>
          <div className="trace-router-options">
            {specialistNames.map((name) => (
              <span key={name} className={trace.dispatch_decision[name] ? "trace-chip trace-chip-active" : "trace-chip"}>
                {SPECIALIST_LABELS[name] ?? name}
              </span>
            ))}
          </div>
        </div>

        {dispatchedNames.length > 0 && (
          <>
            <Arrow />
            <div className="trace-stage">
              <div className="trace-stage-label">2. Dispatched in parallel</div>
              <div className="trace-stage-hint">Results each specialist actually found</div>
              <div className="trace-specialists">
                {dispatchedNames.map((name) => {
                  const specialist = trace.specialists[name];
                  return (
                    <div key={name} className={specialist?.timed_out ? "trace-specialist trace-specialist-timeout" : "trace-specialist"}>
                      <div className="trace-specialist-name">{SPECIALIST_LABELS[name] ?? name}</div>
                      <div className="trace-specialist-count">
                        {specialist?.timed_out ? "timed out" : `${specialist?.result_count ?? 0} result${specialist?.result_count === 1 ? "" : "s"}`}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}

        {trace.consolidation && (
          <>
            <Arrow />
            <div className="trace-stage">
              <div className="trace-stage-label">3. Ranked &amp; deduplicated</div>
              <div className="trace-stage-hint">Candidates judged actually relevant to your question</div>
              <div className="trace-stat">
                {trace.consolidation.candidate_count} candidate{trace.consolidation.candidate_count === 1 ? "" : "s"}
                <span className="trace-stat-arrow">→</span>
                {trace.consolidation.ranked_count} relevant
              </div>
            </div>
          </>
        )}

        {trace.citations && (
          <>
            <Arrow />
            <div className="trace-stage">
              <div className="trace-stage-label">4. Citations fact-checked</div>
              <div className="trace-stage-hint">Claims in the answer, cross-checked against the actual product data</div>
              {trace.citations.drafted === 0 ? (
                <div className="trace-stat trace-stat-muted">No specific claims were cited in this answer</div>
              ) : (
                <div className="trace-stat">
                  {trace.citations.drafted} claim{trace.citations.drafted === 1 ? "" : "s"} cited
                  <span className="trace-stat-arrow">→</span>
                  {trace.citations.verified} confirmed grounded
                  {dropped > 0 && <span className="trace-stat-dropped"> ({dropped} dropped as unverifiable)</span>}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
