export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface RestoredBounds {
  width: number;
  height: number;
  x?: number;
  y?: number;
}

/** The strip along the top of the window where the custom title bar sits. */
const TITLE_STRIP = 36;
/** How much of that strip must land on a display to grab and drag it. */
const MIN_VISIBLE_WIDTH = 120;
const MIN_VISIBLE_HEIGHT = 20;

const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);

function overlap(a: Rect, b: Rect): { width: number; height: number } {
  return {
    width: Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x),
    height: Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y),
  };
}

/**
 * Where to reopen the main window, given the bounds saved last time and the
 * work areas of the displays connected now.
 *
 * The window is frameless. Restored onto a monitor that has since been
 * unplugged, or past the edge of a smaller one, it opens where nobody can see
 * it and there is no title bar to drag it back. The saved position is kept
 * only when enough of the title bar lands on a connected display; otherwise
 * Electron centres the window on the primary display. The size never exceeds
 * the work area the window opens on.
 */
export function restoreBounds(
  saved: Partial<Rect> | null | undefined,
  workAreas: Rect[],
  primary: Rect,
): RestoredBounds {
  const fallback = {
    width: Math.min(1380, primary.width - 40),
    height: Math.min(900, primary.height - 40),
  };
  if (!saved || !finite(saved.width) || !finite(saved.height) || saved.width <= 0 || saved.height <= 0) {
    return fallback;
  }
  const fit = (area: Rect) => ({
    width: Math.min(saved.width!, area.width),
    height: Math.min(saved.height!, area.height),
  });
  if (finite(saved.x) && finite(saved.y)) {
    const strip = { x: saved.x, y: saved.y, width: saved.width, height: TITLE_STRIP };
    const home = workAreas.find((area) => {
      const o = overlap(strip, area);
      return o.width >= MIN_VISIBLE_WIDTH && o.height >= MIN_VISIBLE_HEIGHT;
    });
    if (home) return { ...fit(home), x: saved.x, y: saved.y };
  }
  return fit(primary);
}
