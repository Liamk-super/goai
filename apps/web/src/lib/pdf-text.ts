const MODEL_CONTENT_LIMIT = 30_000;

export type PdfTextResult = {
  text: string;
  pageCount: number;
  characterCount: number;
  truncated: boolean;
};

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

export async function extractPdfText(file: File): Promise<PdfTextResult> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url,
  ).toString();
  const loadingTask = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) });
  const document = await loadingTask.promise;
  const pageCount = document.numPages;
  const pages: string[] = [];
  try {
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      const page = await document.getPage(pageNumber);
      const content = await page.getTextContent();
      const line = content.items
        .map(item => ("str" in item ? item.str : ""))
        .filter(Boolean)
        .join(" ");
      if (line.trim()) pages.push(`[第 ${pageNumber} 页]\n${line.trim()}`);
    }
  } finally {
    await loadingTask.destroy();
  }
  const completeText = pages.join("\n\n");
  if (!completeText.trim()) throw new Error("这份 PDF 没有可读取文字，可能是扫描件；请粘贴文字或提供可搜索版本。");
  const fitted = fitModelContent(completeText);
  return { text: fitted.text, pageCount, characterCount: completeText.length, truncated: fitted.truncated };
}
