"use client";

import {
  demoActions,
  demoCitations,
  demoCopy,
  demoEvidenceRows,
  demoSpecialists,
} from "../../../lib/hit-predictor-demo-data";
import styles from "./hit-predictor-demo.module.css";

function CitationLinks({ labels }: { labels: readonly number[] }) {
  return (
    <span className={styles.citations} aria-label={demoCopy.citationAria}>
      {labels.map(label => (
        <a key={label} href={`#source-${label}`} aria-label={`${demoCopy.citationViewAria} ${label}`}>
          [{label}]
        </a>
      ))}
    </span>
  );
}

function SemanticTitle({ text, phrase }: { text: string; phrase: string }) {
  const [before, after] = text.split(phrase);

  return <h2>{before}<span className={styles.keepTogether}>{phrase}</span>{after}</h2>;
}

export function HitPredictorDemoReport() {
  return (
    <div className={styles.page} data-report-ready="true">
      <header className={styles.topbar}>
        <a className={styles.brand} href="/" aria-label={demoCopy.homeAria}>
          <span className={styles.brandMark} aria-hidden="true">LS</span>
          <span><strong>{demoCopy.brand}</strong><small>LaunchScope</small></span>
        </a>
        <div className={styles.topActions}>
          <span className={styles.demoBadge}>{demoCopy.staticDemo}</span>
          <button type="button" onClick={() => window.print()}>{demoCopy.printReport}</button>
          <a href="/">{demoCopy.backHome}</a>
        </div>
      </header>

      <div className={styles.layout}>
        <aside className={styles.rail} aria-label={demoCopy.reportDirectory}>
          <span className={styles.railIndex}>{demoCopy.reportIndex}</span>
          <strong>{demoCopy.integratedReport}</strong>
          <nav>
            {[["#conclusion", "01"], ["#highlights", "02"], ["#issues", "03"], ["#roles", "04"], ["#actions", "05"], ["#evidence", "06"], ["#agent-reports", "07"]].map(([href, order], index) => (
              <a href={href} key={href}><span>{order}</span>{demoCopy.railItems[index]}</a>
            ))}
          </nav>
          <div className={styles.railNote}>
            <span>{demoCopy.sampleStatus}</span>
            <strong>{demoCopy.validateFurther}</strong>
            <small>{demoCopy.sourceHint}</small>
          </div>
        </aside>

        <main className={styles.report}>
          <header className={styles.reportHeading}>
            <div>
              <p className={styles.eyebrow}>{demoCopy.reportEyebrow}</p>
              <h1>{demoCopy.reportTitleLineOne}<br />{demoCopy.reportTitleLineTwo}</h1>
            </div>
            <div className={styles.identity}>
              <span>{demoCopy.evaluationTarget}</span>
              <strong>{demoCopy.productTitle}</strong>
              <dl>
                <div><dt>{demoCopy.organizationLabel}</dt><dd>{demoCopy.organization}</dd></div>
                <div><dt>{demoCopy.runLabel}</dt><dd>{demoCopy.runId}</dd></div>
                <div><dt>{demoCopy.reviewDateLabel}</dt><dd>{demoCopy.reviewDate}</dd></div>
              </dl>
            </div>
          </header>

          <section className={styles.decisionCard} aria-labelledby="potential-title">
            <div className={styles.scoreBlock}>
              <div className={styles.scoreDial} aria-label={`${demoCopy.potentialIndex} 63`}>
                <span><b>63</b><small>/ 100</small></span>
              </div>
              <div>
                <span className={styles.kicker} id="potential-title">{demoCopy.potentialIndex}</span>
                <p>{demoCopy.probabilityDisclaimer}</p>
              </div>
            </div>
            <dl className={styles.decisionFacts}>
              <div><dt>{demoCopy.currentStage}</dt><dd><strong>{demoCopy.stageValue}</strong><small>{demoCopy.stageDetail}</small></dd></div>
              <div><dt>{demoCopy.comparedWithLast}</dt><dd><strong className={styles.positive}>{demoCopy.improved}</strong><small>{demoCopy.comparisonDetail}</small></dd></div>
              <div><dt>{demoCopy.conclusionConfidence}</dt><dd><strong>{demoCopy.medium}</strong><small>{demoCopy.confidenceDetail}</small></dd></div>
              <div><dt>{demoCopy.currentRecommendation}</dt><dd><strong className={styles.attention}>{demoCopy.validateFurther}</strong><small>{demoCopy.recommendationDetail}</small></dd></div>
            </dl>
            <div className={styles.dimensionReadout} aria-label={demoCopy.dimensionAria}>
              {demoCopy.dimensions.map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span><i><b style={{ width: `${value}%` }} /></i><strong>{value}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className={styles.section} id="conclusion">
            <div className={styles.sectionNumber}>01</div>
            <div className={styles.sectionBody}>
              <p className={styles.kicker}>{demoCopy.conclusionKicker}</p>
              <SemanticTitle text={demoCopy.conclusionTitle} phrase={demoCopy.conclusionPhrase} />
              <p className={styles.lead}>
                {demoCopy.conclusionBody}
                <CitationLinks labels={[1, 4, 5]} />
              </p>
              <div className={styles.comparisonStrip}>
                <span>{demoCopy.changeLabel}</span>
                <strong>{demoCopy.changeTitle}</strong>
                <p>{demoCopy.changeBody}</p>
              </div>
            </div>
          </section>

          <section className={styles.section} id="highlights">
            <div className={styles.sectionNumber}>02</div>
            <div className={styles.sectionBody}>
              <p className={styles.kicker}>{demoCopy.highlightsKicker}</p>
              <h2>{demoCopy.highlightsTitle}</h2>
              <div className={styles.highlightGrid}>
                {demoCopy.highlights.map(([order, title, body, labels]) => (
                  <article key={order}><span>{order}</span><h3>{title}</h3><p>{body}<CitationLinks labels={labels} /></p></article>
                ))}
              </div>
            </div>
          </section>

          <section className={styles.section} id="issues">
            <div className={styles.sectionNumber}>03</div>
            <div className={styles.sectionBody}>
              <p className={styles.kicker}>{demoCopy.issuesKicker}</p>
              <h2>{demoCopy.issuesTitle}</h2>
              <div className={styles.issueList}>
                {demoCopy.issues.map(([label, title, body, consequence, citations], index) => (
                  <article data-tone={index === 0 ? "critical" : undefined} key={label}>
                    <span>{label}</span><h3>{title}</h3><p>{body}<CitationLinks labels={citations} /></p><small>{consequence}</small>
                  </article>
                ))}
              </div>
            </div>
          </section>

          <section className={styles.section} id="roles">
            <div className={styles.sectionNumber}>04</div>
            <div className={styles.sectionBody}>
              <p className={styles.kicker}>{demoCopy.rolesKicker}</p>
              <h2>{demoCopy.rolesTitle}</h2>
              <div className={styles.roleGrid}>
                {demoSpecialists.slice(0, 3).map(agent => (
                  <article key={agent.code}>
                    <div><span>{agent.order}</span><small>{agent.shortLabel}</small></div>
                    <strong>{agent.stance}</strong>
                    <p>{agent.verdict}<CitationLinks labels={agent.citations.slice(0, 2)} /></p>
                    <small>{demoCopy.biggestIssueLabel}</small>
                    <p>{agent.biggestIssue}</p>
                    <a href={`/demo/hit-predictor/agents/${agent.code}`}>{demoCopy.openSpecialist}</a>
                  </article>
                ))}
              </div>
              <article className={styles.crossDomain}>
                <span>{demoCopy.crossDomainLabel}</span>
                <h3>{demoCopy.crossDomainTitle}</h3>
                <p>{demoCopy.crossDomainBody}</p>
                <div><strong>{demoCopy.conflictLabel}</strong><p>{demoCopy.conflictBody}<CitationLinks labels={[1, 2, 4]} /></p></div>
              </article>
            </div>
          </section>

          <section className={styles.section} id="actions">
            <div className={styles.sectionNumber}>05</div>
            <div className={styles.sectionBody}>
              <p className={styles.kicker}>{demoCopy.actionsKicker}</p>
              <h2>{demoCopy.actionsTitle}</h2>
              <ol className={styles.actionList}>
                {demoActions.map(action => (
                  <li key={action.order}>
                    <span className={styles.actionNumber}>{action.order}</span>
                    <div className={styles.actionHead}><h3>{action.title}</h3><span>{action.owner} · {action.deadline}</span></div>
                    <p>{action.reason}<CitationLinks labels={action.citations} /></p>
                    <dl>
                      <div><dt>{demoCopy.successGate}</dt><dd>{action.success}</dd></div>
                      <div><dt>{demoCopy.failureSignal}</dt><dd>{action.failure}</dd></div>
                      <div><dt>{demoCopy.requiredEvidence}</dt><dd>{action.evidence}</dd></div>
                    </dl>
                  </li>
                ))}
              </ol>
              <div className={styles.retest}>
                <span>{demoCopy.retestLabel}</span>
                <div><h3>{demoCopy.retestTitle}</h3><p>{demoCopy.retestBody}</p></div>
              </div>
            </div>
          </section>

          <section className={styles.section} id="evidence">
            <div className={styles.sectionNumber}>06</div>
            <div className={styles.sectionBody}>
              <p className={styles.kicker}>{demoCopy.evidenceKicker}</p>
              <h2>{demoCopy.evidenceTitle}</h2>
              <div className={styles.evidenceSummary}>
                {demoCopy.evidenceStats.map(([value, label]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}
                <p>{demoCopy.evidenceDemoBoundary}</p>
              </div>
              <div className={styles.tableWrap}>
                <table>
                  <thead><tr>{demoCopy.evidenceHeaders.map(header => <th key={header}>{header}</th>)}</tr></thead>
                  <tbody>{demoEvidenceRows.map(row => (
                    <tr key={row[0]}><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td><td>{row[3]}</td><td><CitationLinks labels={[row[4]]} /></td></tr>
                  ))}</tbody>
                </table>
              </div>
              <ol className={styles.sourceDirectory}>
                {demoCitations.map(source => (
                  <li id={`source-${source.label}`} key={source.label}>
                    <span>[{source.label}]</span>
                    <div><strong>{source.title}</strong><p>{source.publisher} · {source.kind} · {source.locator}</p></div>
                    <em data-audit={source.auditLabel}>{source.auditLabel}</em>
                    {source.href && <a href={source.href} target="_blank" rel="noreferrer">{demoCopy.openSource}</a>}
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <section className={styles.section} id="agent-reports">
            <div className={styles.sectionNumber}>07</div>
            <div className={styles.sectionBody}>
              <p className={styles.kicker}>{demoCopy.agentReportsKicker}</p>
              <h2>{demoCopy.agentReportsTitle}</h2>
              <div className={styles.agentCards}>
                {demoSpecialists.map(agent => (
                  <a key={agent.code} href={`/demo/hit-predictor/agents/${agent.code}`}>
                    <span>{agent.order}</span>
                    <small>{agent.shortLabel}</small>
                    <h3>{agent.label}</h3>
                    <strong>{agent.stance}</strong>
                    <p>{agent.verdict}</p>
                    <em>{demoCopy.readViews}</em>
                  </a>
                ))}
              </div>
              <details className={styles.auditDetails}>
                <summary>{demoCopy.auditSummary}</summary>
                <div>
                  <p>{demoCopy.demoBoundaryOne}</p>
                  <p>{demoCopy.demoBoundaryTwo}</p>
                </div>
              </details>
              <div className={styles.stopPanel}>
                <span>{demoCopy.stopConditions}</span>
                <ul>
                  {demoCopy.stopItems.map(item => <li key={item}>{item}</li>)}
                </ul>
              </div>
            </div>
          </section>

          <footer className={styles.footer}>
            <span>LAUNCHSCOPE / HIT PREDICTOR</span>
            <p>{demoCopy.footerDisclaimer}</p>
            <a href="/">{demoCopy.backHomeUp}</a>
          </footer>
        </main>
      </div>
    </div>
  );
}
