const NUMERALS = "零〇一二三四五六七八九十百千万两0-9";
function numeral(value: string): number | null {
  if (/^\d+$/.test(value)) return Number(value);
  const digits: Record<string, number> = { 零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  const units: Record<string, number> = { 十: 10, 百: 100, 千: 1000, 万: 10000 };
  if ([...value].every((char) => char in digits)) return Number([...value].map((char) => digits[char]).join(""));
  let total = 0, section = 0, digit = 0;
  for (const char of value) {
    if (char in digits) { digit = digits[char]; continue; }
    const unit = units[char];
    if (!unit) return null;
    if (unit === 10000) { total += (section + digit || 1) * unit; section = 0; }
    else section += (digit || 1) * unit;
    digit = 0;
  }
  return total + section + digit;
}

export function clauseOrdinal(clause: string | undefined): [number, number] | null {
  const match = clause?.normalize("NFKC").match(new RegExp(`^第([${NUMERALS}]+)条(?:之([${NUMERALS}]+))?$`));
  if (!match) return null;
  const main = numeral(match[1]);
  const suffix = match[2] ? numeral(match[2]) : 0;
  return main === null || suffix === null ? null : [main, suffix];
}
