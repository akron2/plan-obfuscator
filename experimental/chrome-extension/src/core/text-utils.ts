export interface LineWithOffset {
  offset: number;
  text: string;
}

export function linesWithOffsets(source: string): LineWithOffset[] {
  const result: LineWithOffset[] = [];
  const pattern = /.*(?:\r\n|\n|\r|$)/g;
  for (const match of source.matchAll(pattern)) {
    if (!match[0]) continue;
    result.push({ offset: match.index, text: match[0] });
  }
  return result;
}

export function trimmedBounds(
  source: string,
  start: number,
  end: number
): [number, number] | null {
  let left = start;
  let right = end;
  while (left < right && /\s/.test(source[left] ?? "")) left += 1;
  while (right > left && /\s/.test(source[right - 1] ?? "")) right -= 1;
  return left < right ? [left, right] : null;
}

export function cellBounds(line: string): Array<[number, number]> {
  const pipes: number[] = [];
  for (let index = 0; index < line.length; index += 1) {
    if (line[index] === "|") pipes.push(index);
  }
  const cells: Array<[number, number]> = [];
  for (let index = 0; index + 1 < pipes.length; index += 1) {
    cells.push([pipes[index]! + 1, pipes[index + 1]!]);
  }
  return cells;
}

export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}
