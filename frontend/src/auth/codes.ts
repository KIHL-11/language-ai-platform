// Six digits, or the 12-character code sent once an address saw many failed attempts.
export function normalizeCode(value: string): string {
  return value.toUpperCase().replace(/[^0-9A-Z-]/g, '').slice(0, 14)
}
export function codeComplete(value: string): boolean {
  const compact = value.replace(/-/g, '')
  return /^[0-9]{6}$/.test(compact) || /^[A-Z0-9]{12}$/.test(compact)
}
