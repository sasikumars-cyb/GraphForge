/** GraphForge's mark: three connected nodes, echoing the Knowledge Graph
 * this product is built around, rendered in the brand gradient. */
export function Logomark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="url(#logomark-gradient)" />
      <g stroke="white" strokeOpacity="0.9" strokeWidth="1.4" strokeLinecap="round">
        <line x1="16" y1="10" x2="10" y2="20" />
        <line x1="16" y1="10" x2="22" y2="20" />
        <line x1="10" y1="20" x2="22" y2="20" />
      </g>
      <circle cx="16" cy="10" r="3" fill="white" />
      <circle cx="10" cy="20" r="2.4" fill="white" fillOpacity="0.85" />
      <circle cx="22" cy="20" r="2.4" fill="white" fillOpacity="0.85" />
      <defs>
        <linearGradient id="logomark-gradient" x1="0" y1="0" x2="32" y2="32">
          <stop offset="0%" stopColor="#8425ff" />
          <stop offset="100%" stopColor="#47bfff" />
        </linearGradient>
      </defs>
    </svg>
  );
}
