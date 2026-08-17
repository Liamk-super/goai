import type { Project, Run } from "./api-client.ts";

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase();
}

export function filterProjects(projects: Project[], query: string): Project[] {
  const term = normalized(query);
  if (!term) return projects;
  return projects.filter(project => normalized(project.name).includes(term));
}

export function runVersionLabel(run: Run, fallback: number): string {
  const label = run.product_version_label?.trim();
  if (label) return label;
  if (run.product_version_number) return `V${run.product_version_number}`;
  return `V${fallback}`;
}

export function filterProjectRuns(runs: Run[], query: string): Run[] {
  const term = normalized(query).replaceAll(" ", "");
  if (!term) return runs;
  return runs.filter((run, index) => {
    const fallback = runs.length - index;
    const version = runVersionLabel(run, fallback);
    const versionNumber = run.product_version_number ? `v${run.product_version_number}` : "";
    return [version, versionNumber, run.status]
      .some(value => normalized(value).replaceAll(" ", "").includes(term));
  });
}
