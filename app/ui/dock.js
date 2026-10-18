// Mirrors Home's two cards (two.js renders them; this view only floats them over the website).
const L = document.getElementById("L"), R = document.getElementById("R");
let lastL = "", lastR = "";
window.dock.onState(s => {
  if (s.leftHTML !== lastL) { L.innerHTML = lastL = s.leftHTML; }
  if (s.rightHTML !== lastR) { R.innerHTML = lastR = s.rightHTML; }
  L.className = "two-card left " + (s.leftClass || ""); R.className = "two-card right " + (s.rightClass || "");
  for (const [el, st] of [[L, s.left], [R, s.right]]) {
    if (!st) continue;
    const t = el.querySelector(".two-state"); if (t) t.textContent = st.text || "";
    const f = el.querySelector(".two-fill"); if (f) f.style.width = st.fill || "0";
  }
  document.body.classList.toggle("large", !!s.large);
});
L.onclick = () => window.dock.pick("left");
R.onclick = () => window.dock.pick("right");
