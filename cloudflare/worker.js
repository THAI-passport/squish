// Backwards-compatible standalone Worker entry point. Cloudflare Pages uses
// backend/static/_worker.js; both routes share one implementation.
export { default } from '../backend/static/squish-email-worker.js';
