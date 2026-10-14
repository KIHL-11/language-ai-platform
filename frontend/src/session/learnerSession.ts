export const LEARNER_ID_STORAGE_KEY = 'language_ai_learner_id_v1'

const UUID_V4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

interface LearnerStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

interface LearnerCrypto {
  randomUUID?: () => string
  getRandomValues?: (bytes: Uint8Array<ArrayBuffer>) => void
}

interface LearnerSessionDependencies {
  storage?: LearnerStorage
  crypto?: LearnerCrypto
}

function isLearnerId(value: string | null): value is string {
  return value !== null && UUID_V4_PATTERN.test(value)
}

function bytesToUuid(bytes: Uint8Array): string {
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, '0'),
  )
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10, 16).join(''),
  ].join('-')
}

function createLearnerId(source: LearnerCrypto): string {
  if (source.randomUUID) {
    const value = source.randomUUID().toLowerCase()
    if (isLearnerId(value)) {
      return value
    }
  }
  if (source.getRandomValues) {
    const bytes = new Uint8Array(new ArrayBuffer(16))
    source.getRandomValues(bytes)
    return bytesToUuid(bytes)
  }
  throw new Error('Secure local learner identity generation is unavailable.')
}

export function getOrCreateLearnerId(
  dependencies: LearnerSessionDependencies = {},
): string {
  const storage = dependencies.storage ?? window.localStorage
  const source: LearnerCrypto = dependencies.crypto ?? {
    randomUUID: globalThis.crypto.randomUUID?.bind(globalThis.crypto),
    getRandomValues: (bytes) => globalThis.crypto.getRandomValues(bytes),
  }
  let stored: string | null = null
  try {
    stored = storage.getItem(LEARNER_ID_STORAGE_KEY)
  } catch {
    // Storage can be unavailable in restricted browser modes. The in-memory
    // caller state still keeps this ID stable for the current app lifecycle.
  }
  if (isLearnerId(stored)) {
    return stored
  }

  const learnerId = createLearnerId(source)
  try {
    storage.setItem(LEARNER_ID_STORAGE_KEY, learnerId)
  } catch {
    // This identifier is a persistence key, not an authentication credential.
  }
  return learnerId
}
