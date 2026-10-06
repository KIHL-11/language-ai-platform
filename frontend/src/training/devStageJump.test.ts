import { describe, expect, it } from 'vitest'

import { getDevStageOverride } from './devStageJump'

describe('development stage query parsing', () => {
  it('does not expose an override when development mode is disabled', () => {
    expect(getDevStageOverride('?stage=shadowing', false)).toBeNull()
  })

  it('rejects unknown stage names without throwing', () => {
    expect(getDevStageOverride('?stage=invalid', true)).toBeNull()
  })
})
