const MODEL_CONTENT_LIMIT = 30_000;
const PAGE_CONTEXT_LIMIT = 1_800;
const MAX_RENDERED_VISUAL_PAGES = 24;

export type PdfTableAnalysis = {
  status: "NOT_DETECTED" | "DETECTED" | "UNDERSTOOD" | "FAILED";
  title: string | null;
  headers: string[];
  rows: string[][];
  confidence: number;
};

export type PdfVisualAnalysis = {
  status: "NOT_DETECTED" | "AWAITING_VISION" | "UNDERSTOOD" | "NOT_INSPECTED" | "FAILED";
  recognitionType: "TEXT" | "TABLE" | "IMAGE" | "DIAGRAM" | "SCREENSHOT" | "SCAN" | "MIXED";
  imageCount: number;
  rotationDegrees: 0 | 90 | 180 | 270;
  summary: string | null;
  confidence: number | null;
  source: "LOCAL_PDFJS" | "LOCAL_POSITIONAL" | "MODEL_VISION";
  error?: string;
};

export type PdfPageAnalysis = {
  pageNumber: number;
  text: string;
  characterCount: number;
  textStatus: "READ" | "LOW_DENSITY" | "NO_TEXT";
  table: PdfTableAnalysis;
  visual: PdfVisualAnalysis;
  previewDataUrl?: string;
};

export type PdfTextResult = {
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  text: string;
  pageCount: number;
  characterCount: number;
  truncated: boolean;
  pages: PdfPageAnalysis[];
  contextPages: number[];
};

export type CorpusDocument = Pick<PdfTextResult, "fileName" | "pageCount" | "pages">;

export function redactSensitiveText(value: string): string {
  return value
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[敏感信息已脱敏]")
    .replace(/(?<!\d)1[3-9]\d{9}(?!\d)/g, "[敏感信息已脱敏]")
    .replace(/(?<![A-Z0-9])[0-9A-Z]{18}(?![A-Z0-9])/gi, "[敏感信息已脱敏]")
    .replace(/(?<!\d)\d{17}[\dXx](?!\d)/g, "[敏感信息已脱敏]");
}

export function fitModelContent(content: string, limit = MODEL_CONTENT_LIMIT): { text: string; truncated: boolean } {
  const normalized = content.replace(/\u0000/g, "").replace(/[ \t]+\n/g, "\n").trim();
  if (normalized.length <= limit) return { text: normalized, truncated: false };
  const tailSize = Math.min(8_000, Math.floor(limit / 3));
  const headSize = limit - tailSize - 32;
  return {
    text: `${normalized.slice(0, headSize)}\n\n[中间内容已省略]\n\n${normalized.slice(-tailSize)}`,
    truncated: true,
  };
}

