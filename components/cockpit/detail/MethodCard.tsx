import type { MethodSection } from "@/lib/types";

interface Props { sections: MethodSection[]; cross_link?: { label: string; href: string } }

export function MethodCard({ sections, cross_link }: Props) {
  if (!sections.length) return null;
  return (
    <section className="method" aria-labelledby="method-heading">
      <h2 id="method-heading" className="section-label">Méthode</h2>
      <div className="method-body">
        {sections.map((s, i) => (
          <div className="method-section" key={i}>
            <h3>{s.heading}</h3>
            {s.body.split(/\n\n+/).map((p, j) => <p key={j}>{p}</p>)}
          </div>
        ))}
      </div>
      {cross_link && (
        <a className="method-cross" href={cross_link.href}>
          {cross_link.label}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>
          </svg>
        </a>
      )}
    </section>
  );
}
