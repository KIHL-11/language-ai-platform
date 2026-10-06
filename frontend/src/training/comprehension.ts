import type { LessonSentence } from '../types/lesson'

export interface ComprehensionQuestion {
  question: string
  options: string[]
  answerIndex: number
}

const FALLBACK_DISTRACTORS = [
  'It describes an unrelated place or journey.',
  'It gives instructions for a numerical task.',
  'It discusses a different person and situation.',
]

function addUniqueOption(options: string[], value: string | undefined) {
  const cleaned = value?.trim()
  if (
    cleaned &&
    !options.some((option) => option.toLowerCase() === cleaned.toLowerCase())
  ) {
    options.push(cleaned)
  }
}

function collectDistractors(
  sentence: LessonSentence,
  lessonSentences: LessonSentence[],
  correctAnswer: string,
): string[] {
  const candidates: string[] = []

  sentence.chunks.slice(1).forEach((chunk) => addUniqueOption(candidates, chunk.meaning))
  sentence.keywords.slice(1).forEach((keyword) => addUniqueOption(candidates, keyword.meaning))

  lessonSentences
    .filter((candidate) => candidate.id !== sentence.id)
    .forEach((candidate) => {
      addUniqueOption(candidates, candidate.chunks[0]?.meaning)
      addUniqueOption(candidates, candidate.keywords[0]?.meaning)
      addUniqueOption(candidates, candidate.translation)
    })

  FALLBACK_DISTRACTORS.forEach((fallback) => addUniqueOption(candidates, fallback))

  return candidates
    .filter(
      (candidate) => candidate.toLowerCase() !== correctAnswer.toLowerCase(),
    )
    .slice(0, 2)
}

export function generateComprehensionQuestion(
  sentence: LessonSentence,
  lessonSentences: LessonSentence[] = [sentence],
): ComprehensionQuestion {
  const primaryChunk = sentence.chunks.find(
    (chunk) => chunk.text.trim() && chunk.meaning.trim(),
  )
  const primaryKeyword = sentence.keywords.find(
    (keyword) => keyword.word.trim() && keyword.meaning.trim(),
  )

  let question = 'Which meaning best matches the sentence you hear?'
  let correctAnswer = sentence.translation.trim() || sentence.text.trim()

  if (primaryChunk) {
    question = `What does “${primaryChunk.text}” mean in this sentence?`
    correctAnswer = primaryChunk.meaning.trim()
  } else if (primaryKeyword) {
    question = `What does “${primaryKeyword.word}” mean in this sentence?`
    correctAnswer = primaryKeyword.meaning.trim()
  }

  const wrongOptions = collectDistractors(
    sentence,
    lessonSentences,
    correctAnswer,
  )
  const optionCount = wrongOptions.length + 1
  const answerIndex = Math.abs(sentence.id) % optionCount
  const options = [...wrongOptions]
  options.splice(answerIndex, 0, correctAnswer)

  return { question, options, answerIndex }
}
