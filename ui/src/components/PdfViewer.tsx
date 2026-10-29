import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { SyncBox } from "../api";
import { IconFitWidth, IconMinus, IconPlus, IconSync } from "../icons";

GlobalWorkerOptions.workerSrc = workerUrl;

const ZOOMS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
const GAP = 14;

interface Size {
  w: number;
  h: number;
}

interface Props {
  title: string;
  data: ArrayBuffer | null;
  /** Changes with each new PDF; the viewer keeps its place from one version to the next. */
  version: string;
  highlight: SyncBox[] | null;
  onDoubleClick?: (page: number, x: number, y: number) => void;
  onSyncFromCursor?: () => void;
  extraHead?: ReactNode;
  /** Shown when there is no PDF. */
  placeholder?: string;
}

interface PageProps {
  doc: PDFDocumentProxy;
  index: number;
  size: Size;
  scale: number;
  visible: boolean;
  boxes: SyncBox[];
  onDoubleClick?: (page: number, x: number, y: number) => void;
}

function Page({ doc, index, size, scale, visible, boxes, onDoubleClick }: PageProps) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const target = canvas.current;
    if (!target) return;
    if (!visible) {
      // Far off screen: free the memory. Nothing visible changes.
      target.width = 0;
      target.height = 0;
      return;
    }
    let cancelled = false;
    let task: RenderTask | null = null;
    // Draw off screen, then copy in one step, so the page never shows blank while it renders.
    doc.getPage(index + 1).then((page) => {
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const ratio = window.devicePixelRatio || 1;
      const off = document.createElement("canvas");
      off.width = Math.floor(viewport.width * ratio);
      off.height = Math.floor(viewport.height * ratio);
      task = page.render({
        canvasContext: off.getContext("2d")!,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      });
      task.promise.then(
        () => {
          if (cancelled) return;
          target.width = off.width;
          target.height = off.height;
          target.style.width = `${viewport.width}px`;
          target.style.height = `${viewport.height}px`;
          target.getContext("2d")!.drawImage(off, 0, 0);
        },
        () => undefined,
      );
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, index, scale, visible]);

  return (
    <div
      className="pdf-page"
      data-page={index + 1}
      style={{ width: size.w * scale, height: size.h * scale }}
      onDoubleClick={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        onDoubleClick?.(index + 1, (e.clientX - rect.left) / scale, (e.clientY - rect.top) / scale);
      }}
    >
      <canvas ref={canvas} />
      {boxes.map((b, i) => (
        <div
          key={i}
          className="sync-box"
          style={{ left: b.left * scale, top: b.top * scale, width: b.width * scale, height: b.height * scale }}
        />
      ))}
    </div>
  );
}

