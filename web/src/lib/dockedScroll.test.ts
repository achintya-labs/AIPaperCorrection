import assert from "node:assert/strict";
import { test } from "node:test";
import { pageScrollFor, type WheelState } from "./dockedScroll.ts";

const base: WheelState = { deltaY: 100, deltaMode: 0, paneScrollTop: 0, pageY: 0, pageMax: 220, viewportH: 800, nested: true };
const s = (o: Partial<WheelState>): WheelState => ({ ...base, ...o });

test("scrolling down moves the page first, until the panes reach the top of the window", () => {
  assert.equal(pageScrollFor(s({ pageY: 0 })), 100);
  assert.equal(pageScrollFor(s({ pageY: 150 })), 70); // only as far as the page can go
  assert.equal(pageScrollFor(s({ pageY: 219.5 })), 0); // within a pixel of docked: the pane takes over
});

test("once docked, scrolling down belongs to the pane", () => {
  assert.equal(pageScrollFor(s({ pageY: 220 })), 0);
});

test("scrolling up scrolls the pane first, and only at its top does it move the page back", () => {
  assert.equal(pageScrollFor(s({ deltaY: -100, pageY: 220, paneScrollTop: 400 })), 0);
  assert.equal(pageScrollFor(s({ deltaY: -100, pageY: 220, paneScrollTop: 0 })), -100);
  assert.equal(pageScrollFor(s({ deltaY: -100, pageY: 40, paneScrollTop: 0 })), -40); // not past the top of the page
});

test("nothing for the page when it is already at the top and you scroll up", () => {
  assert.equal(pageScrollFor(s({ deltaY: -100, pageY: 0, paneScrollTop: 0 })), 0);
});

test("a page that can't scroll, or panes laid out inline, are left to the browser", () => {
  assert.equal(pageScrollFor(s({ pageMax: 0 })), 0);
  assert.equal(pageScrollFor(s({ pageMax: 1 })), 0);
  assert.equal(pageScrollFor(s({ nested: false })), 0);
});

test("line and page wheel modes are converted to pixels", () => {
  assert.equal(pageScrollFor(s({ deltaY: 3, deltaMode: 1 })), 48);
  assert.equal(pageScrollFor(s({ deltaY: 1, deltaMode: 2, pageMax: 5000 })), 800);
});

test("a zero delta does nothing", () => {
  assert.equal(pageScrollFor(s({ deltaY: 0 })), 0);
});
