import { useState } from "react";
import { figureLatex, tableLatex } from "../editor/insert";
import { Modal } from "./Modal";

const MAX_SIZE = 20;

export function TableDialog(props: { booktabs: boolean; onInsert: (latex: string) => void; onClose: () => void }) {
  const [rows, setRows] = useState(3);
  const [columns, setColumns] = useState(3);
  const [cells, setCells] = useState<string[][]>([]);
  const [caption, setCaption] = useState("");
  const [label, setLabel] = useState("");
  const [booktabs, setBooktabs] = useState(props.booktabs);
  const cell = (r: number, c: number) => cells[r]?.[c] ?? "";
  const setCell = (r: number, c: number, value: string) =>
    setCells((old) => {
      const next = old.map((row) => [...row]);
      while (next.length <= r) next.push([]);
      next[r][c] = value;
      return next;
    });
  const grid = Array.from({ length: rows }, (_, r) => Array.from({ length: columns }, (_, c) => cell(r, c)));
  const size = (value: string) => Math.min(MAX_SIZE, Math.max(1, Number(value) || 1));
  return (
    <Modal title="Insert a table" onClose={props.onClose} wide>
      <div className="row">
        <label className="small">
          Rows <input type="number" min={1} max={MAX_SIZE} value={rows} onChange={(e) => setRows(size(e.target.value))} />
        </label>
        <label className="small">
          Columns <input type="number" min={1} max={MAX_SIZE} value={columns} onChange={(e) => setColumns(size(e.target.value))} />
        </label>
        <label className="check small" title={props.booktabs ? "" : "The paper does not load booktabs yet. Add \\usepackage{booktabs} to its preamble if you use this."}>
          <input type="checkbox" checked={booktabs} onChange={(e) => setBooktabs(e.target.checked)} />
          Rules from booktabs{props.booktabs ? "" : " (not loaded by this paper)"}
        </label>
      </div>
      <table className="table-grid">
        <tbody>
          {grid.map((row, r) => (
            <tr key={r}>
              {row.map((value, c) => (
                <td key={c}>
                  <input aria-label={`Row ${r + 1}, column ${c + 1}`} value={value} onChange={(e) => setCell(r, c, e.target.value)} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted small">The first row is the header. Math such as $x^2$ works in a cell.</div>
      <label className="small block">
        Caption <input value={caption} onChange={(e) => setCaption(e.target.value)} />
      </label>
      <label className="small block">
        Label <input value={label} placeholder="tab:runs" onChange={(e) => setLabel(e.target.value)} />
      </label>
      <div className="modal-foot">
        <button className="primary" onClick={() => props.onInsert(tableLatex({ cells: grid, caption, label, booktabs }))}>
          Insert
        </button>
      </div>
    </Modal>
  );
}

export function FigureDialog(props: { images: string[]; macro: string; previewUrl: (name: string) => string; onInsert: (latex: string) => void; onClose: () => void }) {
  const [image, setImage] = useState(props.images[0] ?? "");
  const [width, setWidth] = useState(1);
  const [caption, setCaption] = useState("");
  const [label, setLabel] = useState("");
  return (
    <Modal title="Insert a figure" onClose={props.onClose} wide>
      {props.images.length === 0 ? (
        <p>This paper has no image files yet. Add one to its folder first.</p>
      ) : (
        <>
          <label className="small block">
            Image
            <select value={image} onChange={(e) => setImage(e.target.value)}>
              {props.images.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          {image && <img className="figure-choice" alt={image} src={props.previewUrl(image)} />}
          <label className="small block">
            Width, as a share of the line{" "}
            <input type="number" min={0.1} max={1} step={0.05} value={width} onChange={(e) => setWidth(Math.min(1, Math.max(0.1, Number(e.target.value) || 1)))} />
          </label>
          <label className="small block">
            Caption <input value={caption} onChange={(e) => setCaption(e.target.value)} />
          </label>
          <label className="small block">
            Label <input value={label} placeholder="fig:results" onChange={(e) => setLabel(e.target.value)} />
          </label>
          <div className="modal-foot">
            <button className="primary" onClick={() => props.onInsert(figureLatex({ image, width, caption, label, macro: props.macro }))}>
              Insert
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
