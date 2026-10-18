// Run: npm test
// Where a site view goes when it isn't the one on screen. Music keeps playing in the background, and a page shrunk to
// 0 x 0 reports itself hidden and YouTube Music then restarts the track every ~10 seconds (measured in the real app),
// so a playing page must stay a real, full-size page that is simply out of sight.
const test = require("node:test");
const assert = require("node:assert");
const { hiddenBounds } = require("../lib/layout");

const STAGE = { x: 0, y: 136, width: 1200, height: 664 };

test("music stays a full-size page, moved out of sight", () => {
  const b = hiddenBounds("music", STAGE);
  assert.strictEqual(b.width, STAGE.width);
  assert.strictEqual(b.height, STAGE.height);
  assert.ok(b.x + b.width <= 0, "entirely off the left edge of the window");
});

test("every other site is shrunk to nothing, as before (they are paused when you leave)", () => {
  for (const id of ["youtube", "reels", "shorts", "whatsapp", "netflix", "web", "aiweb", "food"])
    assert.deepStrictEqual(hiddenBounds(id, STAGE), { x: 0, y: 0, width: 0, height: 0 }, id);
});

test("a stage that hasn't been measured yet still gives music a real size", () => {
  const b = hiddenBounds("music", { x: 0, y: 0, width: 0, height: 0 });
  assert.ok(b.width >= 300 && b.height >= 300);
  assert.ok(b.x + b.width <= 0);
});
