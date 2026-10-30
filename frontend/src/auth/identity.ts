import { cloudAuth } from './client'
import { getOrCreateLearnerId } from '../session/learnerSession'

export function getSessionLearnerId(): string {
  const state = cloudAuth.getSnapshot()
  if (state.status === 'personal') return getOrCreateLearnerId()
  if (state.status === 'authenticated') return state.userId
  throw new Error('No authenticated learner identity')
}
