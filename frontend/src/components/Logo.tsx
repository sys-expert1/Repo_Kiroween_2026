/** Geometric circuit-board skull: emerald eyes, restrained violet traces. Decorative. */
export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <rect width="64" height="64" rx="12" fill="#0B0F17" />
      <path
        d="M32 8 L50 16 L53 34 L46 42 L46 52 L18 52 L18 42 L11 34 L14 16 Z"
        fill="#111827" stroke="#334155" strokeWidth="2" strokeLinejoin="round"
      />
      <path d="M19 27 H28 V35 H19 Z M36 27 H45 V35 H36 Z" fill="#10B981" />
      <path d="M32 38 L28 45 H36 Z" fill="#334155" />
      <path d="M22 52 V46 M28 52 V47 M36 52 V47 M42 52 V46" stroke="#334155" strokeWidth="2" />
      <path d="M32 8 V18 M14 16 L22 20 M50 16 L42 20" stroke="#8B5CF6" strokeWidth="2" strokeLinecap="round" />
      <circle cx="32" cy="18" r="2" fill="#8B5CF6" />
    </svg>
  );
}
