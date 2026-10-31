import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

interface Props {
  /** "x" is a vertical bar dragged sideways; "y" a horizontal bar dragged up and down. */
  axis: "x" | "y";
  /** Called once, on release, with how far the bar moved; nothing moves until then. */
  onMoved: (delta: number) => void;
  label: string;
  style?: React.CSSProperties;
}

const STEP = 24;

/** A drag handle. While dragging, a still line follows the pointer and the panes stay as they are, so the PDF
 * does not redraw over and over. They take the new size when the pointer is released. */
export function Splitter({ axis, onMoved, label, style }: Props) {
  const start = useRef(0);
  const [guide, setGuide] = useState<number | null>(null);
  const at = (e: PointerEvent) => (axis === "x" ? e.clientX : e.clientY);

  const onKey = (e: KeyboardEvent) => {
    const back = axis === "x" ? "ArrowLeft" : "ArrowUp";
    const forward = axis === "x" ? "ArrowRight" : "ArrowDown";
    if (e.key === back || e.key === forward) {
      e.preventDefault();
      onMoved(e.key === back ? -STEP : STEP);
    }
  };

  return (
    <>
      <div
        className={`splitter ${axis}`}
        style={style}
        role="separator"
        aria-orientation={axis === "x" ? "vertical" : "horizontal"}
        aria-label={label}
        title={label}
        tabIndex={0}
        onKeyDown={onKey}
        onPointerDown={(e) => {
          e.preventDefault();
          e.currentTarget.setPointerCapture(e.pointerId);
          start.current = at(e);
          setGuide(at(e));
        }}
        onPointerMove={(e) => {
          if (guide !== null) setGuide(at(e));
        }}
        onPointerUp={(e) => {
          if (guide === null) return;
          setGuide(null);
          const delta = at(e) - start.current;
          if (delta !== 0) onMoved(delta);
        }}
        onPointerCancel={() => setGuide(null)}
      />
      {guide !== null && <div className={`splitter-guide ${axis}`} style={axis === "x" ? { left: guide } : { top: guide }} />}
    </>
  );
}
