export function computeImportanceScore(content: string): number {
  // Simple heuristic: length-based bounded score 0-1.
  const len = Math.min(content.length, 2000);
  return Math.round((len / 2000) * 100) / 100;
}
