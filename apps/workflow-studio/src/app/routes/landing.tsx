import { useState, useMemo } from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowRight, Play } from 'lucide-react'

const SPARKLE_COLORS = [
  'white', 'white', 'white', 'white', 'white',
  '#18a0fb', '#18a0fb', '#a78bfa', '#34d399', '#f59e0b', '#7b61ff',
]

function generateSparkles(count: number) {
  const sparkles = []
  for (let i = 0; i < count; i++) {
    sparkles.push({
      id: i,
      size: 1 + Math.random() * 3,
      left: Math.random() * 100 + '%',
      top: Math.random() * 100 + '%',
      duration: 1.5 + Math.random() * 5 + 's',
      delay: Math.random() * 8 + 's',
      color: SPARKLE_COLORS[Math.floor(Math.random() * SPARKLE_COLORS.length)],
    })
  }
  return sparkles
}

export default function LandingPage() {
  const [glowPos, setGlowPos] = useState({ x: -300, y: -300 })
  const sparkles = useMemo(() => generateSparkles(80), [])

  const handleMouseMove = (e: React.MouseEvent) => {
    setGlowPos({ x: e.clientX, y: e.clientY })
  }

  return (
    <div
      className="landing-page relative h-screen w-full overflow-hidden bg-[#050505] text-white flex flex-col"
      onMouseMove={handleMouseMove}
    >
      <style>{`
        .landing-page {
          font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
        }

        .landing-grid-background {
          position: absolute;
          width: 200%;
          height: 200%;
          top: 0;
          left: -50%;
          background-image:
            linear-gradient(to right, rgba(255, 255, 255, 0.08) 1px, transparent 1px),
            linear-gradient(to bottom, rgba(255, 255, 255, 0.08) 1px, transparent 1px);
          background-size: 50px 50px;
          mask-image: radial-gradient(ellipse 80% 70% at 50% 0%, black 20%, transparent);
          transform: perspective(1000px) rotateX(60deg);
          transform-origin: top;
          z-index: 1;
          pointer-events: none;
        }

        .landing-mouse-glow {
          position: fixed;
          width: 600px;
          height: 600px;
          background: radial-gradient(circle, rgba(24, 160, 251, 0.12) 0%, transparent 70%);
          border-radius: 50%;
          pointer-events: none;
          z-index: 2;
          transform: translate(-50%, -50%);
          transition: opacity 0.3s ease;
        }

        .landing-sparkle {
          position: absolute;
          border-radius: 50%;
          opacity: 0;
          pointer-events: none;
          z-index: 3;
          animation-name: landing-twinkle;
          animation-timing-function: ease-in-out;
          animation-iteration-count: infinite;
        }

        @keyframes landing-twinkle {
          0%, 100% { opacity: 0; transform: scale(0.5); }
          50% { opacity: 0.8; transform: scale(1.2); }
        }

        .landing-cta-button {
          position: relative;
          background: linear-gradient(135deg, #fff 0%, #e2e8f0 100%);
          color: #000;
          overflow: hidden;
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          cursor: pointer;
          border: none;
        }

        .landing-cta-button:hover {
          transform: translateY(-2px);
          box-shadow: 0 0 30px rgba(255, 255, 255, 0.3);
        }

        .landing-cta-button:active {
          transform: translateY(0) scale(0.98);
        }

        .landing-glass-card {
          background: rgba(255, 255, 255, 0.03);
          backdrop-filter: blur(12px);
          border: 1px solid rgba(255, 255, 255, 0.08);
        }

        .landing-shimmer {
          background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.05), transparent);
          background-size: 200% 100%;
          animation: landing-shimmer 3s infinite;
        }

        @keyframes landing-shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
      `}</style>

      {/* Mouse follower glow */}
      <div
        className="landing-mouse-glow"
        style={{ left: glowPos.x, top: glowPos.y }}
      />

      {/* Grid background */}
      <div className="landing-grid-background" />

      {/* Sparkle field */}
      <div className="absolute inset-0 pointer-events-none">
        {sparkles.map((s) => (
          <div
            key={s.id}
            className="landing-sparkle"
            style={{
              width: s.size,
              height: s.size,
              left: s.left,
              top: s.top,
              animationDuration: s.duration,
              animationDelay: s.delay,
              background: s.color,
            }}
          />
        ))}
      </div>

      {/* Header */}
      <header className="relative z-10 flex items-center justify-between px-8 py-4">
        <div className="flex items-center gap-2">
          <span className="text-[15px] font-semibold tracking-tight text-white">Command</span>
          <span className="rounded bg-[#18a0fb] px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">Studio</span>
        </div>

        <div className="flex items-center gap-3">
          <Link
            to="/workflows"
            className="px-4 py-2 rounded-md transition-all font-medium flex items-center gap-2 text-slate-400 hover:text-white hover:bg-white/5"
          >
            Workflows
          </Link>
          <Link
            to="/editor"
            className="px-4 py-2 rounded-md transition-all font-medium flex items-center gap-2 text-slate-400 hover:text-white hover:bg-white/5"
          >
            Editor
          </Link>
        </div>
      </header>

      {/* Hero section */}
      <section className="relative z-10 flex-1 flex flex-col justify-center px-6 text-center">
        <div className="mx-auto max-w-[56rem]">
          {/* Pill badge */}
          <div className="mb-4 flex justify-center">
            <div className="landing-shimmer inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-4 py-1.5 text-xs font-semibold uppercase tracking-wider text-slate-300 backdrop-blur-sm">
              <span className="h-2 w-2 animate-pulse rounded-full bg-[#18a0fb]" />
              Workflow Automation Platform
            </div>
          </div>

          {/* Heading */}
          <h1 className="mb-3 text-4xl font-extrabold leading-[1.1] tracking-tighter sm:text-5xl md:text-7xl">
            Automate anything, <br />
            <span className="bg-gradient-to-b from-white to-slate-500 bg-clip-text text-transparent">
              visually.
            </span>
          </h1>

          {/* Subtitle */}
          <p className="mx-auto mb-6 text-sm leading-relaxed text-slate-400 sm:text-base md:text-lg max-w-[42rem]">
            <span className="font-semibold text-[#18a0fb]">Drag-and-drop</span> a visual canvas to design workflows,
            <span className="font-semibold text-[#a78bfa]"> Connect APIs</span> and transform data across services,
            <span className="font-semibold text-[#34d399]"> Orchestrate automations</span> with AI-powered intelligence — all without writing code.
          </p>

          {/* CTA buttons */}
          <div className="flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              to="/workflows"
              className="landing-cta-button group inline-flex items-center gap-3 rounded-xl px-10 py-4 text-base font-bold shadow-[0_0_40px_rgba(24,160,251,0.15)]"
            >
              Get Started
              <ArrowRight className="h-5 w-5 transition-transform group-hover:translate-x-1" />
            </Link>
            <Link
              to="/editor"
              className="inline-flex items-center gap-2 rounded-xl bg-[#1a1a1a] border border-white/10 px-10 py-4 text-base font-bold text-white hover:bg-[#222] transition-all"
            >
              <Play className="h-4 w-4 fill-current" />
              Create New Flow
            </Link>
          </div>

          {/* Feature pills row */}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
            {[
              { label: 'Visual Canvas Editor', color: '#18a0fb' },
              { label: 'API Integrations', color: '#a78bfa' },
              { label: 'AI-Powered Workflows', color: '#34d399' },
              { label: 'Real-time Execution', color: '#f59e0b' },
            ].map((feature, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs"
                style={{
                  borderColor: `${feature.color}33`,
                  backgroundColor: `${feature.color}0D`,
                  color: feature.color,
                }}
              >
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: feature.color }} />
                {feature.label}
              </span>
            ))}
          </div>
        </div>

        {/* Visual mock — node-based interface preview */}
        <div className="relative mx-auto mt-6 w-full max-w-[56rem]">
          <div className="absolute -inset-1 rounded-[32px] bg-gradient-to-r from-[#18a0fb] to-[#7b61ff] opacity-20 blur-2xl" />
          <div className="landing-glass-card relative overflow-hidden rounded-[28px] p-4">
            {/* Fake toolbar */}
            <div className="mb-3 flex items-center gap-2 opacity-40">
              <div className="h-2.5 w-2.5 rounded-full bg-white/20" />
              <div className="h-2.5 w-2.5 rounded-full bg-white/20" />
              <div className="h-2.5 w-2.5 rounded-full bg-white/20" />
              <div className="ml-4 h-2 w-24 rounded bg-white/10" />
            </div>
            {/* Canvas area */}
            <div className="flex aspect-[16/6] items-center justify-center rounded-xl bg-white/[0.02] border border-white/[0.04]">
              <svg
                className="h-full w-full"
                viewBox="0 0 800 340"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <defs>
                  <marker id="arrow" viewBox="0 0 10 8" refX="9" refY="4" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
                    <path d="M0 0L10 4L0 8Z" fill="#4a5568" />
                  </marker>
                  <marker id="arrow-green" viewBox="0 0 10 8" refX="9" refY="4" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
                    <path d="M0 0L10 4L0 8Z" fill="#34d399" />
                  </marker>
                  <filter id="glow-amber" x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="4" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                  </filter>
                </defs>

                {/* Dot grid background */}
                <pattern id="dotgrid" x="0" y="0" width="20" height="20" patternUnits="userSpaceOnUse">
                  <circle cx="10" cy="10" r="0.5" fill="white" opacity="0.15" />
                </pattern>
                <rect width="800" height="340" fill="url(#dotgrid)" />

                {/* ── Edges (smooth step paths) ── */}
                {/* Webhook → Code */}
                <path d="M152 170 H248" stroke="#4a5568" strokeWidth="1.5" markerEnd="url(#arrow)" />
                {/* Code → If/Switch */}
                <path d="M312 170 H398" stroke="#4a5568" strokeWidth="1.5" markerEnd="url(#arrow)" />
                {/* If/Switch → MongoDB (upper branch) */}
                <path d="M462 155 L462 105 Q462 97 470 97 L548 97" stroke="#4a5568" strokeWidth="1.5" markerEnd="url(#arrow)" />
                {/* If/Switch → SendEmail (lower branch) */}
                <path d="M462 185 L462 243 Q462 251 470 251 L548 251" stroke="#4a5568" strokeWidth="1.5" markerEnd="url(#arrow)" />
                {/* MongoDB → Output */}
                <path d="M612 97 L660 97 Q668 97 668 105 L668 155" stroke="#4a5568" strokeWidth="1.5" markerEnd="url(#arrow)" />
                {/* SendEmail → Output */}
                <path d="M612 251 L660 251 Q668 251 668 243 L668 185" stroke="#4a5568" strokeWidth="1.5" markerEnd="url(#arrow)" />

                {/* Running edge overlay (Webhook → Code) */}
                <path d="M152 170 H248" stroke="#f59e0b" strokeWidth="2" strokeDasharray="6 4" opacity="0.7" filter="url(#glow-amber)">
                  <animate attributeName="stroke-dashoffset" from="20" to="0" dur="0.8s" repeatCount="indefinite" />
                </path>

                {/* Success edge overlay (Code → If) */}
                <path d="M312 170 H398" stroke="#34d399" strokeWidth="2" opacity="0.5" markerEnd="url(#arrow-green)" />

                {/* ── Node: Trigger (amber) ── */}
                <g>
                  <rect x="96" y="142" width="56" height="48" rx="12" fill="#fde68a" opacity="0.25" stroke="#fcd34d" strokeWidth="1.5" />
                  {/* Running ring */}
                  <rect x="94" y="140" width="60" height="52" rx="14" fill="none" stroke="#f59e0b" strokeWidth="1.5" opacity="0.5">
                    <animate attributeName="opacity" values="0.2;0.6;0.2" dur="2s" repeatCount="indefinite" />
                  </rect>
                </g>

                {/* ── Node: Transform (blue) ── */}
                <g>
                  <rect x="248" y="142" width="64" height="48" rx="12" fill="#bfdbfe" opacity="0.2" stroke="#93c5fd" strokeWidth="1.5" />
                  {/* Success badge */}
                  <circle cx="305" cy="147" r="7" fill="#14ae5c" />
                  <polyline points="301,147 304,150 309,144" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </g>

                {/* ── Node: Flow (purple) ── */}
                <g>
                  <rect x="398" y="142" width="64" height="48" rx="12" fill="#ddd6fe" opacity="0.2" stroke="#c4b5fd" strokeWidth="1.5" />
                </g>

                {/* ── Node: Action (green, upper) ── */}
                <g>
                  <rect x="548" y="73" width="64" height="48" rx="12" fill="#a7f3d0" opacity="0.2" stroke="#6ee7b7" strokeWidth="1.5" />
                </g>

                {/* ── Node: Action (green, lower) ── */}
                <g>
                  <rect x="548" y="227" width="64" height="48" rx="12" fill="#a7f3d0" opacity="0.2" stroke="#6ee7b7" strokeWidth="1.5" />
                </g>

                {/* ── Node: Output (teal) ── */}
                <g>
                  <rect x="640" y="146" width="56" height="48" rx="12" fill="#99f6e4" opacity="0.2" stroke="#5eead4" strokeWidth="1.5" />
                </g>

                {/* Handle dots */}
                <circle cx="152" cy="166" r="3" fill="#9ca3af" />
                <circle cx="248" cy="166" r="3" fill="#9ca3af" />
                <circle cx="312" cy="166" r="3" fill="#9ca3af" />
                <circle cx="398" cy="166" r="3" fill="#9ca3af" />
                <circle cx="462" cy="155" r="3" fill="#9ca3af" />
                <circle cx="462" cy="178" r="3" fill="#9ca3af" />
                <circle cx="548" cy="97" r="3" fill="#9ca3af" />
                <circle cx="612" cy="97" r="3" fill="#9ca3af" />
                <circle cx="548" cy="251" r="3" fill="#9ca3af" />
                <circle cx="612" cy="251" r="3" fill="#9ca3af" />
                <circle cx="640" cy="170" r="3" fill="#9ca3af" />
              </svg>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
