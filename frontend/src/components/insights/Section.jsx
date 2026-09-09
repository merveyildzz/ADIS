export default function Section({ icon, title, count, children }) {
  if (count === 0) return null;
  return (
    <section className="insight-section">
      <h3 className="insight-section-title">
        {icon && <>{icon} </>}
        {title} <span className="insight-section-count">({count})</span>
      </h3>
      <div className="insight-card-grid">{children}</div>
    </section>
  );
}
