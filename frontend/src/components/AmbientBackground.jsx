import { useMemo } from "react";

/**
 * Fixed ambient scene behind the whole app: drifting aurora blobs, a fading
 * blueprint grid, a whisper of film grain, and a slow field of particles.
 * Pure transform/opacity animation (GPU-cheap) and pointer-events-none, so it
 * can never intercept clicks or slow interaction.
 */
const PARTICLE_COUNT = 26;

export default function AmbientBackground({ density = PARTICLE_COUNT }) {
  const particles = useMemo(
    () =>
      Array.from({ length: density }, (_, i) => {
        // Deterministic pseudo-random spread so SSR/StrictMode stays stable.
        const x = (i * 37.7 + 11) % 100;
        const y = (i * 53.3 + 29) % 100;
        const size = 1.5 + ((i * 7) % 3);
        const duration = 6 + ((i * 13) % 9);
        const delay = -((i * 1.7) % 9);
        const hue = i % 3 === 0 ? "70,216,255" : i % 3 === 1 ? "139,124,246" : "244,113,181";
        return { x, y, size, duration, delay, hue, key: i };
      }),
    [density]
  );

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      {/* Aurora blobs */}
      <div className="absolute -top-40 left-[8%] h-[480px] w-[620px] rounded-full bg-brand-600/25 blur-[130px] animate-aurora-a" />
      <div className="absolute -top-24 right-[4%] h-[420px] w-[520px] rounded-full bg-accent-cyan/15 blur-[130px] animate-aurora-b" />
      <div className="absolute bottom-[-20%] left-[30%] h-[460px] w-[680px] rounded-full bg-accent-pink/[0.07] blur-[150px] animate-aurora-a" />

      {/* Blueprint grid, fading toward the bottom */}
      <div className="bg-grid absolute inset-0" />

      {/* Particles */}
      {particles.map((p) => (
        <span
          key={p.key}
          className="absolute rounded-full animate-float-y"
          style={{
            left: `${p.x}%`,
            top: `${p.y}%`,
            width: p.size,
            height: p.size,
            background: `rgba(${p.hue},0.8)`,
            boxShadow: `0 0 8px 1px rgba(${p.hue},0.5)`,
            animationDuration: `${p.duration}s`,
            animationDelay: `${p.delay}s`,
          }}
        />
      ))}

      {/* Film grain + vignette */}
      <div className="bg-noise absolute inset-0" />
      <div
        className="absolute inset-0"
        style={{ boxShadow: "inset 0 0 220px 60px rgba(3,5,10,0.75)" }}
      />
    </div>
  );
}
