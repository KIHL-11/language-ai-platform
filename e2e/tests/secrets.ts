// Registers a value with the GitHub Actions log masker before Playwright can echo it.
// Playwright's call log prints the argument of a failed fill(), and the list reporter
// forwards test stdout unprefixed, so a "::add-mask::" line written here reaches the
// runner and masks every later occurrence in the job log.
export function maskInActionsLog(value: string) {
  if (!value || /[\r\n]/.test(value)) throw new Error('Refusing to mask an empty or multi-line value')
  if (process.env.GITHUB_ACTIONS === 'true') process.stdout.write(`::add-mask::${value}\n`)
}
