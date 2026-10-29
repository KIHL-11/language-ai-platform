import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSpeakingCurriculum from './TopicSpeakingCurriculum'


const topic = {
  topic_id: 'en-friends-reunion', version: 1, language: 'en', level: 'B1',
  label: 'Meeting an old friend', scenario: 'Meet an old friend.',
  goal: 'Exchange news.', learner_role: 'Old friend', partner_role: 'Returning friend',
  success_criteria: [{ id: 'recent-news', label: 'Share recent news', evidence_terms: ['recently'] }],
  guided_prompts: ['What has changed?', 'When should we meet again?'],
  support_expressions: ['Let’s catch up'], state: 'published',
}
const target = { study_item_id: 'study-1', text: "let's catch up", meaning: '叙旧' }
const preview = {
  topic, target_chunks: [target],
  lifecycle: ['setup', 'preview', 'connecting', 'live', 'ended', 'feedback', 'saved'],
  evidence_notice: 'Only final learner turns count. This is not a fluency assessment.',
}
const started = {
  session_id: 'session-1', topic_id: topic.topic_id, topic_version: 1, phase: 'live',
  event_count: 0, learner_turn_count: 0,
  target_usage: [{ ...target, use_count: 0, first_used_turn: null, last_used_turn: null, evidence: [] }],
  prompt: 'What has changed?',
}
const turned = {
  ...started, event_count: 2, learner_turn_count: 1,
  target_usage: [{ ...target, use_count: 1, first_used_turn: 1, last_used_turn: 1, evidence: ["Let's catch up next time."] }],
  prompt: 'When should we meet again?',
}
const feedback = {
  topic_id: topic.topic_id, topic_version: 1, completion: 'completed', learner_turn_count: 1,
  checkpoints: [{ checkpoint_id: 'recent-news', label: 'Share recent news', observed: true, evidence: "Let's catch up next time." }],
  target_usage: turned.target_usage, corrections: [],
  limitations: ['Expression matches do not measure pronunciation, fluency, or CEFR level.', 'No Study Bank or Daily Review state was changed.'],
}
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })


describe('Topic speaking curriculum', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    fetchMock.mockReset()
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      const value = String(url)
      if (value.includes('/api/topics?')) return Promise.resolve(response({ catalog_version: 1, topics: [topic] }))
      if (value.endsWith('/preview')) return Promise.resolve(response(preview))
      if (value.endsWith('/attempts')) return Promise.resolve(response(started))
      if (value.endsWith('/turns')) return Promise.resolve(response({ state: turned, agent_reply: 'When should we meet again?' }))
      if (value.endsWith('/finish')) return Promise.resolve(response({ state: { ...turned, phase: 'saved' }, feedback: { ...feedback, completion: JSON.parse(String(options?.body)).interrupted ? 'interrupted' : 'completed' } }))
      return Promise.resolve(response({}))
    })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('loads an accessible language/level catalog and supports filters', async () => {
    const user = userEvent.setup()
    render(<TopicSpeakingCurriculum learnerId="learner" onBack={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: 'Meeting an old friend' })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Topic language'), 'de')
    await user.selectOptions(screen.getByLabelText('Topic level'), 'C1')
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/topics?language=de&level=C1')))
    expect(screen.getByLabelText('Available topics')).toBeInTheDocument()
  })

  it('previews role, checkpoints and owner targets without starting a microphone or provider', async () => {
    const user = userEvent.setup()
    const mediaDevices = { getUserMedia: vi.fn() }
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: mediaDevices })
    render(<TopicSpeakingCurriculum learnerId="learner" onBack={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Preview Meeting an old friend' }))
    expect(await screen.findByText('Exchange news.')).toBeInTheDocument()
    expect(screen.getByText("let's catch up")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start mock topic practice' })).toBeDisabled()
    expect(mediaDevices.getUserMedia).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([url]) => /client-token|realtime/.test(String(url)))).toBe(false)
  })

  it('requires explicit consent and start, then runs a deterministic finalized-turn flow', async () => {
    const user = userEvent.setup()
    render(<TopicSpeakingCurriculum learnerId="learner" onBack={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Preview Meeting an old friend' }))
    await user.click(screen.getByRole('checkbox', { name: /ready to start/i }))
    await user.click(screen.getByRole('button', { name: 'Start mock topic practice' }))
    expect(await screen.findByRole('status')).toHaveTextContent('no microphone or provider credential')
    await user.type(screen.getByLabelText('Your response'), "Let's catch up next time.")
    await user.click(screen.getByRole('button', { name: 'Send finalized turn' }))
    expect(await screen.findByText("Let's catch up next time.")).toBeInTheDocument()
    expect(screen.getByText('When should we meet again?')).toBeInTheDocument()
    const body = JSON.parse(String(fetchMock.mock.calls.find(([url]) => String(url).endsWith('/turns'))?.[1]?.body))
    expect(body).toEqual({ user_id: 'learner', expected_event_count: 0, text: "Let's catch up next time." })
  })

  it('shows evidence-grounded feedback without a fluency or CEFR score', async () => {
    const user = userEvent.setup()
    render(<TopicSpeakingCurriculum learnerId="learner" onBack={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Preview Meeting an old friend' }))
    await user.click(screen.getByRole('checkbox', { name: /ready to start/i })); await user.click(screen.getByRole('button', { name: 'Start mock topic practice' }))
    await user.type(screen.getByLabelText('Your response'), "Let's catch up next time."); await user.click(screen.getByRole('button', { name: 'Send finalized turn' }))
    await user.click(screen.getByRole('button', { name: 'Finish practice' }))
    expect(await screen.findByRole('heading', { name: 'Topic practice feedback' })).toBeInTheDocument()
    expect(screen.getByText(/Observed:/)).toBeInTheDocument()
    expect(screen.getAllByText(/Let's catch up next time/).length).toBeGreaterThan(0)
    expect(screen.getByText(/do not measure pronunciation, fluency, or CEFR/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/fluency score|CEFR score/i)
  })

  it('records interruption honestly', async () => {
    const user = userEvent.setup()
    render(<TopicSpeakingCurriculum learnerId="learner" onBack={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Preview Meeting an old friend' }))
    await user.click(screen.getByRole('checkbox', { name: /ready to start/i })); await user.click(screen.getByRole('button', { name: 'Start mock topic practice' }))
    await user.click(screen.getByRole('button', { name: 'Interrupt practice' }))
    expect(await screen.findByText(/Interrupted · 1 learner turns/)).toBeInTheDocument()
    const finishBody = JSON.parse(String(fetchMock.mock.calls.find(([url]) => String(url).endsWith('/finish'))?.[1]?.body))
    expect(finishBody.interrupted).toBe(true)
  })

  it('explains the owner-scoped cloud Speaking storage gate', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/api/topics?')) return Promise.resolve(response({ catalog_version: 1, topics: [topic] }))
      if (String(url).endsWith('/preview')) return Promise.resolve(response(preview))
      if (String(url).endsWith('/attempts')) return Promise.resolve(response({}, 503))
      return Promise.resolve(response({}))
    })
    const user = userEvent.setup(); render(<TopicSpeakingCurriculum learnerId="learner" onBack={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Preview Meeting an old friend' }))
    await user.click(screen.getByRole('checkbox', { name: /ready to start/i })); await user.click(screen.getByRole('button', { name: 'Start mock topic practice' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('owner-scoped Speaking storage')
  })
})
