// Where a site view goes when it isn't the one on screen.
//
// Music keeps playing in the background. A page shrunk to 0 x 0 reports itself hidden (and a 0-width viewport), and YouTube Music
// then restarts the track every ~10 seconds, so a pause never sticks. (Measured in the running app: position 13, 3, 8, 13, 2, 7...)
// A page that stays full size but sits off the left edge of the window keeps playing without a glitch. Every other site is
// paused when you leave it, so it can shrink to nothing.

const MIN = 320;

function hiddenBounds(id, stage) {
  if (id === "music") {
    const w = Math.max(MIN, (stage && stage.width) || 0), h = Math.max(MIN, (stage && stage.height) || 0);
    return { x: -(w + 2000), y: (stage && stage.y) || 0, width: w, height: h };
  }
  return { x: 0, y: 0, width: 0, height: 0 };
}

module.exports = { hiddenBounds };
