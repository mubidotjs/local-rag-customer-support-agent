import { useRef, useState } from 'react';
import type { FormEvent } from 'react';

type Citation = { chunk_id: string; document_id: string; title: string; version: string; quote: string; normalized_start: number; normalized_end: number };
type Passage = { id: string; title: string; version: string; text: string; source_start: number; source_end: number; normalized_start: number; normalized_end: number; similarity: number; semantic_rank: number | null; keyword_rank: number | null; rrf_score: number };
export type Answer = { answer: string; abstained: boolean; citations: Citation[]; passages: Passage[]; diagnostics: { reason: string; algorithm: string; generation_called: boolean; embedding_model: string; generation_model: string } };

const examples = ['How do I factory reset LumaDock Home?', 'Are dual external displays supported?', 'How long is the LumaDock Home warranty?'];

export default function App() {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<Answer | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current || question.trim().length < 3) return;
    inFlight.current = true;
    setPending(true); setError(''); setResult(null);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 195_000);
    try {
      const response = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim() }), signal: controller.signal });
      if (!response.ok) throw new Error(response.status === 429 ? 'The local model is busy. Try again shortly.' : 'The local service is unavailable. Check Ollama and run document ingestion.');
      setResult(await response.json() as Answer);
    } catch (e) {
      setError(e instanceof Error && e.name === 'AbortError' ? 'The local request timed out. Try again once the model is ready.' : e instanceof Error ? e.message : 'The request failed.');
    } finally { clearTimeout(timeout); inFlight.current = false; setPending(false); }
  }

  return <div className="shell">
    <header><a className="brand" href="/" aria-label="Luma local support home"><span className="brand-icon">l</span> luma<span className="brand-divider">/</span><span className="brand-sub">support lab</span></a><span className="local-badge"><i /> LOCAL INFERENCE</span></header>
    <main>
      <section className="intro"><p className="eyebrow">LUMADOCK DOCUMENTATION · FICTIONAL PRODUCT</p><h1>A little clarity.<br /><span>Straight from the source.</span></h1><p className="lede">Ask about your LumaDock. Every answer comes with the exact words behind it. If the docs don’t know, we’ll say so.</p></section>
      <div className="workspace"><section className="question-panel" aria-label="Ask documentation">
        <div className="panel-heading"><span className="step">01</span><h2>What can we help with?</h2></div>
        <form onSubmit={submit}><label htmlFor="question">Your question</label><textarea id="question" placeholder="e.g. How do I reset my dock?" maxLength={600} minLength={3} value={question} onChange={e => setQuestion(e.target.value)} disabled={pending} required /><div className="form-bottom"><span>{question.length}/600</span><button disabled={pending || question.trim().length < 3} type="submit">{pending ? 'Reading the docs…' : 'Find an answer'} <span aria-hidden="true">↗</span></button></div></form>
        <div className="examples"><p>TRY A QUESTION</p>{examples.map(q => <button key={q} onClick={() => setQuestion(q)} disabled={pending}>{q}<span aria-hidden="true">↗</span></button>)}</div>
        <div className="privacy"><span aria-hidden="true">◈</span><p>Your questions stay on this computer.<br />No cloud inference. No external actions.</p></div>
      </section>
      <section className="answer-panel" aria-label="Answer and evidence" aria-live="polite" aria-busy={pending}>
        <div className="panel-heading"><span className="step">02</span><h2>Answer & evidence</h2>{result && <span className={`answer-state ${result.abstained ? 'uncertain' : ''}`}>{result.abstained ? 'ABSTAINED' : 'SOURCE VERIFIED'}</span>}</div>
        {pending && <div className="empty-state"><div className="pulse" aria-hidden="true">⌕</div><h3>Looking for supporting evidence</h3><p>Searching locally, then checking the answer against the retrieved passages. The first request may take a moment.</p></div>}
        {error && <div role="alert" className="error">{error}</div>}
        {!pending && !result && !error && <div className="empty-state"><div className="empty-icon" aria-hidden="true">⌕</div><h3>Good answers leave a trail.</h3><p>Ask a question to see an answer, its sources, and how the evidence was found.</p><span className="empty-caption">13 LOCAL DOCUMENTS · EXACT CITATIONS</span></div>}
        {result && <div className="result"><p className="answer-text">{result.answer}</p>{result.abstained && <p className="reason">{result.diagnostics.reason === 'conflicting_sources' ? 'The documentation contains multiple active sources for this topic. Clarification is needed.' : 'The retrieved evidence did not pass the answer checks. Try a more specific question.'}</p>}
          {result.citations.length > 0 && <div className="citations"><h3>Cited sources <span>{result.citations.length}</span></h3>{result.citations.map((c, i) => <article className="citation" key={`${c.chunk_id}-${i}`}><div className="citation-head"><b>{c.title}</b><span>v{c.version}</span></div><blockquote>{c.quote}</blockquote><code>{c.chunk_id} · chars {c.normalized_start}–{c.normalized_end}</code></article>)}</div>}
          <details className="diagnostics"><summary>Retrieval diagnostics <span>{result.passages.length} passages</span></summary><p>{result.diagnostics.algorithm}</p><p>Result: {result.diagnostics.reason} · Generation {result.diagnostics.generation_called ? 'called' : 'skipped'}</p><p>{result.diagnostics.embedding_model} / {result.diagnostics.generation_model}</p>{result.passages.map(p => <article className="passage" key={p.id}><b>{p.title} · v{p.version}</b><code>{p.id}</code><dl><div><dt>Cosine</dt><dd>{p.similarity.toFixed(4)}</dd></div><div><dt>Semantic rank</dt><dd>{p.semantic_rank ?? '—'}</dd></div><div><dt>Keyword rank</dt><dd>{p.keyword_rank ?? '—'}</dd></div><div><dt>RRF</dt><dd>{p.rrf_score.toFixed(6)}</dd></div></dl><p>{p.text}</p><small>Source chars {p.source_start}–{p.source_end}; normalized {p.normalized_start}–{p.normalized_end}</small></article>)}</details>
        </div>}
      </section></div>
      <footer><span>BUILT TO KNOW ITS LIMITS</span><p>Local models. Trusted documentation. Verifiable words.</p><span>PROJECT 05 / MVP</span></footer>
    </main>
  </div>;
}
