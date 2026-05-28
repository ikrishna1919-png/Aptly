/**
 * Static mock for the Application Tracker coming-soon page. Shows
 * the intended kanban layout with representative-but-clearly-fake
 * column titles + cards. No interaction, no real data — purely a
 * shape preview so the visitor understands what's coming.
 */
export function ApplicationTrackerPreview() {
  const columns: { name: string; count: number; cards: { role: string; company: string }[] }[] = [
    {
      name: "Applied",
      count: 8,
      cards: [
        { role: "Senior Data Engineer", company: "Stripe" },
        { role: "Backend Engineer", company: "Anthropic" },
        { role: "Platform Engineer", company: "Linear" },
      ],
    },
    {
      name: "Interviewing",
      count: 3,
      cards: [
        { role: "Senior Software Engineer", company: "Vercel" },
        { role: "Staff ML Engineer", company: "Hugging Face" },
      ],
    },
    {
      name: "Offer",
      count: 1,
      cards: [{ role: "Senior Software Engineer", company: "Notion" }],
    },
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {columns.map((col) => (
        <div key={col.name} className="rounded-xl border border-border/70 bg-card p-4">
          <div className="flex items-baseline justify-between">
            <h3 className="font-display text-sm font-medium uppercase tracking-[0.12em] text-foreground">
              {col.name}
            </h3>
            <span className="text-xs text-muted-foreground">{col.count}</span>
          </div>
          <ul className="mt-3 space-y-2">
            {col.cards.map((c, i) => (
              <li
                key={i}
                className="rounded-lg border border-border/60 bg-background p-3 text-xs"
              >
                <p className="font-medium text-foreground">{c.role}</p>
                <p className="mt-0.5 text-muted-foreground">{c.company}</p>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
