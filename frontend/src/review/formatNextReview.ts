export function formatNextReview(dueAt: string, now = new Date()): string {
  const difference = new Date(dueAt).getTime() - now.getTime()
  if (difference <= 0) return 'Due now'
  const minutes = Math.ceil(difference / 60_000)
  if (minutes < 60) return `In ${minutes} min`
  if (minutes < 24 * 60) return `In ${Math.ceil(minutes / 60)} hours`
  const days = Math.ceil(difference / 86_400_000)
  if (days === 1) return 'Tomorrow'
  if (days <= 7) return `In ${days} days`
  return new Date(dueAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
