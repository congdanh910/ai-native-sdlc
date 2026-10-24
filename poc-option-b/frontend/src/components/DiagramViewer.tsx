import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { Download, ExternalLink, Maximize2, Minus, Plus, X } from "lucide-react";

const ZOOM_STEP = 1.25;
const MIN_SCALE = 0.05;
const MAX_SCALE = 8;

function svgUrl(svg: string): string {
  return URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
}

/** Opens the diagram as a plain SVG in a new tab, where the browser zooms and prints it. */
function openSvg(svg: string) {
  const url = svgUrl(svg);
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function downloadSvg(svg: string, name: string) {
  const url = svgUrl(svg);
  const link = Object.assign(document.createElement("a"), { href: url, download: `${name}.svg` });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

const toolButton = "inline-flex size-7 items-center justify-center rounded-md border border-line bg-panel text-ink-2 hover:bg-sunken";

/** The tools over a drawn diagram: full screen, open the SVG in a tab, download it. */
export function DiagramTools({ svg, name, onExpand }: { svg: string; name: string; onExpand: () => void }) {
  return (
    <div className="absolute right-2 top-2 flex gap-1 opacity-70 transition-opacity hover:opacity-100">
      <button type="button" className={toolButton} title="Full screen: zoom with the wheel, drag to move" aria-label="Full screen" onClick={onExpand}><Maximize2 className="size-3.5" /></button>
      <button type="button" className={toolButton} title="Open the SVG in a new tab" aria-label="Open SVG" onClick={() => openSvg(svg)}><ExternalLink className="size-3.5" /></button>
      <button type="button" className={toolButton} title="Download the SVG" aria-label="Download SVG" onClick={() => downloadSvg(svg, name)}><Download className="size-3.5" /></button>
    </div>
  );
}

/** The diagram over the whole window at its own size: wheel to zoom at the pointer, drag to move, Fit and 100%. */
export function DiagramViewer({ svg, name, onClose }: { svg: string; name: string; onClose: () => void }) {
  const frame = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const size = useRef({ width: 0, height: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);

  const fit = useCallback(() => {
    const box = frame.current?.getBoundingClientRect();
    const { width, height } = size.current;
    if (!box || !width || !height) return;
    const scale = Math.min(box.width / width, box.height / height) * 0.95;
    setView({ scale, x: (box.width - width * scale) / 2, y: (box.height - height * scale) / 2 });
  }, []);

  // Mermaid draws the SVG to shrink into its container; here it gets its own size, then fits the window.
  useEffect(() => {
    const element = content.current?.querySelector("svg");
    if (!element) return;
    const box = element.viewBox.baseVal;
    const width = box?.width || element.getBoundingClientRect().width;
    const height = box?.height || element.getBoundingClientRect().height;
    element.setAttribute("width", String(width));
    element.setAttribute("height", String(height));
    element.style.maxWidth = "none";
    size.current = { width, height };
    fit();
  }, [svg, fit]);

  const zoomAt = useCallback((factor: number, px?: number, py?: number) => {
    setView((v) => {
      const box = frame.current?.getBoundingClientRect();
      const cx = px ?? (box ? box.width / 2 : 0), cy = py ?? (box ? box.height / 2 : 0);
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor));
      const ratio = scale / v.scale;
      return { scale, x: cx - (cx - v.x) * ratio, y: cy - (cy - v.y) * ratio };
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "+" || e.key === "=") zoomAt(ZOOM_STEP);
      else if (e.key === "-") zoomAt(1 / ZOOM_STEP);
      else if (e.key === "0") fit();
    };
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = overflow; };
  }, [onClose, zoomAt, fit]);

  // A non-passive listener, so the wheel zooms the diagram instead of scrolling the page.
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const box = element.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - box.left, e.clientY - box.top);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const down = (e: ReactPointerEvent) => { drag.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); };
  const move = (e: ReactPointerEvent) => {
    if (!drag.current) return;
    const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y;
    drag.current = { x: e.clientX, y: e.clientY };
    setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
  };
  const up = () => { drag.current = null; };
  const actual = () => {
    const box = frame.current?.getBoundingClientRect();
    const { width, height } = size.current;
    setView({ scale: 1, x: box ? (box.width - width) / 2 : 0, y: box ? Math.max(0, (box.height - height) / 2) : 0 });
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-bg/95 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Diagram">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line bg-panel px-3 py-2 text-sm">
        <button type="button" className={toolButton} title="Zoom out (-)" aria-label="Zoom out" onClick={() => zoomAt(1 / ZOOM_STEP)}><Minus className="size-3.5" /></button>
        <span className="w-14 text-center font-mono text-xs text-muted">{Math.round(view.scale * 100)}%</span>
        <button type="button" className={toolButton} title="Zoom in (+)" aria-label="Zoom in" onClick={() => zoomAt(ZOOM_STEP)}><Plus className="size-3.5" /></button>
        <button type="button" className="rounded-md border border-line bg-panel px-2 py-1 text-xs hover:bg-sunken" title="Fit the window (0)" onClick={fit}>Fit</button>
        <button type="button" className="rounded-md border border-line bg-panel px-2 py-1 text-xs hover:bg-sunken" title="Actual size" onClick={actual}>100%</button>
        <span className="ml-2 hidden text-xs text-muted sm:inline">Wheel to zoom, drag to move, Esc to close</span>
        <div className="ml-auto flex gap-1.5">
          <button type="button" className={toolButton} title="Open the SVG in a new tab" aria-label="Open SVG" onClick={() => openSvg(svg)}><ExternalLink className="size-3.5" /></button>
          <button type="button" className={toolButton} title="Download the SVG" aria-label="Download SVG" onClick={() => downloadSvg(svg, name)}><Download className="size-3.5" /></button>
          <button type="button" className={toolButton} title="Close (Esc)" aria-label="Close" onClick={onClose}><X className="size-4" /></button>
        </div>
      </div>
      <div ref={frame} className="relative flex-1 cursor-grab overflow-hidden active:cursor-grabbing"
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onDoubleClick={() => zoomAt(ZOOM_STEP * ZOOM_STEP)}>
        <div ref={content} className="absolute left-0 top-0 origin-top-left select-none rounded-lg bg-panel p-0"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}
          dangerouslySetInnerHTML={{ __html: svg }} />
      </div>
    </div>,
    document.body,
  );
}
