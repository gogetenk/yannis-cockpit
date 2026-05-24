interface Props { brief: string }

export function AICard({ brief }: Props) {
  return (
    <aside className="ai-card" aria-label="Analyse IA">
      <div className="ai-card-label">
        <span className="ai-card-dot" aria-hidden />
        Analyse Claude
      </div>
      <p className="ai-card-text">{brief}</p>
    </aside>
  );
}
