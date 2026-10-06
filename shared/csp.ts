/**
 * Content-Security-Policy for the renderer.
 *
 * The renderer loads only its own bundle and talks to the backend through the
 * preload bridge, so nothing needs another origin. Inline styles stay allowed
 * for Terminal's scoped stylesheet and the styles CodeMirror injects; scripts
 * never run inline. Blob URLs cover the PNG and CSV exports.
 */
export const RENDERER_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');
