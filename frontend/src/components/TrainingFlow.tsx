import type { TrainingStage } from '../types/lesson'
import { TRAINING_STAGE_LABELS } from '../training/trainingMachine'

interface TrainingFlowProps {
  stages: TrainingStage[]
  currentStage?: TrainingStage
  completedStages?: TrainingStage[]
}

export default function TrainingFlow({
  stages,
  currentStage,
  completedStages = [],
}: TrainingFlowProps) {
  return (
    <ol className="grid gap-px overflow-hidden border border-[#c9d5d0] bg-[#c9d5d0] sm:grid-cols-2 lg:grid-cols-7">
      {stages.map((stage, index) => (
        <li
          key={stage}
          aria-current={currentStage === stage ? 'step' : undefined}
          className={`flex min-h-20 items-center gap-3 px-4 py-3 ${
            currentStage === stage ? 'bg-[#e2eee9]' : 'bg-white'
          }`}
        >
          <span className={`flex h-7 w-7 shrink-0 items-center justify-center text-sm font-semibold ${
            completedStages.includes(stage)
              ? 'bg-[#176b5b] text-white'
              : 'bg-[#e2eee9] text-[#176b5b]'
          }`}>
            {completedStages.includes(stage) ? '✓' : index + 1}
          </span>
          <span className="text-sm font-medium leading-5 text-[#293430]">
            {TRAINING_STAGE_LABELS[stage]}
          </span>
        </li>
      ))}
    </ol>
  )
}
