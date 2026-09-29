/** Static spatial guides: decoration, never simulated market activity. */
export function drawRadar(ctx: CanvasRenderingContext2D, w: number, h: number) {
  const cx = w / 2, cy = h / 2;
  const radius = Math.min(w, h) * .37;
  ctx.save();
  const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 1.6);
  glow.addColorStop(0, 'rgba(52,126,230,.14)');
  glow.addColorStop(1, 'rgba(9,18,33,0)');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);
  ctx.lineWidth = 1;
  for (const scale of [.38, .66, 1, 1.22]) {
    ctx.strokeStyle = scale === 1 ? 'rgba(108,177,255,.20)' : 'rgba(108,177,255,.08)';
    ctx.beginPath(); ctx.arc(cx, cy, radius * scale, 0, Math.PI * 2); ctx.stroke();
  }
  for (let i = 0; i < 72; i++) {
    const angle = i * Math.PI / 36;
    const major = i % 6 === 0;
    ctx.strokeStyle = major ? 'rgba(151,204,255,.38)' : 'rgba(108,177,255,.13)';
    const end = radius * 1.22;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(angle) * end, cy + Math.sin(angle) * end);
    ctx.lineTo(cx + Math.cos(angle) * (end - (major ? 9 : 3)),
               cy + Math.sin(angle) * (end - (major ? 9 : 3)));
    ctx.stroke();
  }
  ctx.setLineDash([3, 8]); ctx.strokeStyle = 'rgba(108,177,255,.10)';
  ctx.beginPath(); ctx.moveTo(cx - radius * 1.3, cy); ctx.lineTo(cx + radius * 1.3, cy);
  ctx.moveTo(cx, cy - radius * 1.3); ctx.lineTo(cx, cy + radius * 1.3); ctx.stroke();
  ctx.restore();
}
