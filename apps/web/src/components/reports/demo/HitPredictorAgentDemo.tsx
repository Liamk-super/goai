import type { DemoSpecialist } from "../../../lib/hit-predictor-demo-data";
import { demoCitations, demoCopy } from "../../../lib/hit-predictor-demo-data";
import styles from "./hit-predictor-demo.module.css";

function CitationLinks({ labels }: { labels: readonly number[] }) {
  return (
    <span className={styles.citations} aria-label={demoCopy.citationAria}>
      {labels.map(label => <a key={label} href={`#source-${label}`}>[{label}]</a>)}
    </span>
  );
}

export function HitPredictorAgentDemo({
  agent,
  view,
}: {
  agent: DemoSpecialist;
  view: "summary" | "full";
}) {
  const sections = view === "summary" ? agent.summarySections : agent.fullSections;
  return (
    <div className={`${styles.page} ${styles.agentPage}`} data-report-ready="true">
      <header className={styles.topbar}>
        <a className={styles.brand} href="/" aria-label={demoCopy.homeAria}>
          <span className={styles.brandMark} aria-hidden="true">LS</span>
          <span><strong>{demoCopy.brand}</strong><small>LaunchScope</small></span>
        </a>
        <div className={styles.topActions}>
          <span className={styles.demoBadge}>{demoCopy.sharedViewDemo}</span>
          <a href="/demo/hit-predictor#agent-reports">{demoCopy.backSupervisor}</a>
        </div>
      </header>

      <main className={styles.agentDocument}>
        <header className={styles.agentHero}>
          <div>
            <p className={styles.eyebrow}>{agent.order} / {demoCopy.specialistEyebrow}</p>
            <h1>{agent.label}</h1>
            <p>{agent.verdict}<CitationLinks labels={agent.citations} /></p>
          </div>
          <dl>
            <div><dt>{demoCopy.roleLabel}</dt><dd>{agent.shortLabel}</dd></div>
            <div><dt>{demoCopy.readingLabel}</dt><dd>{agent.score}</dd></div>
            <div><dt>{demoCopy.conclusionConfidence}</dt><dd>{agent.confidence}</dd></div>
            <div><dt>{demoCopy.currentJudgment}</dt><dd>{agent.stance}</dd></div>
          </dl>
        </header>

        <nav className={styles.viewTabs} aria-label={demoCopy.reportViewAria}>
          <a
            href={`/demo/hit-predictor/agents/${agent.code}?view=summary`}
            aria-current={view === "summary" ? "page" : undefined}
          >{demoCopy.summaryView}</a>
          <a
            href={`/demo/hit-predictor/agents/${agent.code}?view=full`}
            aria-current={view === "full" ? "page" : undefined}
          >{demoCopy.fullView}</a>
          <span>{demoCopy.canonicalNote}</span>
        </nav>

        <section className={styles.agentKeyIssue}>
          <span>{demoCopy.specialistIssue}</span>
          <h2>{agent.biggestIssue}</h2>
        </section>

        <div className={styles.agentSections}>
          {sections.map((section, index) => (
            <section key={section.title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div>
                <h2>{section.title}</h2>
                {section.lead && <p className={styles.lead}>{section.lead}</p>}
                <ul>{section.bullets.map(bullet => <li key={bullet}>{bullet}</li>)}</ul>
              </div>
            </section>
          ))}
        </div>

        <section className={styles.agentSources} id="evidence">
          <p className={styles.kicker}>{demoCopy.specialistSources}</p>
          <h2>{demoCopy.sourceDirectory}</h2>
          <ol className={styles.sourceDirectory}>
            {demoCitations.filter(source => agent.citations.includes(source.label)).map(source => (
              <li id={`source-${source.label}`} key={source.label}>
                <span>[{source.label}]</span>
                <div><strong>{source.title}</strong><p>{source.publisher} · {source.locator}</p></div>
                <em data-audit={source.auditLabel}>{source.auditLabel}</em>
                {source.href && <a href={source.href} target="_blank" rel="noreferrer">{demoCopy.openSource}</a>}
              </li>
            ))}
          </ol>
        </section>

        <details className={styles.auditDetails}>
          <summary>{demoCopy.specialistAuditSummary}</summary>
          <div>
            <p>{demoCopy.specialistBoundaryOne}</p>
            <p>{demoCopy.specialistBoundaryTwo}</p>
          </div>
        </details>

        <footer className={styles.agentFooter}>
          <a href="/demo/hit-predictor#agent-reports">{demoCopy.backSupervisorLong}</a>
          <a href="/">{demoCopy.homeShort}</a>
        </footer>
      </main>
    </div>
  );
}
