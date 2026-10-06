export type TargetWordStatus = 'correct' | 'missing'
export type AnswerWordStatus = 'correct' | 'extra'

export interface ComparedWord<TStatus extends string> {
  value: string
  normalized: string
  status: TStatus
}

export interface DictationResult {
  accuracy: number
  correctWords: string[]
  missingIncorrectWords: string[]
  extraWords: string[]
  targetWords: ComparedWord<TargetWordStatus>[]
  answerWords: ComparedWord<AnswerWordStatus>[]
}

interface Token {
  value: string
  normalized: string
}

export function normalizeDictationText(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[.,!?;:]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function tokenize(value: string): Token[] {
  return value
    .trim()
    .split(/\s+/)
    .map((token) => ({
      value: token,
      normalized: normalizeDictationText(token),
    }))
    .filter((token) => token.normalized.length > 0)
}

function findMatchingPairs(target: Token[], answer: Token[]): Array<[number, number]> {
  const table = Array.from({ length: target.length + 1 }, () =>
    Array<number>(answer.length + 1).fill(0),
  )

  for (let targetIndex = target.length - 1; targetIndex >= 0; targetIndex -= 1) {
    for (let answerIndex = answer.length - 1; answerIndex >= 0; answerIndex -= 1) {
      table[targetIndex][answerIndex] =
        target[targetIndex].normalized === answer[answerIndex].normalized
          ? table[targetIndex + 1][answerIndex + 1] + 1
          : Math.max(
              table[targetIndex + 1][answerIndex],
              table[targetIndex][answerIndex + 1],
            )
    }
  }

  const pairs: Array<[number, number]> = []
  let targetIndex = 0
  let answerIndex = 0
  while (targetIndex < target.length && answerIndex < answer.length) {
    if (target[targetIndex].normalized === answer[answerIndex].normalized) {
      pairs.push([targetIndex, answerIndex])
      targetIndex += 1
      answerIndex += 1
    } else if (
      table[targetIndex + 1][answerIndex] >=
      table[targetIndex][answerIndex + 1]
    ) {
      targetIndex += 1
    } else {
      answerIndex += 1
    }
  }

  return pairs
}

export function compareDictation(
  targetText: string,
  answerText: string,
): DictationResult {
  const target = tokenize(targetText)
  const answer = tokenize(answerText)
  const matchingPairs = findMatchingPairs(target, answer)
  const matchedTargetIndexes = new Set(matchingPairs.map(([index]) => index))
  const matchedAnswerIndexes = new Set(
    matchingPairs.map(([, index]) => index),
  )

  const targetWords = target.map((token, index) => ({
    ...token,
    status: matchedTargetIndexes.has(index)
      ? ('correct' as const)
      : ('missing' as const),
  }))
  const answerWords = answer.map((token, index) => ({
    ...token,
    status: matchedAnswerIndexes.has(index)
      ? ('correct' as const)
      : ('extra' as const),
  }))
  const denominator = Math.max(target.length, answer.length)
  const accuracy =
    denominator === 0
      ? 100
      : Math.round((matchingPairs.length / denominator) * 100)

  return {
    accuracy,
    correctWords: targetWords
      .filter((word) => word.status === 'correct')
      .map((word) => word.value),
    missingIncorrectWords: targetWords
      .filter((word) => word.status === 'missing')
      .map((word) => word.value),
    extraWords: answerWords
      .filter((word) => word.status === 'extra')
      .map((word) => word.value),
    targetWords,
    answerWords,
  }
}

export function compareRecallAnswer(
  targetText: string,
  answerText: string,
): DictationResult {
  return compareDictation(targetText, answerText)
}