export function PdfViewer({ title, data, version, highlight, onDoubleClick, onSyncFromCursor, extraHead, placeholder }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<Size[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [width, setWidth] = useState(600);
  const [visible, setVisible] = useState<Set<number>>(new Set([0]));
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  // Where the reader was, kept across a new version: page and the fraction of it scrolled past.
  const place = useRef<{ page: number; within: number } | null>(null);

  const maxWidth = sizes.length ? Math.max(...sizes.map((s) => s.w)) : 595;
  const scale = zoom === "fit" ? Math.max(0.3, (width - 2 * GAP - 12) / maxWidth) : zoom;

  // Measured before the first paint, so the pages never show at a default size first.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const pageElement = (n: number) => scroller.current?.querySelector<HTMLElement>(`[data-page="${n}"]`) ?? null;

  const remember = useCallback(() => {
    const el = scroller.current;
    const current = pageElement(page);
    if (el && current) {
      place.current = { page, within: (el.scrollTop - current.offsetTop) / current.offsetHeight };
    }
  }, [page]);

  // Load each new PDF; the bytes are copied because PDF.js takes ownership of what it is given.
  useEffect(() => {
    if (!data) {
      setDoc(null);
      setSizes([]);
      return;
    }
    remember();
    let cancelled = false;
    const task = getDocument({ data: new Uint8Array(data.slice(0)) });
    task.promise.then(
      async (loaded) => {
        const found: Size[] = [];
        for (let i = 1; i <= loaded.numPages; i++) {
          const viewport = (await loaded.getPage(i)).getViewport({ scale: 1 });
          found.push({ w: viewport.width, h: viewport.height });
        }
        if (cancelled) {
          void loaded.destroy();
          return;
        }
        setError(null);
        setSizes(found);
        setDoc((old) => {
          void old?.destroy();
          return loaded;
        });
      },
      (e: Error) => !cancelled && setError(`This PDF could not be shown: ${e.message}`),
    );
    return () => {
      cancelled = true;
    };
    // remember() reads the current page; it must not reload the PDF when the page changes.
  }, [data, version]);

  // Put the reader back where they were once the new pages have their sizes.
  useLayoutEffect(() => {
    const el = scroller.current;
    const saved = place.current;
    if (!el || !saved || !doc) return;
    const target = pageElement(Math.min(saved.page, sizes.length));
    if (target) el.scrollTop = target.offsetTop + saved.within * target.offsetHeight;
    place.current = null;
  }, [doc, sizes]);

  // Render only pages near the screen.
  useEffect(() => {
    const el = scroller.current;
    if (!el || !doc) return;
    const observer = new IntersectionObserver(
      (entries) =>
        setVisible((prev) => {
          const next = new Set(prev);
          for (const entry of entries) {
            const n = Number((entry.target as HTMLElement).dataset.page) - 1;
            if (entry.isIntersecting) next.add(n);
            else next.delete(n);
          }
          return next;
        }),
      { root: el, rootMargin: "150% 0px" },
    );
    el.querySelectorAll(".pdf-page").forEach((p) => observer.observe(p));
    return () => observer.disconnect();
  }, [doc, sizes, scale]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const middle = el.scrollTop + el.clientHeight / 3;
    let current = 1;
    el.querySelectorAll<HTMLElement>(".pdf-page").forEach((p) => {
      if (p.offsetTop <= middle) current = Number(p.dataset.page);
    });
    if (current !== page) {
      setPage(current);
      setPageInput(String(current));
    }
  };

  const goToPage = (n: number) => {
    const target = pageElement(Math.min(Math.max(1, n), sizes.length));
    if (target && scroller.current) scroller.current.scrollTop = target.offsetTop - GAP;
  };

  // Show a SyncTeX result: the first box a third of the way down the view.
  useEffect(() => {
    if (!highlight?.length || !scroller.current) return;
    const first = highlight[0];
    const target = pageElement(first.page);
    if (target) scroller.current.scrollTop = target.offsetTop + first.top * scale - scroller.current.clientHeight / 3;
  }, [highlight, doc]);

  const zoomBy = (step: 1 | -1) => {
    remember();
    const index = ZOOMS.findIndex((z) => z >= scale - 0.001);
    const from = index < 0 ? ZOOMS.length - 1 : index;
    setZoom(ZOOMS[Math.min(ZOOMS.length - 1, Math.max(0, from + step))]);
  };

  // Keep the place when the scale changes.
  useLayoutEffect(() => {
    const el = scroller.current;
    const saved = place.current;
    if (!el || !saved) return;
    const target = pageElement(saved.page);
    if (target) el.scrollTop = target.offsetTop + saved.within * target.offsetHeight;
    place.current = null;
  }, [scale]);

  return (
    <div className="pane">
      <div className="pane-head">
        <span className="pane-title">{title}</span>
        {extraHead}
        <span className="spacer" />
        {sizes.length > 0 && (
          <>
            <button className="icon" onClick={() => goToPage(page - 1)} title="Previous page" aria-label="Previous page">
              ‹
            </button>
            <input
              className="page-input"
              value={pageInput}
              aria-label="Page"
              onChange={(e) => setPageInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && goToPage(Number(pageInput) || 1)}
            />
            <span className="muted small">of {sizes.length}</span>
            <button className="icon" onClick={() => goToPage(page + 1)} title="Next page" aria-label="Next page">
              ›
            </button>
            <button className="icon" onClick={() => zoomBy(-1)} title="Zoom out" aria-label="Zoom out">
              <IconMinus />
            </button>
            <span className="small" style={{ minWidth: 38, textAlign: "center" }}>
              {Math.round(scale * 100)}%
            </span>
            <button className="icon" onClick={() => zoomBy(1)} title="Zoom in" aria-label="Zoom in">
              <IconPlus />
            </button>
            <button
              className={`icon ${zoom === "fit" ? "on" : ""}`}
              onClick={() => {
                remember();
                setZoom("fit");
              }}
              title="Fit width"
              aria-label="Fit width"
            >
              <IconFitWidth />
            </button>
          </>
        )}
        {onSyncFromCursor && (
          <button className="icon" onClick={onSyncFromCursor} title="Show the cursor's place in the PDF (⌘J)">
            <IconSync />
          </button>
        )}
      </div>
      <div className="pane-body">
        <div className="pdf-scroll" ref={scroller} onScroll={onScroll}>
          {error && <div className="empty">{error}</div>}
          {!error && !doc && <div className="empty">{data ? "Opening the PDF..." : (placeholder ?? "Compile to see the PDF.")}</div>}
          {doc &&
            sizes.map((size, i) => (
              <Page
                key={i}
                doc={doc}
                index={i}
                size={size}
                scale={scale}
                visible={visible.has(i)}
                boxes={(highlight ?? []).filter((b) => b.page === i + 1)}
                onDoubleClick={onDoubleClick}
              />
            ))}
        </div>
      </div>
    </div>
  );
}
