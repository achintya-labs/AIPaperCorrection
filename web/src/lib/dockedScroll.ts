/**
 * Wheel behaviour for the review screen's two panes (the scanned sheet and the grade cards).
 *
 * The page scrolls so the header can be moved out of the way; once it has, the panes fill the window and each
 * scrolls on its own. If the wheel went straight to the pane under the mouse, the page could never be scrolled
 * from there, so instead:
 *   - scrolling DOWN moves the page until the panes reach the top of the window ("docked"), then the pane scrolls;
 *   - scrolling UP scrolls the pane until it is back at its top, and only then moves the page back up.
 */
export interface WheelState {
  deltaY: number;
  deltaMode: number; // 0 pixels, 1 lines, 2 pages
  paneScrollTop: number;
  pageY: number;
  pageMax: number; // the furthest the page can scroll
  viewportH: number;
  /** false when the panes are laid out inline (narrow windows): then the browser's own scrolling is right */
  nested: boolean;
}

/** How many pixels the PAGE should scroll for this wheel event; 0 means leave it to the pane. */
export function pageScrollFor(s: WheelState): number {
  if (!s.nested || s.pageMax <= 1) return 0;
  const dy = s.deltaMode === 1 ? s.deltaY * 16 : s.deltaMode === 2 ? s.deltaY * s.viewportH : s.deltaY;
  if (dy > 0 && s.pageY < s.pageMax - 1) return Math.min(dy, s.pageMax - s.pageY);
  if (dy < 0 && s.paneScrollTop <= 0 && s.pageY > 0) return Math.max(dy, -s.pageY);
  return 0;
}
