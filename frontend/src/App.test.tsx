import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import App from './App';

afterEach(() => vi.unstubAllGlobals());

describe('local support UI (synthetic HTTP)', () => {
  it('shows safe empty state and fills example questions', () => {
    render(<App />);
    expect(screen.getByRole('button', { name: /Find an answer/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /factory reset/ }));
    expect(screen.getByLabelText('Your question')).toHaveValue('How do I factory reset LumaDock Home?');
  });
  it('renders abstention and diagnostics', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ answer: 'I cannot answer that from the available documentation.', abstained: true, citations: [], passages: [], diagnostics: { reason: 'conflicting_sources', algorithm: 'RRF(k=60)', generation_called: false, embedding_model: 'nomic-embed-text', generation_model: 'qwen3:4b' } }) }));
    render(<App />);
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'warranty' } });
    fireEvent.click(screen.getByRole('button', { name: /Find an answer/ }));
    expect(await screen.findByText('ABSTAINED')).toBeInTheDocument();
    expect(screen.getByText(/multiple active sources/)).toBeInTheDocument();
    expect(screen.getByText(/Retrieval diagnostics/)).toBeInTheDocument();
  });
  it('shows an actionable offline error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Local service offline')));
    render(<App />);
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'reset' } });
    fireEvent.click(screen.getByRole('button', { name: /Find an answer/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Local service offline');
  });
});
