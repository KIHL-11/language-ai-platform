import type { TrainingStage } from '../types/lesson'

export const DEV_TRAINING_STAGES: readonly TrainingStage[] = [
  'active_recall',
  'blind_listening',
  'comprehension',
  'mini_dictation',
  'shadowing',
  'retell',
  'live_dialogue',
]

const DEV_TRAINING_STAGE_SET = new Set<TrainingStage>(DEV_TRAINING_STAGES)

export function getDevStageOverride(
  search: string,
  isDevelopment: boolean,
): TrainingStage | null {
  if (!isDevelopment) {
    return null
  }

  const stage = new URLSearchParams(search).get('stage') as TrainingStage | null
  return stage && DEV_TRAINING_STAGE_SET.has(stage) ? stage : null
}

export function updateDevStageQuery(stage: TrainingStage | null): void {
  const url = new URL(window.location.href)

  if (stage) {
    url.searchParams.set('stage', stage)
  } else {
    url.searchParams.delete('stage')
  }

  window.history.replaceState(
    window.history.state,
    '',
    `${url.pathname}${url.search}${url.hash}`,
  )
}
