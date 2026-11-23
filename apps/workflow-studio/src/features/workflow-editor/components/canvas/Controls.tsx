"use client";

import { useReactFlow, useViewport } from "@xyflow/react";
import { Hand, Maximize, Minus, MousePointer2, Plus } from "lucide-react";
import { Button } from "@/shared/components/ui/button";
import { Kbd } from "@/shared/components/ui/kbd";
import { Tip } from "@/shared/components/ui/tooltip";
import { cn } from "@/shared/lib/utils";
import { useEditorLayoutStore } from "../../stores/editorLayoutStore";
import { DOCK_LABEL, useEditorInterface } from "../interface/useEditorInterface";

const Sep = () => <span className="mx-1 h-4 w-px self-center bg-hairline" aria-hidden />;

/**
 * The canvas toolbar (bottom centre): select / hand, add node, zoom out / level / zoom in, fit, and (when the
 * workflow has an interface) the button that opens the "Try it" dock: Chat / Open form / Open screen.
 * A glass overlay on the recessed canvas; the chosen tool is raised.
 */
export const Controls = ({ className }: { className?: string; showInteractive?: boolean }) => {
  const { zoomIn, zoomOut, zoomTo, fitView } = useReactFlow();
  const { zoom } = useViewport();
  const canvasMode = useEditorLayoutStore((s) => s.canvasMode);
  const setCanvasMode = useEditorLayoutStore((s) => s.setCanvasMode);
  const ensureRightPanelOpen = useEditorLayoutStore((s) => s.ensureRightPanelOpen);
  const dockOpen = useEditorLayoutStore((s) => s.interfaceDockOpen);
  const toggleDock = useEditorLayoutStore((s) => s.toggleInterfaceDock);
  const { iface } = useEditorInterface();
  const dock = iface ? DOCK_LABEL[iface.kind] : null;

  const tool = (active: boolean) => cn("text-muted-foreground hover:text-foreground", active && "bg-raise text-foreground shadow-raise-sm hover:bg-raise");

  return (
    <div
      role="toolbar"
      aria-label="Canvas tools"
      data-testid="canvas-toolbar"
      className={cn("glass-overlay absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 p-1", className)}
    >
      <Tip label="Select" side="top">
        <Button variant="ghost" size="icon-sm" className={tool(canvasMode === 'pointer')} aria-label="Select" aria-pressed={canvasMode === 'pointer'} onClick={() => setCanvasMode('pointer')} data-testid="canvas-tool-select">
          <MousePointer2 />
        </Button>
      </Tip>
      <Tip label="Hand (pan)" side="top">
        <Button variant="ghost" size="icon-sm" className={tool(canvasMode === 'hand')} aria-label="Hand" aria-pressed={canvasMode === 'hand'} onClick={() => setCanvasMode('hand')} data-testid="canvas-tool-hand">
          <Hand />
        </Button>
      </Tip>
      <Sep />
      <Tip label="Add a node" kbd="N" side="top">
        <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground" onClick={() => ensureRightPanelOpen('nodes')} data-testid="canvas-add-node">
          <Plus /> Add node <Kbd>N</Kbd>
        </Button>
      </Tip>
      <Sep />
      <Tip label="Zoom out" side="top">
        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" aria-label="Zoom out" onClick={() => zoomOut()} data-testid="canvas-zoom-out">
          <Minus />
        </Button>
      </Tip>
      <Tip label="Reset to 100%" side="top">
        <button
          type="button"
          onClick={() => zoomTo(1)}
          className="h-7 min-w-12 rounded-ctl px-1 text-[12px] font-medium text-foreground tabular-nums outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
          data-testid="canvas-zoom-level"
        >
          {Math.round(zoom * 100)}%
        </button>
      </Tip>
      <Tip label="Zoom in" side="top">
        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" aria-label="Zoom in" onClick={() => zoomIn()} data-testid="canvas-zoom-in">
          <Plus />
        </Button>
      </Tip>
      <Tip label="Fit to view" kbd="F" side="top">
        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" aria-label="Fit to view" onClick={() => fitView({ padding: 0.2, maxZoom: 1, duration: 200 })} data-testid="canvas-fit">
          <Maximize />
        </Button>
      </Tip>
      {iface && dock && (
        <>
          <Sep />
          <Tip label={dockOpen ? "Close the interface" : "Use the interface here and watch it run"} side="top">
            <Button
              variant="ghost"
              size="sm"
              className={tool(dockOpen)}
              aria-pressed={dockOpen}
              onClick={toggleDock}
              data-testid="canvas-try-interface"
              data-kind={iface.kind}
            >
              <dock.icon /> {dock.label}
            </Button>
          </Tip>
        </>
      )}
    </div>
  );
};
