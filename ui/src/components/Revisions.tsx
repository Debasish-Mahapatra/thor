import type { Revision } from "../api";

export function Revisions({ revisions }: { revisions: Revision[] }) {
  return (
    <section className="card">
      <h2>Revisions</h2>
      {revisions.length === 0 ? (
        <p className="muted">No revision folders yet.</p>
      ) : (
        <table className="revisions">
          <thead>
            <tr>
              <th>Revision</th>
              <th>Folder</th>
              <th>PDFs</th>
              <th>Deliverables</th>
            </tr>
          </thead>
          <tbody>
            {[...revisions].reverse().map((r) => (
              <tr key={r.path}>
                <td className="num">{r.number}</td>
                <td className="mono small">{r.path}/</td>
                <td className="small">{r.pdfs.join(", ") || "none"}</td>
                <td className="small">
                  {r.missing.length === 0 ? (
                    <span className="good-text">Complete</span>
                  ) : (
                    <span className="warn-text">Missing {r.missing.join(", ")}</span>
                  )}
                  {!r.supporting_files && <span className="muted"> · no supporting_files/</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