function normalizedCell(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function hasStructuredVisualCue(text: string): boolean {
  const normalized = text.replace(/\s+/g, " ");
  return /(?:图\s*[一二三四五六七八九十\d]+\s*[:：]|架构图|界面图|产品界面|页面截图|调研维度\s+核心发现|表\s*[一二三四五六七八九十\d]+\s*[:：])/i.test(normalized);
}

export function detectTableFromRows(rows: string[][]): PdfTableAnalysis {
  const normalized = rows
    .map(row => row.map(normalizedCell).filter(Boolean))
    .filter(row => row.length >= 2);
  const wideRows = normalized.filter(row => row.length >= 3);
  const twoColumnRows = normalized.filter(row => row.length === 2);
  const tabular = wideRows.length >= 3 ? wideRows : twoColumnRows.length >= 5 ? twoColumnRows : [];
  const numericRows = tabular.filter(row => row.some(cell => /(?:\d[\d,.]*|\d+(?:\.\d+)?%)/.test(cell)));
  const requiredNumericRows = tabular[0]?.length === 2 ? 3 : 2;
  if (tabular.length < 3 || numericRows.length < requiredNumericRows) {
    return { status: "NOT_DETECTED", title: null, headers: [], rows: [], confidence: 0 };
  }
  const widths = tabular.map(row => row.length).sort((a, b) => a - b);
  const median = widths[Math.floor(widths.length / 2)] ?? 0;
  const consistent = tabular.filter(row => Math.abs(row.length - median) <= 1);
  const confidence = Math.min(0.96, 0.55 + Math.min(0.25, consistent.length * 0.03) + Math.min(0.16, numericRows.length * 0.02));
  const first = consistent[0] ?? tabular[0];
  const previous = normalized[normalized.indexOf(first) - 1];
  return {
    status: "DETECTED",
    title: previous?.length === 1 ? previous[0] : null,
    headers: first,
    rows: consistent.slice(1, 21),
    confidence: Number(confidence.toFixed(2)),
  };
}

function priorityPages(pages: PdfPageAnalysis[], pageCount: number): number[] {
  const anchors = [1, Math.max(1, Math.ceil(pageCount / 2)), Math.max(1, pageCount)];
  const scored = pages.map(page => ({
    page: page.pageNumber,
    score:
      (page.table.status === "DETECTED" ? 120 : 0)
      + (hasStructuredVisualCue(page.text) ? 100 : 0)
      + (page.textStatus === "NO_TEXT" ? 110 : page.textStatus === "LOW_DENSITY" ? 90 : 0)
      + Math.min(80, page.visual.imageCount * 20)
      + (anchors.includes(page.pageNumber) ? 70 : 0),
  }));
  return [...new Set([
    ...anchors,
    ...scored.sort((a, b) => b.score - a.score || a.page - b.page).map(item => item.page),
  ])];
}

export function buildModelCorpus(
  rawContent: string,
  documents: CorpusDocument[],
  limit = MODEL_CONTENT_LIMIT,
): { text: string; truncated: boolean; coverage: Record<string, number[]> } {
  const raw = rawContent.replace(/\u0000/g, "").trim();
  const chunks: { fileName: string; pageNumber: number; content: string }[] = [];
  const coverage: Record<string, number[]> = {};
  const orderedByDocument = documents.map(document => {
    const byPage = new Map(document.pages.map(page => [page.pageNumber, page]));
    return priorityPages(document.pages, document.pageCount)
      .map(pageNumber => byPage.get(pageNumber))
      .filter((page): page is PdfPageAnalysis => Boolean(page));
  });
  const maximumPages = Math.max(0, ...orderedByDocument.map(pages => pages.length));
  for (let index = 0; index < maximumPages; index += 1) {
    for (let documentIndex = 0; documentIndex < documents.length; documentIndex += 1) {
      const page = orderedByDocument[documentIndex]?.[index];
      if (!page) continue;
      const document = documents[documentIndex];
      const table = page.table.status === "DETECTED" || page.table.status === "UNDERSTOOD"
        ? `\n表格: ${[page.table.title, page.table.headers.join(" | "), ...page.table.rows.slice(0, 8).map(row => row.join(" | "))].filter(Boolean).join("\n")}`
        : "";
      const visual = page.visual.summary ? `\n视觉识别: ${page.visual.summary}` : "";
      const body = redactSensitiveText(`${page.text}${table}${visual}`.trim());
      if (!body) continue;
      chunks.push({ fileName: document.fileName, pageNumber: page.pageNumber, content: body.slice(0, PAGE_CONTEXT_LIMIT) });
    }
  }
  const prefix = raw ? `【用户产品描述】\n${raw.slice(0, Math.min(8_000, Math.floor(limit / 3)))}\n\n` : "";
  let text = prefix;
  let truncated = raw.length > 8_000;
  for (const chunk of chunks) {
    const marker = `【${chunk.fileName} / 第 ${chunk.pageNumber} 页】\n`;
    const available = limit - text.length - marker.length - 2;
    if (available <= 80) {
      truncated = true;
      break;
    }
    const content = chunk.content.slice(0, available);
    text += `${marker}${content}\n\n`;
    coverage[chunk.fileName] = [...(coverage[chunk.fileName] ?? []), chunk.pageNumber];
    if (content.length < chunk.content.length) {
      truncated = true;
      break;
    }
  }
  return { text: text.trim(), truncated, coverage };
}

type TextItem = { str: string; transform: number[]; width?: number; height?: number };

function rowsFromItems(items: TextItem[]): { text: string; cells: string[] }[] {
  const rows = new Map<number, TextItem[]>();
  for (const item of items) {
    const y = Math.round((item.transform[5] ?? 0) / 3) * 3;
    rows.set(y, [...(rows.get(y) ?? []), item]);
  }
  return [...rows.entries()]
    .sort(([left], [right]) => right - left)
    .map(([, row]) => {
      const sorted = row.sort((left, right) => (left.transform[4] ?? 0) - (right.transform[4] ?? 0));
      const cells: string[] = [];
      let previousEnd: number | null = null;
      for (const item of sorted) {
        const x = item.transform[4] ?? 0;
        const width = item.width ?? Math.max(8, item.str.length * 5);
        if (previousEnd === null || x - previousEnd > 18) cells.push(item.str);
        else cells[cells.length - 1] = `${cells.at(-1) ?? ""} ${item.str}`;
        previousEnd = x + width;
      }
      return { text: sorted.map(item => item.str).join(" ").trim(), cells };
    })
    .filter(row => row.text);
}

async function mapLimit<T, R>(items: T[], limit: number, task: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  async function worker() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await task(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

export async function extractPdfText(
  file: File,
  onProgress?: (completedPages: number, totalPages: number) => void,
): Promise<PdfTextResult> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
  const loadingTask = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) });
  const document = await loadingTask.promise;
  const pageCount = document.numPages;
  let completed = 0;
  try {
    const pages = await mapLimit<number, PdfPageAnalysis>(
      Array.from({ length: pageCount }, (_, index) => index + 1),
      2,
      async pageNumber => {
      const page = await document.getPage(pageNumber);
      const content = await page.getTextContent();
      const items = content.items
        .filter((item): item is typeof item & TextItem => "str" in item && Boolean(item.str.trim()))
        .map(item => ({ str: item.str, transform: [...item.transform], width: item.width, height: item.height }));
      const rows = rowsFromItems(items);
      const text = rows.map(row => row.text).join("\n").trim();
      const operatorList = await page.getOperatorList();
      const imageOps = new Set([
        pdfjs.OPS.paintImageXObject,
        pdfjs.OPS.paintInlineImageXObject,
        pdfjs.OPS.paintImageMaskXObject,
      ]);
      const imageCount = operatorList.fnArray.filter(operation => imageOps.has(operation)).length;
      const table = detectTableFromRows(rows.map(row => row.cells));
      const textStatus = text.length === 0 ? "NO_TEXT" : text.length < 120 ? "LOW_DENSITY" : "READ";
      const structuredVisualCue = hasStructuredVisualCue(text);
      completed += 1;
      onProgress?.(completed, pageCount);
      return {
        pageNumber,
        text,
        characterCount: text.length,
        textStatus,
        table,
        visual: {
          status: table.status === "DETECTED"
            ? "UNDERSTOOD"
            : imageCount > 0 || textStatus !== "READ" || structuredVisualCue ? "NOT_INSPECTED" : "NOT_DETECTED",
          recognitionType: table.status === "DETECTED"
            ? "TABLE"
            : /(?:架构图|图\s*[一二三四五六七八九十\d]+\s*[:：])/i.test(text)
              ? "DIAGRAM"
              : /(?:调研维度\s+核心发现|表\s*[一二三四五六七八九十\d]+\s*[:：])/i.test(text)
                ? "TABLE"
                : imageCount > 0 ? "IMAGE" : "TEXT",
          imageCount,
          rotationDegrees: 0,
          summary: table.status === "DETECTED"
            ? `Local positional parser preserved ${table.headers.length} headers and ${table.rows.length} representative rows.`
            : null,
          confidence: table.status === "DETECTED" ? table.confidence : imageCount > 0 ? null : 1,
          source: table.status === "DETECTED" ? "LOCAL_POSITIONAL" : "LOCAL_PDFJS",
        },
      };
    });
    const renderCandidates = priorityPages(pages, pageCount)
      .filter(pageNumber => {
        const page = pages[pageNumber - 1];
        return page && (
          page.visual.imageCount > 0
          || page.textStatus !== "READ"
          || page.table.status === "DETECTED"
          || hasStructuredVisualCue(page.text)
        );
      })
      .slice(0, MAX_RENDERED_VISUAL_PAGES);
    for (const pageNumber of renderCandidates) {
      const page = await document.getPage(pageNumber);
      const viewport = page.getViewport({ scale: 1.15 });
      const canvas = window.document.createElement("canvas");
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      const context = canvas.getContext("2d", { alpha: false });
      if (!context) continue;
      await page.render({ canvas, canvasContext: context, viewport }).promise;
      pages[pageNumber - 1].previewDataUrl = canvas.toDataURL("image/jpeg", 0.72);
      if (pages[pageNumber - 1].table.status !== "DETECTED") {
        pages[pageNumber - 1].visual.status = "AWAITING_VISION";
      }
    }
    const completeText = pages
      .filter(page => page.text)
      .map(page => `[${file.name} / 第 ${page.pageNumber} 页]\n${page.text}`)
      .join("\n\n");
    if (!completeText.trim() && !pages.some(page => page.previewDataUrl)) {
      throw new Error("这份 PDF 没有可读取文字或可渲染页面；请检查文件是否损坏或受密码保护。");
    }
    const corpus = buildModelCorpus("", [{ fileName: file.name, pageCount, pages }]);
    return {
      fileName: file.name,
      mimeType: file.type || "application/pdf",
      sizeBytes: file.size,
      text: corpus.text,
      pageCount,
      characterCount: completeText.length,
      truncated: corpus.truncated,
      pages,
      contextPages: corpus.coverage[file.name] ?? [],
    };
  } finally {
    await loadingTask.destroy();
  }
}

export function persistentPdfAnalysis(result: PdfTextResult, source: { objectKey: string; sha256: string }) {
  return {
    schema_version: "1.0",
    analysis_type: "LAUNCHSCOPE_PDF_MIXED_ANALYSIS",
    source: {
      file_name: result.fileName,
      mime_type: result.mimeType,
      size_bytes: result.sizeBytes,
      object_key: source.objectKey,
      sha256: source.sha256,
    },
    page_count: result.pageCount,
    character_count: result.characterCount,
    context_pages: result.contextPages,
    pages: result.pages.map(({ previewDataUrl: _preview, ...page }) => page),
    model_context: result.text,
  };
}
