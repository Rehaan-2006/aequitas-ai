const OUTCOME_LABELS = {
  answered: 'Answered',
  abstain: 'Abstained',
  rejected_at_sanitization: 'Rejected (sanitization)',
  no_retrieval_results: 'No results found',
}

export default function ResultView({ result }) {
  if (!result) return null

  return (
    <div style={{ border: '1px solid #ccc', padding: 16, marginTop: 16 }}>
      <h3>
        Outcome: {OUTCOME_LABELS[result.outcome] || result.outcome}{' '}
        {result.outcome === 'answered' && (
          <span style={{ color: result.citations_verified ? 'green' : '#b45309' }}>
            [{result.citations_verified ? 'citations verified' : 'citations NOT verified'}]
          </span>
        )}
      </h3>

      {result.rejection_reason && <p><strong>Reason:</strong> {result.rejection_reason}</p>}
      {result.message && <p><strong>Message:</strong> {result.message}</p>}
      {result.abstain_reason && <p><strong>Abstain reason:</strong> {result.abstain_reason}</p>}

      {result.outcome === 'answered' && (
        <div>
          <p><strong>Issue:</strong> {result.issue}</p>
          <p><strong>Rule:</strong> {result.rule}</p>
          <p><strong>Application:</strong> {result.application}</p>
          <p><strong>Conclusion:</strong> {result.conclusion}</p>

          {result.citations?.length > 0 && (
            <div>
              <strong>Citations:</strong>
              <ul>
                {result.citations.map((c) => (
                  <li key={c.marker}>
                    [{c.marker}] {c.case_name} — {c.citation}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {result.trace?.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary>Pipeline trace ({result.trace.length} stages)</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, background: '#f5f5f5', padding: 8 }}>
            {JSON.stringify(result.trace, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}
