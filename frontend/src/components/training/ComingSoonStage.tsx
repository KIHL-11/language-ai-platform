import { TRAINING_STAGE_LABELS } from '../../training/trainingMachine'
import type { TrainingStage } from '../../types/lesson'

interface ComingSoonStageProps {
  stage: TrainingStage
  isLastStage: boolean
  onContinue: () => void
}

export default function ComingSoonStage({
  stage,
  isLastStage,
  onContinue,
}: ComingSoonStageProps) {
  const label = TRAINING_STAGE_LABELS[stage]

  return (
    <section className="border-y border-[#c9d5d0] bg-white px-5 py-10 sm:px-8" aria-labelledby="coming-soon-title">
      <h2 id="coming-soon-title" className="text-2xl font-semibold text-[#18211f]">
        {label}
      </h2>
      <p className="mt-3 max-w-2xl leading-7 text-[#596560]">
        {label} interaction will be implemented in the next milestone.
      </p>
      <button
        type="button"
        onClick={onContinue}
        className="mt-7 h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
      >
        {isLastStage ? 'Complete session' : 'Continue to next stage'}
      </button>
    </section>
  )
}
