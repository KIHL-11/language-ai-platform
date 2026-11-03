import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EmailAddresses } from './EmailAddresses'

const CHALLENGE = '0b8f2a4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b'

vi.mock('./client', async (original) => {
  const actual = await original<typeof import('./client')>()
  return { ...actual, startEmailClaim: vi.fn(), verifyEmailClaim: vi.fn() }
})
import { CloudAuthError, startEmailClaim, verifyEmailClaim } from './client'

afterEach(() => { vi.clearAllMocks() })

describe('Email addresses', () => {
  it('adds a second address only after confirmation and a code from the new mailbox', async () => {
    vi.mocked(startEmailClaim).mockResolvedValue({ challengeId: CHALLENGE, expiresIn: 600, resendAfter: 60 })
    vi.mocked(verifyEmailClaim).mockResolvedValue({ result: 'added', notice: 'sent' })
    const changed = vi.fn(() => Promise.resolve())
    const user = userEvent.setup()
    render(<EmailAddresses emails={['learner@example.test']} fresh onChanged={changed} />)
    await user.click(screen.getByRole('button', { name: 'Add an email address' }))
    await user.type(screen.getByLabelText('Additional email address'), 'second@example.test')
    expect(screen.getByRole('button', { name: 'Send code to new address' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Send code to new address' }))
    expect(startEmailClaim).toHaveBeenCalledWith('second@example.test', null)
    await user.type(await screen.findByLabelText('Code from the new address'), '1234-56')
    await user.click(screen.getByRole('button', { name: 'Confirm new address' }))
    expect(verifyEmailClaim).toHaveBeenCalledWith(CHALLENGE, '123456')
    expect(await screen.findByRole('status')).toHaveTextContent('second@example.test can now sign in')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(changed).toHaveBeenCalled()
  })

  it('warns plainly when the security notice could not be sent after a change', async () => {
    vi.mocked(startEmailClaim).mockResolvedValue({ challengeId: CHALLENGE, expiresIn: 600, resendAfter: 60 })
    vi.mocked(verifyEmailClaim).mockResolvedValue({ result: 'changed', notice: 'failed' })
    const user = userEvent.setup()
    render(<EmailAddresses emails={['old@example.test']} fresh onChanged={() => Promise.resolve()} />)
    expect(screen.queryByRole('button', { name: 'Add an email address' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Change old@example.test' }))
    await user.type(screen.getByLabelText('New address to replace old@example.test'), 'new@example.test')
    await user.click(screen.getByRole('checkbox', { name: /Replace old@example.test/ }))
    await user.click(screen.getByRole('button', { name: 'Send code to new address' }))
    expect(startEmailClaim).toHaveBeenCalledWith('new@example.test', 'old@example.test')
    await user.type(await screen.findByLabelText('Code from the new address'), '123456')
    await user.click(screen.getByRole('button', { name: 'Confirm new address' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('could not send a security notice to old@example.test')
  })

  it('needs a fresh proof, caps at two addresses and explains a taken address', async () => {
    vi.mocked(startEmailClaim).mockResolvedValue({ challengeId: CHALLENGE, expiresIn: 600, resendAfter: 60 })
    vi.mocked(verifyEmailClaim).mockRejectedValue(new CloudAuthError(409, 'Request conflicts with saved state'))
    const user = userEvent.setup()
    const { rerender } = render(<EmailAddresses emails={['a@example.test', 'b@example.test']} fresh={false} onChanged={() => Promise.resolve()} />)
    expect(screen.queryByRole('button', { name: 'Add an email address' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Change a@example.test' })).toBeDisabled()
    rerender(<EmailAddresses emails={['a@example.test', 'b@example.test']} fresh onChanged={() => Promise.resolve()} />)
    await user.click(screen.getByRole('button', { name: 'Change b@example.test' }))
    await user.type(screen.getByLabelText('New address to replace b@example.test'), 'taken@example.test')
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Send code to new address' }))
    await user.type(await screen.findByLabelText('Code from the new address'), '123456')
    await user.click(screen.getByRole('button', { name: 'Confirm new address' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('already belongs to another'))
  })
})

