import Link from "next/link";

interface Props { title: string; meta: string }

export function DetailHeader({ title, meta }: Props) {
  return (
    <header className="detail-header" role="banner">
      <Link href="/" className="detail-back" aria-label="Retour au cockpit">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M19 12H5"/><path d="M11 18l-6-6 6-6"/>
        </svg>
      </Link>
      <div className="detail-head-text">
        <h1 className="detail-title">{title}</h1>
        <span className="detail-meta">{meta}</span>
      </div>
    </header>
  );
}
